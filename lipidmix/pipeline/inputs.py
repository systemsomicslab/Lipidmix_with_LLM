"""生データフォルダの入力隔離・メソッド選択・解析専用配置（spec §4）。

このモジュールはpipelineの入力固定層。4つの公開関数を持つ。

- `inspect_inputs(source_root, request, *, exe_path)`: 元フォルダを読むだけで
  何一つ変更せず、採用する形式・メソッド・LBM・実行体・極性を決定し、
  「入力計画」(plan) を返す。
- `select_method(candidates)`: メソッド候補を内容（sha256 と、宣言された参照
  キーの解決先ハッシュ）でグルーピングし、一意なら代表を返す。異なる内容が
  複数あれば `METHOD_FILE_CHOICE_REQUIRED`。mtimeは一切使わない。
- `stage_inputs(plan, pipeline_root)`: planに基づき、raw・随伴・実効メソッドを
  `pipeline_root/input` へ実際に配置し、STAT fingerprint等を固定した
  「入力スナップショット」(snapshot) を返す。
- `verify_inputs(snapshot)`: 元rawと配置済みコピーのstat fingerprint・固定
  hashを再検査し、変化していれば例外にする（実行開始・上流終了時の再検査、
  および再開時の照合の両方から呼ばれる想定）。

副作用に注意: mcp_core・pipeline.store等の永続化層をimportしない。返す辞書は
JSON互換のプリミティブのみ（Pathは引数でだけ受け取り、戻り値はすべてstr）。

パスの絶対・相対（Task14への申し送り）:
    - `source_root` / `method.source_path` / `lbm.path` / `exe.path` /
      `pipeline_root` は**絶対文字列**（元フォルダ・元メソッド・元LBM・実行体・
      pipeline_rootそのものは、互いの外側にありうるため相対化できない）。
    - `raw_stat[].relative_path`（source_root基準）・`entries[].name`
      （同）・`companions`（同）・`staged_files[].relative_path`
      （pipeline_root基準）・`staged_files[].source_relative_path`
      （source_root基準）・`method.effective_relative_path`
      （pipeline_root基準）は**相対文字列**。
    pipeline-run.json等へ永続化する際の相対化（絶対パス側を含む）は
    Task14/17の責務であり、このモジュールは何もrelativizeしない。
"""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
from pathlib import Path

from lipidmix.console import job_manager
from lipidmix.console import method_file as method_file_mod
from lipidmix.console import runner as console_runner
from lipidmix.console.input_prep import _companions_of
from lipidmix.core.atomic_io import DomainError, canonical_hash

__all__ = ["inspect_inputs", "select_method", "stage_inputs", "verify_inputs"]

#: 初期版はlipidomics固定（Global Constraints）。pipeline-request.v1にomicsは無い。
_OMICS = "lipidomics"

_INPUT_SUBDIR = "input"
_INPUTS_META_SUBDIR = "inputs"
_EFFECTIVE_METHOD_NAME = "effective-method.txt"


# ---------- 小さなユーティリティ ----------

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolves_outside_root(path: Path, root: Path) -> bool:
    """path（symlink/reparse pointを含みうる）がroot外を指すかを判定する。

    ジャンクション・シンボリックリンクのどちらも`os.path.realpath`が解決するため、
    「symlinkかどうか」を個別判定する必要はない —— 通常のファイルはrealpathが
    自分自身のままなので常にroot配下、リンクだけが外を指しうる。
    """
    try:
        real = Path(os.path.realpath(path))
        real_root = Path(os.path.realpath(root))
    except OSError:
        return True
    try:
        real.relative_to(real_root)
    except ValueError:
        return True
    return False


