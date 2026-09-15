"""`lcms-profile.v1`の読込・依存/実行環境の解決・実行スナップショット（spec §5, §5.1, §6.1）。

このモジュールが担うのは4つ。

- `hash_files(paths)`: statだけでは検出できない内容変化を捕まえる、hash計算
  自体の前後stat検査つきSHA-256。method/依存/実行体/raw等、あらゆる固定
  ファイルのfingerprintの唯一の計算経路（spec §6.1「hash計算自体も進捗を
  記録する」——ここでは「計算中に変わっていないか」まで見る）。
- `load_profile(path, purpose)`: profileファイルを読み`profile_schema.validate_profile`
  へ通す。`purpose`（`"routine"` / `"validation"`）はspec §6の`execution_purpose`
  の先取りで、`routine`はvalidated profileを要求する。**外部ファイルパスは
  ここでは絶対化しない**——`processing.method_path`等を相対のまま返すことで、
  `profile_content_hash`（`validation`を除くprofile本体のcanonical hash）が
  「このprofileをどのマシンのどのディレクトリから読んだか」に依存しない
  安定した内容識別であり続ける（証明書の`profile_content_sha256`が環境非依存の
  監査証跡であるために必須）。
- `resolve_profile_inputs(profile, source_root)`: `source_root`（profileファイル
  自身の親ディレクトリ——spec §5「外部ファイルパスはプロファイルファイルの親を
  基準に解決し」）を基準に、method/依存/実行体を絶対解決し、実在・宣言hashとの
  一致・（`processing.dependencies[].kind`が）adapterのallowlistと一致するかを
  検証し、実行環境manifest（実行体+同梱DLL/設定+adapter版）を組み立てる。
  method原本の宣言（Ion mode / Target omics / Acquisition type）とprofileの
  宣言（polarity / omics / acquisition_type）が食い違えば`PROFILE_METHOD_CONFLICT`。
- `snapshot_profile(plan, run_dir)`: `resolve_profile_inputs`が返した計画を
  `run_dir`へ実体化する。method原本は書き換えず、実効コピー
  （`method_file.write_effective_method_file`が生成、依存の絶対pathで上書き）
  だけを`run_dir`配下に書く。**計画の同一性ハッシュ（`plan_identity_hash`）は
  `plan`自体のcanonical hashで、`run_dir`（＝出力先）に一切依存しない**——
  実行コピーの絶対pathを含む実行証跡ハッシュ（`effective_method_sha256`）は
  別のキーに分離して保存する（spec §6.1「実行時の絶対パス書換え後method hashは
  実行証跡として別途保存し、出力先が変わるだけで計画の同一性が変わらないように
  する」）。

依存ファイル自体（MSP/LBM/text DB/RT補正リスト）は`run_dir`へコピーしない
——既存lipidomics経路（`lipidmix/pipeline/inputs.py`のLBM解決）と同じく、
原本の絶対pathをmethodの実効コピーへ書き込むだけで足りる大きな参照ライブラリ
という前提を踏襲する。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from lipidmix.console import method_file as method_file_mod
from lipidmix.console import profile_adapter
from lipidmix.console.profile_schema import profile_content_hash, validate_profile
from lipidmix.core.atomic_io import DomainError, canonical_hash

__all__ = ["hash_files", "load_profile", "resolve_profile_inputs", "snapshot_profile"]

#: 実行環境manifestが実行体と一緒に列挙する同梱ファイルの拡張子（spec §5.1
#: 「実行環境manifestはexeのほか同梱DLL/設定...を列挙する」）。.NETアプリの
#: 実行に要る同梱物はDLLと設定ファイルが実質すべて（他は`.pdb`等のデバッグ情報で
#: 実行同一性には無関係）。
_EXECUTION_ENVIRONMENT_SUFFIXES = (".dll", ".config")

_SNAPSHOT_SUBDIR = "profile"
_EFFECTIVE_METHOD_NAME = "effective-method.txt"

_PURPOSES = frozenset({"routine", "validation"})

_ADAPTER_VERSION_MAJOR_RE = re.compile(r"^\s*(\d+)")


def hash_files(paths: list[Path]) -> dict[str, str]:
    """各pathの内容SHA-256を返す。`{str(path): sha256}`（`str(path)`をキーにする）。

    hash計算の直前と直後でstat（size・mtime_ns）を取り、食い違えば
    `DomainError("INPUT_CHANGED", ...)`——読んでいる最中にファイルが書き換え
    られた（TOCTOU）ケースを、統計だけでなく実際にhashを計算する経路自身で
    検出する（brief「二回のstatが異なるhash計算はINPUT_CHANGEDとして拒否
    する」）。単純に2回hashを比べて差分を見るだけの用途（例:
    `test_hash_detects_stat_preserving_change`）は、内容が変われば当然hashも
    変わるのでこのstat検査が無くても検出できる——ここでのstat検査は
    「1回の呼び出しの最中に変化していないか」という、それとは別の保証を足す。
    """
    out: dict[str, str] = {}
    for path in sorted(Path(p) for p in paths):
        before = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise DomainError(
                "INPUT_CHANGED",
                f"hash計算中にファイルが変化しました（読込中の書換え）: {path}",
                {"path": str(path)},
            )
        out[str(path)] = digest
    return out


def _hash_one(path: Path) -> str:
    return hash_files([path])[str(path)]


def load_profile(path: Path, purpose: str) -> dict:
    """profileファイルを読み、検証して返す（外部パスは絶対化しない）。

    `purpose`は`"routine"`（validated profile必須）か`"validation"`
    （draftでも実行可）のいずれか。それ以外は`DomainError("PROFILE_PURPOSE_INVALID")`。
    JSONとして読めない・ファイルが無い・`validate_profile`が拒否する場合は
    それぞれのDomainErrorをそのまま伝播する。
    """
    if purpose not in _PURPOSES:
        raise DomainError(
            "PROFILE_PURPOSE_INVALID",
            f"purposeは{sorted(_PURPOSES)}のいずれかである必要があります: {purpose!r}",
            {"purpose": purpose},
        )

    path = Path(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DomainError(
            "PROFILE_NOT_FOUND", f"profileファイルが読めません: {path}", {"path": str(path)},
        ) from exc
    try:
        raw = json.loads(raw_text)
    except ValueError as exc:
        raise DomainError(
            "PROFILE_INVALID",
            f"profileファイルがJSONとして読めません: {path}: {exc}",
            {"path": str(path)},
        ) from exc

    profile = validate_profile(raw)

    if purpose == "routine" and profile["validation"]["status"] != "validated":
        raise DomainError(
            "PROFILE_NOT_VALIDATED",
            f"execution_purpose='routine'にはvalidated profileが必要です（現状: "
            f"{profile['validation']['status']!r}）: {path}",
            {"path": str(path), "status": profile["validation"]["status"]},
        )

    return profile


def _resolve_declared_path(declared: str, base_dir: Path) -> Path:
    candidate = Path(declared)
    return candidate if candidate.is_absolute() else (base_dir / candidate)


def _derive_adapter_id(msdial_version: str) -> str:
    """`software.msdial_version`（例: `"5.x"` `"5.5.241113"`）からadapter_idを作る。

    先頭の整数（メジャーバージョン）を`msdial{major}`にする。パースできない
    文字列は、どのみち`profile_adapter`未登録として`PROFILE_ADAPTER_UNSUPPORTED`
    になる（ここで特別扱いしない）。
    """
    match = _ADAPTER_VERSION_MAJOR_RE.match(msdial_version)
    if not match:
        return f"msdial-unrecognized-version:{msdial_version}"
    return f"msdial{match.group(1)}"


def _verify_pinned_file(path: Path, expected_sha256: str, *, missing_code: str,
                        missing_message: str, details: dict) -> str:
    """pathが実在しexpected_sha256と一致することを確認し、実測hashを返す。"""
    if not path.is_file():
        raise DomainError(missing_code, missing_message, details)
    actual = _hash_one(path)
    if actual != expected_sha256:
        raise DomainError(
            "INPUT_CHANGED",
            f"内容が宣言されたhashと一致しません: {path}",
            {**details, "expected": expected_sha256, "actual": actual},
        )
    return actual


def _build_execution_environment(software: dict, adapter_id: str, base_dir: Path) -> dict:
    # software.executable_pathも他の外部参照（method_path・dependencies[].path）と
    # 同じくprofileファイルの親基準で解決する（絶対宣言ならそのまま使う）。
    exe_path = _resolve_declared_path(software["executable_path"], base_dir)
    exe_sha256 = _verify_pinned_file(
        exe_path, software["executable_sha256"],
        missing_code="MSDIAL_EXE_NOT_FOUND",
        missing_message=f"software.executable_pathが指す実行体がありません: {exe_path}",
        details={"executable_path": str(exe_path)},
    )

    companions: dict[str, str] = {}
    try:
        siblings = sorted(exe_path.parent.iterdir())
    except OSError:
        siblings = []
    companion_paths = [
        p for p in siblings
        if p.is_file() and p.suffix.lower() in _EXECUTION_ENVIRONMENT_SUFFIXES
    ]
    if companion_paths:
        digests = hash_files(companion_paths)
        for p in companion_paths:
            companions[p.name] = digests[str(p)]

    manifest = {
        "executable_path": str(exe_path),
        "executable_sha256": exe_sha256,
        "companions": companions,
        "msdial_version": software["msdial_version"],
        "adapter_id": adapter_id,
        "adapter_version": software["adapter_version"],
    }
    return {**manifest, "manifest_hash": canonical_hash(manifest)}


def _check_method_conflicts(method_keys: dict[str, str], acquisition: dict, omics: str) -> None:
    declared_ion_mode = (method_keys.get(method_file_mod.ION_MODE_KEY.lower()) or "").strip().lower()
    if declared_ion_mode and declared_ion_mode != acquisition["polarity"]:
        raise DomainError(
            "PROFILE_METHOD_CONFLICT",
            f"methodの Ion mode ({declared_ion_mode!r}) がprofileのacquisition.polarity "
            f"({acquisition['polarity']!r}) と矛盾します。",
            {"method_ion_mode": declared_ion_mode, "profile_polarity": acquisition["polarity"]},
        )

    declared_omics = (method_keys.get("target omics") or "").strip().lower()
    if declared_omics and declared_omics != omics:
        raise DomainError(
            "PROFILE_METHOD_CONFLICT",
            f"methodの Target omics ({declared_omics!r}) がprofileのomics ({omics!r}) "
            "と矛盾します。",
            {"method_target_omics": declared_omics, "profile_omics": omics},
        )

    declared_acquisition_type = (method_keys.get("acquisition type") or "").strip().lower()
    if declared_acquisition_type and declared_acquisition_type != acquisition["acquisition_type"]:
        raise DomainError(
            "PROFILE_METHOD_CONFLICT",
            f"methodの Acquisition type ({declared_acquisition_type!r}) がprofileの"
            f"acquisition.acquisition_type ({acquisition['acquisition_type']!r}) と矛盾します。",
            {"method_acquisition_type": declared_acquisition_type,
             "profile_acquisition_type": acquisition["acquisition_type"]},
        )


def _resolve_dependency(dep: dict, base_dir: Path, dependency_keys: dict[str, str]) -> dict:
    expected_key = dependency_keys.get(dep["kind"])
    if expected_key != dep["method_key"]:
        raise DomainError(
            "PROFILE_METHOD_CONFLICT",
            f"依存 {dep['dependency_id']!r}（kind={dep['kind']!r}）の method_key "
            f"{dep['method_key']!r} が、このadapterの対応キー {expected_key!r} と"
            "一致しません（未対応のkind、またはkindとmethod_keyの取り違え）。",
            {"dependency_id": dep["dependency_id"], "kind": dep["kind"],
             "declared_method_key": dep["method_key"], "expected_method_key": expected_key},
        )

    resolved_path = _resolve_declared_path(dep["path"], base_dir)
    if not resolved_path.is_file():
        if dep["required"]:
            raise DomainError(
                "PROFILE_INCOMPLETE",
                f"必須の依存 {dep['dependency_id']!r} が見つかりません: {resolved_path}",
                {"dependency_id": dep["dependency_id"], "path": str(resolved_path)},
            )
        return {**dep, "source_path": str(resolved_path), "present": False}

    actual_sha256 = _hash_one(resolved_path)
    if actual_sha256 != dep["sha256"]:
        raise DomainError(
            "INPUT_CHANGED",
            f"依存 {dep['dependency_id']!r} の内容が宣言されたhashと一致しません: "
            f"{resolved_path}",
            {"dependency_id": dep["dependency_id"], "path": str(resolved_path),
             "expected": dep["sha256"], "actual": actual_sha256},
        )
    # dep["sha256"]（宣言値）は実測値と一致確認済みなのでそのまま残す
    # （宣言hashと実測hashを区別して両方持たせるより、確認済みの単一の値で
    # 十分——不一致はここまでに到達する前に例外で止まっている）。
    return {**dep, "source_path": str(resolved_path), "present": True}


def _check_no_duplicate_method_keys(dependencies: list[dict]) -> None:
    """2件以上のdependencyが同じmethod_keyを取り合っていないことを確認する。

    methodファイルの1つのキーには1行しか書けない（`write_effective_method_file`の
    overridesは`{method_key: path}`という単一値の辞書）。schema側
    （`profile_schema._validate_dependency`）は`dependency_id`の重複だけを拒否し、
    `method_key`（や`kind`）の重複は禁止していない——ここでの衝突検出はTask 2
    固有の責務であり、Task 1のスキーマ検証を重複させるものではない（controller
    裁定: fix round 1）。

    `kind`ではなく`method_key`で衝突を判定する。`kind`の重複それ自体は禁止しない
    ——spec §5.1・schemaのどちらも`kind`の重複を禁じておらず、2つの依存が
    実際に異なるmethodファイルの行（method_key）を占めるなら共存できる
    （例: 将来のアダプタが同じ`kind`に複数のmethod_keyスロットを持つ場合）。
    ただし`_resolve_dependency`が`kind`→`method_key`をadapterのallowlistで
    1対1に固定しているため、現行の`msdial5`アダプタでは「`kind`が同じなら
    `method_key`も必ず同じ」になる——つまり`method_key`基準の判定は、今のところ
    「同じ`kind`の重複」も自動的に含む形で検出する。
    """
    seen: dict[str, str] = {}
    for dep in dependencies:
        method_key = dep["method_key"]
        if method_key in seen:
            raise DomainError(
                "PROFILE_METHOD_CONFLICT",
                f"依存 {seen[method_key]!r} と {dep['dependency_id']!r} が同じ"
                f"method_key {method_key!r} を取り合っています（1つのmethodファイル"
                "の1つのキーには1行しか書けないため、どちらを実効メソッドへ反映する"
                "か一意に決まりません）。",
                {"method_key": method_key,
                 "dependency_ids": [seen[method_key], dep["dependency_id"]]},
            )
        seen[method_key] = dep["dependency_id"]


def resolve_profile_inputs(profile: dict, source_root: Path) -> dict:
    """profileの外部参照を`source_root`（profileファイルの親）基準で解決する。

    method・依存ファイル・実行体の実在とhash一致を確認し、
    `processing.dependencies[].kind`と`method_key`の対応がadapterの
    allowlistと一致するかを検証し（不一致・未対応adapterは即座にエラー）、
    method原本の宣言（Ion mode / Target omics / Acquisition type）が
    profileの宣言と矛盾しないかを確認し、実行環境manifestを組み立てる。

    戻り値（「計画」）は`run_dir`に一切依存しないJSON互換dictで、そのまま
    `snapshot_profile`へ渡す。
    """
    source_root = Path(source_root)

    method_path = _resolve_declared_path(profile["processing"]["method_path"], source_root)
    method_sha256 = _verify_pinned_file(
        method_path, profile["processing"]["method_sha256"],
        missing_code="METHOD_FILE_NOT_FOUND",
        missing_message=f"processing.method_pathが指すメソッドファイルがありません: {method_path}",
        details={"method_path": str(method_path)},
    )

    adapter_id = _derive_adapter_id(profile["software"]["msdial_version"])
    capabilities = profile_adapter.adapter_capabilities(adapter_id)

    dependencies = [
        _resolve_dependency(dep, source_root, capabilities["dependency_keys"])
        for dep in profile["processing"]["dependencies"]
    ]
    _check_no_duplicate_method_keys(dependencies)

    execution_environment = _build_execution_environment(
        profile["software"], adapter_id, source_root)

    method_keys = method_file_mod.read_method_keys(method_path)
    _check_method_conflicts(method_keys, profile["acquisition"], profile["omics"])

    return {
        "profile_id": profile["profile_id"],
        "profile_revision": profile["revision"],
        "profile_content_hash": profile_content_hash(profile),
        "adapter": capabilities,
        "polarity": profile["acquisition"]["polarity"],
        "omics": profile["omics"],
        "acquisition_type": profile["acquisition"]["acquisition_type"],
        "measure": profile["processing"]["measure"],
        "method": {"source_path": str(method_path), "sha256": method_sha256},
        "dependencies": dependencies,
        "execution_environment": execution_environment,
        "source_root": str(source_root),
    }


def snapshot_profile(plan: dict, run_dir: Path) -> dict:
    """`plan`を`run_dir`へ実体化し、実行スナップショットを返す。

    method原本は変更しない。実効コピー（依存の絶対pathで上書き済み）だけを
    `run_dir/profile/effective-method.txt`へ書く。`plan_identity_hash`は
    `plan`自体のcanonical hash——`run_dir`に依存する値を一切混ぜないので、
    出力先だけが違う2回の呼び出しは常に同じ`plan_identity_hash`を返す
    （spec §6.1）。実行コピーの絶対pathを含む`effective_method_sha256`は
    別キーに分離する。
    """
    run_dir = Path(run_dir)
    snapshot_dir = run_dir / _SNAPSHOT_SUBDIR
    effective_path = snapshot_dir / _EFFECTIVE_METHOD_NAME

    overrides = {
        dep["method_key"]: dep["source_path"]
        for dep in plan["dependencies"] if dep["present"]
    }
    try:
        method_file_mod.write_effective_method_file(
            Path(plan["method"]["source_path"]), effective_path, overrides)
    except UnicodeEncodeError as exc:
        raise DomainError(
            "METHOD_ENCODING_UNSUPPORTED",
            f"実効メソッドをASCIIで書き出せません（非ASCII文字を含みます）: {exc}",
            {"overrides": overrides},
        ) from exc

    effective_sha256 = _hash_one(effective_path)
    plan_identity_hash = canonical_hash(plan)

    snapshot = copy.deepcopy(plan)
    snapshot["run_dir"] = str(run_dir.resolve())
    snapshot["effective_method_relative_path"] = f"{_SNAPSHOT_SUBDIR}/{_EFFECTIVE_METHOD_NAME}"
    snapshot["effective_method_sha256"] = effective_sha256
    snapshot["plan_identity_hash"] = plan_identity_hash
    return snapshot
