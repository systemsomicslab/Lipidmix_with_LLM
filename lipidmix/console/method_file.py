"""MS-DIAL Console のメソッドファイルを解決する純ロジック。

**なぜこの層が要るか**（docs/HISTRY.md 2026-09-04(2)）:

MS-DIAL 5 の GUI と Console は、脂質ライブラリ（LBM）の持ち方が違う。

- GUI: `DataBaseModel` のリストで持ち、アプリフォルダ
  （`Assembly.GetExecutingAssembly().Location` の親）の `*.lbm?` を 1 件
  自動で拾う（`MethodSettingModelFactory` / `DataBaseSettingModel.TrySetLbmLibrary`）。
  ユーザーには選ばせない。
- Console: レガシーの単一フィールド `param.LbmFilePath` しか読まない
  （`MsdialCoreTestApp/Process/CommonProcess.cs` の
  `if (ErrorHandler.IsFileExist(param.LbmFilePath))`）。

そして GUI は `param.LbmFilePath` に**一度も代入しない**（MSDIAL5 の src 全体で
このフィールドへの代入はプロパティ自身の setter だけ）。`ParameterToString()` は
そのレガシーフィールドを書き出すので、GUI 由来のパラメータは
**`Lbm file path:` が構造的に必ず空**になる。空のまま Console に渡すと、
警告もエラーも出さずに同定 0 件で完走する。

この層は GUI と同じ規則を再現して段差を埋める。**GUI が拒否する状況でだけ拒否する**
（アプリフォルダの `*.lbm?` がちょうど 1 件でなければ GUI も MessageBox で止める。
`DatasetParameterSettingModel.Prepare`）。

もう一方の `find_method_candidates` は、GUI が実行のたびに自動保存する
`<project>_param_<endtimestamp>.txt`（`MethodModelBase.AutoParametersSave`。
ASCII の `key: value`＝Console が読める形式）を探す。ユーザーは手動エクスポートを
しなくてよい。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# GUI の DataBaseSettingViewModel が使う判定と同じ（`@"\.lbm\d*"`）。
# .NET の `GetFiles(dir, "*.lbm?")` が拾う .lbm / .lbm2 に一致し、.lbmx は拾わない。
_LBM_SUFFIX = re.compile(r"\.lbm\d*$", re.IGNORECASE)

# GUI の AutoParametersSave が付ける名前は `<project>_param_<yyyyMMddHHmm>.txt`。
_PARAM_FILENAME = re.compile(r"_param_\d+\.txt$", re.IGNORECASE)

LBM_KEY = "Lbm file path"

ION_MODE_KEY = "Ion mode"
ADDUCT_KEY = "Searched adduct ions"

#: 極性ごとのアダクト標準セット。ラボの実パラメータ（2_lipidome_lcms/NEG と
#: 20240915_spleen/POS）から採った。両者は極性・アダクト・スレッド数以外は
#: 同一だったので、この 2 行の差し替えが極性変換のすべて。
STANDARD_ADDUCTS = {
    "positive": (
        "[M+H]+,[M+NH4]+,[M+Na]+,[M+CH3OH+H]+,[M+K]+,[M+Li]+,[M+ACN+H]+,[M+H-H2O]+,"
        "[M+H-2H2O]+,[M+2Na-H]+,[M+IsoProp+H]+,[M+ACN+Na]+,[M+2K-H]+,[M+DMSO+H]+,"
        "[M+2ACN+H]+,[M+IsoProp+Na+H]+,[M-C6H10O4+H]+,[M-C6H10O5+H]+,[M-C6H8O6+H]+,"
        "[2M+H]+,[2M+NH4]+,[2M+Na]+,[2M+3H2O+2H]+,[2M+K]+,[2M+ACN+H]+,[2M+ACN+Na]+,"
        "[M+2H]2+,[M+H+NH4]2+,[M+H+Na]2+,[M+H+K]2+,[M+ACN+2H]2+,[M+2Na]2+,"
        "[M+2ACN+2H]2+,[M+3ACN+2H]2+,[M+3H]3+,[M+2H+Na]3+,[M+H+2Na]3+,[M+3Na]3+"),
    "negative": (
        "[M-H]-,[M-H2O-H]-,[M+Na-2H]-,[M+Cl]-,[M+K-2H]-,[M+HCOO]-,[M+CH3COO]-,"
        "[M+C2H3N+Na-2H]-,[M+Br]-,[M+TFA-H]-,[M-C6H10O4-H]-,[M-C6H10O5-H]-,"
        "[M-C6H8O6-H]-,[M+CH3COONa-H]-,[2M-H]-,[2M+FA-H]-,[2M+Hac-H]-,[3M-H]-,"
        "[M-2H]2-,[M-3H]3-"),
}


# メソッドファイルは実測 9KB 程度。誤って .mddata（GB 級）を渡されても
# 落ちないよう上限を置く（_looks_like_method_text が先に弾くが、単体でも安全に）。
_MAX_METHOD_BYTES = 1 << 20


@dataclass(frozen=True)
class LbmResolution:
    """LBM の解決結果。`error_code` が非 None なら呼び出し側は停止する。"""

    path: str | None
    source: str  # method_file | env | exe_dir | not_required
    error_code: str | None = None
    message: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodCandidate:
    """GUI が自動保存した既存パラメータファイル 1 件。"""

    path: str
    ion_mode: str | None
    omics: str | None
    has_lbm: bool
    mtime: float


def read_method_keys(path: Path) -> dict[str, str]:
    """`key: value` を小文字キーの辞書にして返す。

    MS-DIAL の `ConfigParser.ReadForLcmsParameter` と同じ割り方をする:
    `#` 始まりを飛ばし、**最初に現れる `:` か `=`** で 1 回だけ割る。
    値側の `:`（`C:\\...`）で割ってはいけないので `split(sep, 1)` にする。

    値が空の行も残す。`Lbm file path:` が「空で存在する」ことが、
    GUI 由来のパラメータを見分ける手がかりそのものだから。
    """
    try:
        raw = path.read_bytes()[:_MAX_METHOD_BYTES]
    except OSError:
        return {}
    if b"\x00" in raw:
        return {}
    text = raw.decode("ascii", errors="replace")

    keys: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        positions = [i for i in (stripped.find(":"), stripped.find("=")) if i > 0]
        if not positions:
            continue
        cut = min(positions)
        key = stripped[:cut].strip().lower()
        if key:
            keys[key] = stripped[cut + 1:].strip()
    return keys


def find_lbm_files(directory: Path) -> list[Path]:
    """directory 直下の `*.lbm` / `*.lbm2` を返す（GUI と同じ TopDirectoryOnly）。"""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    return [p for p in entries if p.is_file() and _LBM_SUFFIX.search(p.name)]


def resolve_lbm(
    method_keys: dict[str, str],
    method_file: Path,
    omics: str,
    exe_path: str | None,
    env: dict[str, str],
    override: str | None = None,
) -> LbmResolution:
    """脂質ライブラリのパスを GUI と同じ規則で解決する。

    順に: 明示引数 → メソッドファイルの宣言 → `MSDIAL_LBM` →
    MSDIAL_EXE と同じフォルダ。
    lipidomics 以外では LBM を要求しない（Console も読まないため）。
    """
    if omics != "lipidomics":
        return LbmResolution(path=None, source="not_required")

    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return LbmResolution(path=str(candidate), source="argument")
        return LbmResolution(
            path=None, source="argument", error_code="LBM_NOT_FOUND",
            message=f"lbm_file が指すファイルがありません: {override}")

    declared = (method_keys.get(LBM_KEY.lower()) or "").strip()
    if declared:
        # MS-DIAL の ResolvePathFromMethodFile と同じくメソッドファイル基準で解決する。
        candidate = Path(declared)
        if not candidate.is_absolute():
            candidate = method_file.parent / candidate
        if candidate.is_file():
            return LbmResolution(path=str(candidate), source="method_file")
        return LbmResolution(
            path=None, source="method_file", error_code="LBM_NOT_FOUND",
            message=(f"メソッドファイルが指す脂質ライブラリが見つかりません: {declared}  "
                     f"（{method_file} 基準で解決: {candidate}）"))

    from_env = (env.get("MSDIAL_LBM") or "").strip()
    if from_env:
        candidate = Path(from_env)
        if candidate.is_file():
            return LbmResolution(path=str(candidate), source="env")
        return LbmResolution(
            path=None, source="env", error_code="LBM_NOT_FOUND",
            message=f"環境変数 MSDIAL_LBM が指すファイルがありません: {from_env}")

    if not exe_path:
        return LbmResolution(
            path=None, source="exe_dir", error_code="LBM_NOT_FOUND",
            message="脂質ライブラリ（.lbm2）を解決できません。MSDIAL_EXE が未設定です。")

    exe_dir = Path(exe_path).expanduser().parent
    found = find_lbm_files(exe_dir)
    if len(found) == 1:
        return LbmResolution(path=str(found[0]), source="exe_dir")
    if not found:
        return LbmResolution(
            path=None, source="exe_dir", error_code="LBM_NOT_FOUND",
            message=(
                "脂質ライブラリ（.lbm2）が見つかりません。MS-DIAL GUI は"
                "アプリフォルダの *.lbm2 を自動で使いますが、Console はメソッド"
                "ファイルの `Lbm file path:` しか読まず、GUI が書き出す"
                "パラメータはこの行が必ず空です。空のまま実行すると"
                "**警告なしで同定 0 件**になります。"
                f"探した場所: {exe_dir}  "
                "MS-DIAL のインストールフォルダにある .lbm2 のパスを環境変数"
                "MSDIAL_LBM に設定するか、lbm_file 引数で渡してください。"))
    return LbmResolution(
        path=None, source="exe_dir", error_code="LBM_AMBIGUOUS",
        message=(
            f"脂質ライブラリの候補が {len(found)} 件あり、どれを使うか決められません: {exe_dir}  "
            "MS-DIAL GUI も 1 件でなければ実行を止めます。1 件だけ残すか、"
            "lbm_file 引数で明示してください。"),
        candidates=tuple(str(p) for p in found))


def find_method_candidates(
    directories, polarity: str | None = None, omics: str | None = None,
) -> list[MethodCandidate]:
    """既存の自動保存パラメータ（`*_param_<ts>.txt`）を新しい順に返す。

    GUI は解析のたびに `MethodModelBase.AutoParametersSave` でこれを
    プロジェクトフォルダへ書く。ASCII の `key: value` なので Console がそのまま読める。
    """
    seen: set[Path] = set()
    out: list[MethodCandidate] = []
    for directory in directories:
        d = Path(directory)
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or not _PARAM_FILENAME.search(p.name):
                continue
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            keys = read_method_keys(p)
            if not keys:
                continue  # バイナリ／読めない
            ion = (keys.get("ion mode") or "").strip().lower() or None
            om = (keys.get("target omics") or "").strip().lower() or None
            if polarity is not None and ion != polarity:
                continue
            if omics is not None and om != omics:
                continue
            out.append(MethodCandidate(
                path=str(p),
                ion_mode=ion,
                omics=om,
                has_lbm=bool((keys.get(LBM_KEY.lower()) or "").strip()),
                mtime=p.stat().st_mtime,
            ))
    out.sort(key=lambda c: c.mtime, reverse=True)
    return out


def write_effective_method_file(src: Path, dest: Path, overrides: dict[str, str]) -> Path:
    """`src` を写して `overrides` のキーを差し替えたメソッドファイルを `dest` に書く。

    **元ファイルは触らない**。ユーザーのデータフォルダにある GUI 由来の
    パラメータを書き換えると、次に GUI で開いたときの整合が取れなくなる。
    出力は ASCII / LF（`ConfigParser` は `StreamReader(path, Encoding.ASCII)`）。
    """
    remaining = dict(overrides)
    lines: list[str] = []
    for line in src.read_text(encoding="ascii", errors="replace").splitlines():
        stripped = line.strip()
        replaced = False
        if stripped and not stripped.startswith("#"):
            positions = [i for i in (stripped.find(":"), stripped.find("=")) if i > 0]
            if positions:
                key = stripped[:min(positions)].strip().lower()
                for override_key in list(remaining):
                    if override_key.lower() == key:
                        lines.append(f"{override_key}: {remaining.pop(override_key)}")
                        replaced = True
                        break
        if not replaced:
            lines.append(line)

    for key, value in remaining.items():
        lines.append(f"{key}: {value}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return dest