def _iter_dir_raw_files(root_entry: Path, source_root: Path) -> list[Path]:
    """ディレクトリ形式rawの内部ファイルを列挙し、reparse point脱出も検査する。"""
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root_entry):
        dpath = Path(dirpath)
        if _resolves_outside_root(dpath, source_root):
            raise DomainError(
                "INPUT_ESCAPES_SOURCE_ROOT",
                f"生データ内のディレクトリが元フォルダ外を指しています: {dpath}",
                {"path": str(dpath)},
            )
        for name in filenames:
            fpath = dpath / name
            if _resolves_outside_root(fpath, source_root):
                raise DomainError(
                    "INPUT_ESCAPES_SOURCE_ROOT",
                    f"生データ内のファイルが元フォルダ外を指しています: {fpath}",
                    {"path": str(fpath)},
                )
            out.append(fpath)
    return sorted(out, key=lambda p: str(p).lower())


def _stat_entry(path: Path, relative_path: str, role: str) -> dict:
    st = path.stat()
    return {"relative_path": relative_path, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "role": role}


# ---------- 形式解決 ----------

def _resolve_raw_format(source_root: Path, requested_extension: str | None) -> tuple[str, dict]:
    """採用する計測拡張子を決める（spec §4.1「1フォルダ直下の1形式」）。

    明示があればそれを使う（フォルダに実在しなければエラー）。省略時は、
    拡張子が1種類ならそれを採用。`.wiff`/`.wiff2`だけが混在し、同一stem集合が
    1対1で一致するときだけ`.wiff`を既定にする（spec §4.2）。それ以外の混在は
    `MIXED_RAW_FORMATS`で形式指定を要求する。rawが無く子フォルダだけがあれば
    `DATASET_SELECTION_REQUIRED`。
    """
    formats = job_manager.raw_input_summary(source_root)
    if requested_extension:
        ext = requested_extension.lower().lstrip(".")
        if ext not in formats:
            raise DomainError(
                "MIXED_RAW_FORMATS",
                f"要求された形式 '{ext}' の計測ファイルがありません: {source_root}",
                {"formats": formats, "requested": ext},
            )
        return ext, formats
    if not formats:
        candidates = [p.name for p in sorted(source_root.iterdir()) if p.is_dir()]
        if candidates:
            raise DomainError(
                "DATASET_SELECTION_REQUIRED",
                f"{source_root} 直下に計測ファイルがなく、子フォルダのみがあります。"
                "解析対象フォルダを明示してください。",
                {"candidates": candidates},
            )
        raise DomainError(
            "MIXED_RAW_FORMATS",
            f"データフォルダに MS-DIAL が読める計測ファイルがありません: {source_root}",
            {"formats": formats},
        )
    if len(formats) == 1:
        return next(iter(formats)), formats
    if set(formats) == {"wiff", "wiff2"}:
        wiff_stems = {p.stem for p in source_root.iterdir() if p.suffix.lower() == ".wiff"}
        wiff2_stems = {p.stem for p in source_root.iterdir() if p.suffix.lower() == ".wiff2"}
        if wiff_stems and wiff_stems == wiff2_stems:
            return "wiff", formats
    raise DomainError(
        "MIXED_RAW_FORMATS",
        "データフォルダに MS-DIAL が対象とする拡張子が2種類以上あり、自動選択できません: "
        + ", ".join(f"{ext}x{n}" for ext, n in sorted(formats.items())),
        {"formats": formats},
    )


def _collect_primaries_and_companions(
    source_root: Path, ext: str,
) -> tuple[list[Path], dict[Path, list[Path]]]:
    """選択済み拡張子の主ファイル一覧と、随伴ファイル一覧を返す（escapeチェック込み）。"""
    entries = sorted(source_root.iterdir())
    file_entries = [p for p in entries if p.is_file()]
    suffix = "." + ext
    primaries = [p for p in entries
                 if p.name.lower().endswith(suffix)
                 and p.suffix.lower().lstrip(".") == ext]
    if not primaries:
        raise DomainError(
            "MIXED_RAW_FORMATS",
            f"入力フォルダに .{ext} がありません: {source_root}",
            {"extension": ext},
        )

    companions_map: dict[Path, list[Path]] = {}
    for primary in primaries:
        if _resolves_outside_root(primary, source_root):
            raise DomainError(
                "INPUT_ESCAPES_SOURCE_ROOT",
                f"生データが元フォルダ外を指しています: {primary}",
                {"path": str(primary)},
            )
        if primary.is_dir():
            _iter_dir_raw_files(primary, source_root)  # escapeチェックのためだけに歩く
            companions_map[primary] = []
            continue
        found = _companions_of(file_entries, primary, ext)
        for companion in found:
            if _resolves_outside_root(companion, source_root):
                raise DomainError(
                    "INPUT_ESCAPES_SOURCE_ROOT",
                    f"随伴ファイルが元フォルダ外を指しています: {companion}",
                    {"path": str(companion)},
                )
        companions_map[primary] = found
    return primaries, companions_map


def _build_entries_and_stat(
    source_root: Path, primaries: list[Path], companions_map: dict[Path, list[Path]],
) -> tuple[list[dict], list[dict]]:
    """物理配置に使う`entries`（トップレベル名の一覧）とSTAT fingerprint一覧を作る。"""
    entries: list[dict] = []
    raw_stat: list[dict] = []
    for primary in primaries:
        if primary.is_dir():
            entries.append({"name": primary.name, "kind": "dir", "role": "primary"})
            for inner in _iter_dir_raw_files(primary, source_root):
                rel = str(inner.relative_to(source_root)).replace(os.sep, "/")
                raw_stat.append(_stat_entry(inner, rel, "primary"))
        else:
            entries.append({"name": primary.name, "kind": "file", "role": "primary"})
            raw_stat.append(_stat_entry(primary, primary.name, "primary"))
        for companion in companions_map.get(primary, []):
            entries.append({"name": companion.name, "kind": "file", "role": "companion",
                            "primary": primary.name})
            raw_stat.append(_stat_entry(companion, companion.name, "companion"))
    return entries, raw_stat


# ---------- メソッド選択 ----------

def _method_candidate_dict(path: Path, ion_mode: str | None, mtime: float) -> dict:
    """1件のメソッドファイルからselect_method用のdictを作る（sha256 + 参照hash）。"""
    keys = method_file_mod.read_method_keys(path)
    fingerprint = method_file_mod.method_reference_fingerprint(keys, path)
    reference_hash = canonical_hash({
        key: (info["sha256"] if info["resolved"] else f"UNRESOLVED:{info['declared']}")
        for key, info in fingerprint.items()
    })
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "mtime": mtime,
        "polarity": ion_mode,
        "reference_hash": reference_hash,
    }


def select_method(candidates: list[dict]) -> dict:
    """メソッド候補を内容でグルーピングし、一意なら代表を返す（spec §4.1/4.3, D07）。

    グルーピング鍵は `(sha256, reference_hash)`。生バイトが同一でも、宣言された
    参照キー（`Lbm file path`等）の解決先ハッシュが候補ごとに異なれば別の実効
    メソッドとして扱う —— 「異なる相対パス基準で同じ文字列を含む候補」を
    内容ハッシュだけでは見分けられないため。mtimeはグルーピングに一切使わない。
    """
    if not candidates:
        raise DomainError("METHOD_FILE_NOT_GIVEN", "メソッド候補がありません。", {})
    groups: dict[tuple, list[dict]] = {}
    for candidate in candidates:
        key = (candidate["sha256"], candidate.get("reference_hash"))
        groups.setdefault(key, []).append(candidate)
    if len(groups) != 1:
        raise DomainError(
            "METHOD_FILE_CHOICE_REQUIRED", "解析条件を一つ選んでください",
            {"candidates": candidates})
    chosen = sorted(next(iter(groups.values())), key=lambda c: c["path"])[0]
    return chosen


def _discover_candidate_dicts(source_root: Path, polarity: str | None) -> list[dict]:
    method_candidates, _searched = method_file_mod.discover_method_candidates(
        source_root, polarity=polarity, omics=_OMICS)
    if polarity is not None:
        # 明示極性が要求極性と食い違う候補は選択対象から外す（spec §4.1-2）。
        method_candidates = [c for c in method_candidates if c.ion_mode == polarity]
    return [_method_candidate_dict(Path(c.path), c.ion_mode, c.mtime) for c in method_candidates]


def _resolve_method(source_root: Path, request: dict) -> dict:
    """method_fileが明示されていればそれを唯一の候補として使い、省略時は
    discover_method_candidates + select_method で決める（spec §4.1-1/2/3）。"""
    explicit = request.get("method_file")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = source_root / path
        if not path.is_file():
            raise DomainError(
                "METHOD_FILE_NOT_FOUND",
                f"method_fileが指すファイルがありません: {explicit}",
                {"method_file": explicit})
        keys = method_file_mod.read_method_keys(path)
        ion_mode = (keys.get("ion mode") or "").strip().lower() or None
        return _method_candidate_dict(path, ion_mode, path.stat().st_mtime)

    candidates = _discover_candidate_dicts(source_root, request.get("polarity"))
    if not candidates:
        raise DomainError(
            "METHOD_FILE_NOT_GIVEN",
            f"使えるメソッドファイルが見つかりません: {source_root}",
            {"source_root": str(source_root)})
    return select_method(candidates)


def _resolve_polarity(request: dict, method_path: Path) -> dict:
    """spec §4.1-4: 極性省略時は一意に選べたメソッドのIon mode宣言を採る。

    これは生データからの極性検証ではないので、呼び出し側は`unverified`へ
    その旨を記録する。
    """
    explicit = request.get("polarity")
    if explicit is not None:
        return {"value": explicit, "source": "request_explicit"}
    keys = method_file_mod.read_method_keys(method_path)
    declared = (keys.get("ion mode") or "").strip().lower()
    if declared not in ("positive", "negative"):
        raise DomainError(
            "POLARITY_UNDETERMINED",
            f"極性が未指定で、メソッドファイルの Ion mode も有効な値ではありません: "
            f"{declared!r}（{method_path}）",
            {"method_file": str(method_path), "ion_mode": declared})
    return {"value": declared, "source": "method_declaration"}


def _resolve_lbm_pinned(method_keys: dict, method_path: Path, exe_path: str,
                        request: dict, source_root: Path) -> dict:
    override = request.get("lbm_file")
    if override and not Path(override).is_absolute():
        override = str(source_root / override)
    lbm = method_file_mod.resolve_lbm(
        method_keys, method_path, omics=_OMICS, exe_path=exe_path,
        env=dict(os.environ), override=override)
    if lbm.error_code:
        raise DomainError(lbm.error_code, lbm.message or "",
                          {"candidates": list(lbm.candidates)} if lbm.candidates else {})
    if lbm.path is None:
        return {"path": None, "sha256": None, "source": lbm.source}
    resolved = Path(lbm.path).resolve()
    return {"path": str(resolved), "sha256": _sha256_file(resolved), "source": lbm.source}


def _resolve_exe(exe_path: Path) -> dict:
    exe_path = Path(exe_path)
    if not exe_path.is_file():
        raise DomainError("MSDIAL_EXE_NOT_FOUND", f"実行体がありません: {exe_path}",
                          {"exe_path": str(exe_path)})
    if not console_runner.is_console_exe(str(exe_path)):
        raise DomainError(
            "MSDIAL_EXE_NOT_CONSOLE",
            f"MSDIAL_EXE が MS-DIAL Console ではありません: {exe_path}",
            {"exe_path": str(exe_path)})
    return {"path": str(exe_path.resolve()), "sha256": _sha256_file(exe_path), "version": None}


# ---------- 公開API: inspect_inputs ----------

def inspect_inputs(source_root: Path, request: dict, *, exe_path: Path) -> dict:
    """元フォルダを読むだけで、採用する形式・メソッド・LBM・実行体・極性を決める。

    何も書き込まない（隔離・固定は`stage_inputs`の責務）。戻り値の「入力計画」は
    そのまま`stage_inputs`へ渡す。
    """
    source_root = Path(source_root).expanduser()
    if not source_root.is_dir():
        raise DomainError("DATASET_ROOT_NOT_FOUND", f"source_rootが存在しません: {source_root}",
                          {"source_root": str(source_root)})

    ext, _formats = _resolve_raw_format(source_root, request.get("keep_extension"))
    primaries, companions_map = _collect_primaries_and_companions(source_root, ext)
    entries, raw_stat = _build_entries_and_stat(source_root, primaries, companions_map)
    companions = {p.name: [c.name for c in cs] for p, cs in companions_map.items() if cs}

    chosen_method = _resolve_method(source_root, request)
    method_path = Path(chosen_method["path"])
    method_keys = method_file_mod.read_method_keys(method_path)

    # 最終選択後にだけ厳格な参照解決を行う（select_methodのグルーピングは弱い版）。
    method_file_mod.resolve_method_references(method_keys, method_path)

    polarity = _resolve_polarity(request, method_path)
    exe_info = _resolve_exe(Path(exe_path))
    lbm_info = _resolve_lbm_pinned(method_keys, method_path, exe_info["path"], request, source_root)

    unverified: list[str] = []
    if polarity["source"] == "method_declaration":
        unverified.append("polarity_from_method_declaration_not_verified_from_raw")

    overrides: dict[str, str] = {}
    if lbm_info["path"] and lbm_info["source"] != "not_required":
        if lbm_info["source"] == "method_file":
            # 原本メソッドが `Lbm file path` を宣言していた場合。宣言値が相対
            # 参照だと、実効コピー（別ディレクトリ）へ verbatim コピーした
            # 瞬間に意味が変わる（コピー先基準で解決されてしまう）。
            # spec §4.3「コピー後に相対参照の意味を変えない」を守るため、
            # 宣言が相対のときだけ、原本基準で解決済みの絶対パスへ書き換える
            # （絶対解決は既存どおりresolve_lbmが原本ディレクトリ基準で行う）。
            declared_lbm = (method_keys.get(method_file_mod.LBM_KEY.lower()) or "").strip()
            if declared_lbm and not Path(declared_lbm).is_absolute():
                overrides[method_file_mod.LBM_KEY] = lbm_info["path"]
        else:
            # 原本に宣言が無く、build_tree/env/exe_dirへフォールバックした場合は
            # 元々そのキーの行自体が無いので、そのまま新規追加する。
            overrides[method_file_mod.LBM_KEY] = lbm_info["path"]

    return {
        "source_root": str(source_root.resolve()),
        "selected_format": ext,
        "entries": entries,
        "raw_stat": raw_stat,
        "companions": companions,
        "method": {
            "source_path": str(method_path.resolve()),
            "sha256": chosen_method["sha256"],
            "effective_relative_path": None,
            "effective_sha256": None,
            "overrides": overrides,
        },
        "lbm": {"path": lbm_info["path"], "sha256": lbm_info["sha256"]},
        "exe": exe_info,
        "polarity": polarity,
        "unverified": unverified,
    }


# ---------- 公開API: stage_inputs ----------

def _copy_or_link(src: Path, dst: Path, link_fn) -> None:
    try:
        link_fn(src, dst)
    except OSError:
        # ボリュームをまたぐ／リンク非対応のファイルシステム。リンクは書込隔離では
        # ないので、コピーでもハードリンクでも「元rawは読むだけ」という前提は
        # 変わらない（spec §4.2）。
        shutil.copy2(src, dst)


def _reconcile_staged_file(src: Path, dst: Path, name: str) -> None:
    src_stat = src.stat()
    dst_stat = dst.stat()
    if dst_stat.st_size != src_stat.st_size or dst_stat.st_mtime_ns != src_stat.st_mtime_ns:
        raise DomainError(
            "STAGED_INPUT_MISMATCH",
            f"配置済みのファイルが元入力と一致しません（再開時の照合）: {name}",
            {"name": name})


def _reconcile_staged_dir(dst: Path, expected_stats: list[dict], dirname: str) -> None:
    prefix = dirname + "/"
    for stat in expected_stats:
        rel = stat["relative_path"][len(prefix):]
        dst_file = dst / rel
        if not dst_file.is_file():
            raise DomainError(
                "STAGED_INPUT_MISMATCH",
                f"配置済みディレクトリに欠落ファイルがあります: {dirname}/{rel}",
                {"name": f"{dirname}/{rel}"})
        st = dst_file.stat()
        if st.st_size != stat["size"] or st.st_mtime_ns != stat["mtime_ns"]:
            raise DomainError(
                "STAGED_INPUT_MISMATCH",
                f"配置済みディレクトリのファイルが元入力と一致しません: {dirname}/{rel}",
                {"name": f"{dirname}/{rel}"})


def stage_inputs(plan: dict, pipeline_root: Path, *,
                  disk_usage=shutil.disk_usage, link_fn=os.link) -> dict:
    """planに基づき、raw・随伴・実効メソッドを`pipeline_root/input`へ実際に配置する。

    1. 合計サイズと空き容量を確認する（起動前に停止、spec §4.2）。
    2. 新規（空の配置先）ならos.link優先→copy2フォールバックで全ファイルを配置。
    3. 既存の配置先（resume）なら、期待される各ファイルの対応・fingerprintを
       照合し、不一致を単にskipしない（spec §4.2「同名ファイルは...検証し、
       不一致を単にskipしない」）。欠けているものだけ補って配置する。
    4. 実効メソッドファイルを`inputs/effective-method.txt`へ常に書く。
    """
    source_root = Path(plan["source_root"])
    pipeline_root = Path(pipeline_root)
    input_dir = pipeline_root / _INPUT_SUBDIR
    inputs_meta_dir = pipeline_root / _INPUTS_META_SUBDIR

    total_size = sum(entry["size"] for entry in plan["raw_stat"])
    usage_root = pipeline_root
    while not usage_root.exists():
        usage_root = usage_root.parent
    usage = disk_usage(usage_root)
    if usage.free < total_size:
        raise DomainError(
            "INSUFFICIENT_FREE_SPACE",
            f"配置先の空き容量が不足しています（必要 {total_size} バイト、"
            f"空き {usage.free} バイト）: {pipeline_root}",
            {"required": total_size, "available": usage.free})

    input_dir.mkdir(parents=True, exist_ok=True)
    entries = plan["entries"]
    expected_names = {e["name"] for e in entries}
    existing_names = {p.name for p in input_dir.iterdir()}
    unexpected = existing_names - expected_names
    if unexpected:
        raise DomainError(
            "STAGED_INPUT_MISMATCH",
            f"配置済みの入力に想定外のファイルがあります: {sorted(unexpected)}",
            {"unexpected": sorted(unexpected)})

    raw_stat_by_prefix: dict[str, list[dict]] = {}
    for stat in plan["raw_stat"]:
        prefix = stat["relative_path"].split("/", 1)[0]
        raw_stat_by_prefix.setdefault(prefix, []).append(stat)

    staged_files: list[dict] = []
    for entry in entries:
        name = entry["name"]
        src = source_root / name
        dst = input_dir / name
        if entry["kind"] == "dir":
            if dst.exists():
                _reconcile_staged_dir(dst, raw_stat_by_prefix.get(name, []), name)
            else:
                shutil.copytree(src, dst)
            for stat in raw_stat_by_prefix.get(name, []):
                staged_files.append({
                    "relative_path": f"{_INPUT_SUBDIR}/{stat['relative_path']}",
                    "source_relative_path": stat["relative_path"]})
        else:
            if dst.exists():
                _reconcile_staged_file(src, dst, name)
            else:
                _copy_or_link(src, dst, link_fn)
            staged_files.append({
                "relative_path": f"{_INPUT_SUBDIR}/{name}",
                "source_relative_path": name})

    overrides = dict(plan["method"]["overrides"])
    method_path = Path(plan["method"]["source_path"])
    effective_path = inputs_meta_dir / _EFFECTIVE_METHOD_NAME
    try:
        method_file_mod.write_effective_method_file(method_path, effective_path, overrides)
    except UnicodeEncodeError as exc:
        raise DomainError(
            "METHOD_ENCODING_UNSUPPORTED",
            f"実効メソッドをASCIIで書き出せません（非ASCII文字を含みます）: {exc}",
            {"overrides": overrides}) from exc

    snapshot = copy.deepcopy(plan)
    snapshot["pipeline_root"] = str(pipeline_root.resolve())
    snapshot["staged_files"] = staged_files
    snapshot["method"]["effective_relative_path"] = f"{_INPUTS_META_SUBDIR}/{_EFFECTIVE_METHOD_NAME}"
    snapshot["method"]["effective_sha256"] = _sha256_file(effective_path)
    return snapshot


# ---------- 公開API: verify_inputs ----------

def verify_inputs(snapshot: dict) -> None:
    """元rawと配置済みコピーのstat fingerprint・固定hashを再検査する（spec D05）。

    変化していれば`INPUT_CHANGED`（元raw・method/LBM/exeの内容）または
    `STAGED_INPUT_MISMATCH`（配置済みコピー）にする。rawの全量内容ハッシュは
    検証しない（spec: 「rawの全量ハッシュは初期版の必須条件にしない」）。
    """
    source_root = Path(snapshot["source_root"])
    changed: list[str] = []
    for stat in snapshot["raw_stat"]:
        path = source_root / stat["relative_path"]
        try:
            st = path.stat()
        except OSError:
            changed.append(stat["relative_path"])
            continue
        if st.st_size != stat["size"] or st.st_mtime_ns != stat["mtime_ns"]:
            changed.append(stat["relative_path"])
    if changed:
        raise DomainError(
            "INPUT_CHANGED",
            f"元の生データが実行開始後に変化しています: {changed}",
            {"changed": changed})

    pipeline_root = snapshot.get("pipeline_root")
    if pipeline_root:
        pipeline_root = Path(pipeline_root)
        raw_stat_by_rel = {s["relative_path"]: s for s in snapshot["raw_stat"]}
        mismatched: list[str] = []
        for staged in snapshot.get("staged_files", []):
            source_stat_entry = raw_stat_by_rel.get(staged["source_relative_path"])
            if source_stat_entry is None:
                continue
            path = pipeline_root / staged["relative_path"]
            try:
                st = path.stat()
            except OSError:
                mismatched.append(staged["relative_path"])
                continue
            if (st.st_size != source_stat_entry["size"]
                    or st.st_mtime_ns != source_stat_entry["mtime_ns"]):
                mismatched.append(staged["relative_path"])
        if mismatched:
            raise DomainError(
                "STAGED_INPUT_MISMATCH",
                f"配置済みの入力が元データと一致しません: {mismatched}",
                {"mismatched": mismatched})

    for label, info, path_key in (
        ("method", snapshot.get("method"), "source_path"),
        ("lbm", snapshot.get("lbm"), "path"),
        ("exe", snapshot.get("exe"), "path"),
    ):
        if not info or not info.get(path_key) or not info.get("sha256"):
            continue
        path = Path(info[path_key])
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            raise DomainError("INPUT_CHANGED", f"{label}のファイルが読めません: {path}",
                              {"which": label, "path": str(path)}) from exc
        if digest != info["sha256"]:
            raise DomainError("INPUT_CHANGED", f"{label}の内容が変化しています: {path}",
                              {"which": label, "path": str(path)})
