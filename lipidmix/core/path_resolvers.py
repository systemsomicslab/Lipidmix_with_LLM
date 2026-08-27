"""データファイルのパス解決とバッチ選択（ファイル名の処理タイムスタンプ基準）。

依存は mcp_core（DATA_DIR）と stdlib のみの下位レイヤ。tools_* / server は import
しない。`list_data_files` はここに純関数として置き、MCP ツールとしての登録は上位
（lipidmix.tools.dataset）が担う — こうすることで resolve_* → list_data_files → DATA_DIR という
参照が下位で閉じ、lipidmix.tools.dataset との循環を避けられる。

DATA_DIR は実行時に差し替わるため `mcp_core.DATA_DIR` を動的参照する。
"""
import os
from pathlib import Path

from lipidmix.core import mcp_core

# MS-DIALのアライメント結果ファイル名に埋め込まれる処理タイムスタンプ。
# 例: AlignmentResult_2026_05_15_10_13_35_PeakProperties.arf
#     → 再アライメントすると新しいタイムスタンプのセットが増える（＝旧版/新版の重複）。
import re

_ALIGNMENT_TIMESTAMP_RE = re.compile(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})")
# サンプル名等に付く12〜14桁の連続タイムスタンプ（例: _202605151012）も拾う。
_COMPACT_TIMESTAMP_RE = re.compile(r"(\d{12,14})")


def _recency_key(path: str) -> tuple[str, float]:
    """ファイルの「新しさ」の並べ替えキー。

    第一に**ファイル名に埋め込まれた処理タイムスタンプ**（コピーでも保たれる）、
    第二に更新時刻(mtime)。タイムスタンプ無しは空文字となり mtime で比較される。
    """
    name = os.path.basename(path)
    match = _ALIGNMENT_TIMESTAMP_RE.search(name)
    if match:
        timestamp = match.group(1).replace("_", "")
    else:
        compact = _COMPACT_TIMESTAMP_RE.search(name)
        timestamp = compact.group(1) if compact else ""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return (timestamp, mtime)


def _pick_latest(paths: list[str]) -> str | None:
    """同種ファイルが重複（旧版/新版）する場合に最新版のパスを返す。"""
    if not paths:
        return None
    return max(paths, key=_recency_key)


def _batch_key(path: str) -> str:
    """ファイル名に埋め込まれた処理タイムスタンプ（＝バッチ識別子）を返す。

    MS-DIAL は1回の処理で生成する全ファイルに同一の `AlignmentResult_<timestamp>`
    接頭辞を付ける。そのタイムスタンプ（無ければ12-14桁連番）を正規化して返す。
    どちらも持たないファイルは空文字（＝バッチ不明）となる。mtime は見ない
    （コピーでも保たれるファイル名側の識別子だけでバッチを束ねるため）。
    """
    name = os.path.basename(path)
    match = _ALIGNMENT_TIMESTAMP_RE.search(name)
    if match:
        return match.group(1).replace("_", "")
    compact = _COMPACT_TIMESTAMP_RE.search(name)
    return compact.group(1) if compact else ""


def _select_latest_batch(paths: list[str]) -> list[str]:
    """複数バッチ（処理タイムスタンプ）が混在する場合に最新バッチへ絞る。

    - 埋め込みタイムスタンプを持つファイルがあれば、その最大値に一致する
      ファイル群だけを残す（＝旧バッチを除外）。
    - タイムスタンプを持たないファイルはバッチ判定不能なので除外せず温存する
      （誤って解析対象を失わない安全側）。
    - 全ファイルが無タイムスタンプなら全件そのまま返す（現状互換）。
    """
    if not paths:
        return []
    keyed = [(p, _batch_key(p)) for p in paths]
    timestamps = [k for _, k in keyed if k]
    if not timestamps:
        return list(paths)
    latest = max(timestamps)
    return [p for p, k in keyed if k == latest or not k]


def _describe_batch_selection(directory: Path) -> str | None:
    """フォルダ内に複数バッチが混在する場合、最新バッチを自動選択した旨の注記を返す。

    バッチが1つ（または判別不能）なら None を返し、注記を出さない。
    """
    try:
        names = [f.name for f in directory.iterdir() if f.is_file()]
    except OSError:
        return None
    # 解析対象は .arf/.arf2 のみ。プロジェクト/メタ(.mddata/.mdproject/.msp2)や
    # per-sample(.pai2/.dcl)も AlignmentResult 形式のタイムスタンプを持つため、
    # 拡張子で解析対象に限定しないと告知バッチが実際に解析する .arf バッチとズレる
    # （例: POS で .mddata の 2026_06_17 を告知するが解析 .arf は 2024_06_13）。
    # 正規化キー -> 表示用タイムスタンプ（アンダースコア付きの読みやすい形）
    display: dict[str, str] = {}
    for name in names:
        low = name.lower()
        if not (low.endswith(".arf") or low.endswith(".arf2")):
            continue
        match = _ALIGNMENT_TIMESTAMP_RE.search(name)
        if match:
            display[match.group(1).replace("_", "")] = match.group(1)
    if len(display) <= 1:
        return None
    latest_key = max(display)
    n_old = len(display) - 1
    return (
        f"🗂️ フォルダ内に複数バッチ（{len(display)} 件の処理タイムスタンプ）を検出しました。"
        f"最新バッチ **{display[latest_key]}** を自動選択し、旧バッチ {n_old} 件はスキップします。"
    )


# このサーバのパーサが読める拡張子。MS-DIAL の出力フォルダには測定生データ
# （.wiff / .wiff.scan / .wiff2 / .timeseries.data / .txt）が同居し、実データでは
# 495 ファイル中 7 割以上がそれだった。既定でそこまで列挙すると、入口ツールの
# 戻り値だけで 46,977 字（≒1万数千トークン）を占める。
ANALYSABLE_EXTENSIONS: tuple[str, ...] = (
    ".arf", ".arf2", ".pai2", ".dcl", ".EIC.aef", ".mddata", ".mdproject",
)


def list_data_files(
    extension: str | None = None,
    directory: str | None = None,
    all_files: bool = False,
) -> list[str]:
    """データディレクトリ内のファイルの絶対パス一覧を返す（純関数）。

    - directory: 探索するディレクトリ。省略時は既定のデータディレクトリ
      (環境変数 LIPIDMIX_DATA_DIR または <project>/data)。
    - extension: 指定するとその拡張子（例 '.pai2', '.arf2', '.EIC.aef'）だけに絞る。
    - all_files: extension 未指定のとき、解析対象外の拡張子まで含めるか。

    見つからない場合・ディレクトリが無い場合は**空リスト**を返す。以前はここで
    エラー文面を1要素のリストとして返していたため、呼び出し側5箇所が
    「メッセージをパスとして掴まない」よう `os.path.isfile` で防御していた。
    """
    target_dir = Path(directory).expanduser() if directory else mcp_core.DATA_DIR
    if not target_dir.is_dir():
        return []

    if extension is not None:
        suffixes: tuple[str, ...] = (extension,)
    elif all_files:
        suffixes = ()
    else:
        suffixes = ANALYSABLE_EXTENSIONS

    file_paths = []
    for file in target_dir.iterdir():
        if not file.is_file():
            continue
        if suffixes and not str(file).endswith(suffixes):
            continue
        file_paths.append(str(file.absolute()))
    return file_paths


def _resolve_data_file(
    extension: str,
    file_path: str | None = None,
    prefer_suffix: str | None = None,
) -> str | None:
    """拡張子から解析対象ファイルを1つ選ぶ（全リゾルバの共通実装）。

    明示パスがあればそれを優先し、無ければ (1) 複数バッチ混在なら最新バッチへ絞り、
    (2) `prefer_suffix` に一致するものがあればそちらを優先し、(3) 重複時は最新版を
    採る。この 3 段は形式によらず同じなので、拡張子と優先条件だけを変えて共有する。
    """
    if file_path and os.path.exists(file_path):
        return file_path

    real_paths = [p for p in list_data_files(extension=extension) if os.path.isfile(p)]
    if not real_paths:
        return None
    real_paths = _select_latest_batch(real_paths)  # 複数バッチ混在時は最新バッチへ
    if prefer_suffix:
        preferred = [p for p in real_paths if p.lower().endswith(prefer_suffix)]
        if preferred:
            return _pick_latest(preferred)
    return _pick_latest(real_paths)  # 重複時は最新版


def resolve_arf_file_path(file_path: str | None = None) -> str | None:
    """.arfファイルのパスを解決するヘルパー。

    MS-DIAL出力フォルダには DriftSpots.arf と PeakProperties.arf が併存しうるが、
    PCA等に使うサンプル別強度を持つのは **PeakProperties.arf** の方。両者がある場合は
    PeakProperties.arf を自動選択する（無ければ先頭にフォールバック）。
    """
    return _resolve_data_file(".arf", file_path, prefer_suffix="peakproperties.arf")


def resolve_arf2_file_path(file_path: str | None = None) -> str | None:
    """.arf2ファイルのパスを解決するヘルパー"""
    return _resolve_data_file(".arf2", file_path)


def resolve_eicaef_file_path(file_path: str | None = None) -> str | None:
    """EIC.aefファイルのパスを解決するヘルパー"""
    return _resolve_data_file(".EIC.aef", file_path)


def resolve_pai2_file_path(file_path: str | None = None) -> str | None:
    """.pai2ファイルのパスを解決するヘルパー。

    複数日付（複数バッチ）が混在していても最新バッチの .pai2 を自動選択する。
    """
    return _resolve_data_file(".pai2", file_path)


def resolve_dcl_file_path(file_path: str | None = None) -> str | None:
    """.dcl ファイルのパスを解決するヘルパー（MSDecResult / MS-MS 本体）。

    .dcl は測定ファイル1つにつき1個あるため、指定なしでは最新バッチの先頭を返す。
    特定サンプルの MS/MS が欲しいときは file_path を明示するか、.pai2 と同名の
    兄弟ファイルを引く dcl_reader.find_dcl_for_pai2 を使う。
    """
    return _resolve_data_file(".dcl", file_path)


def _filter_arf_spots(
    features: list[dict],
    min_intensity: float = 0.0,
    annotation_keyword: str | None = None,
) -> list[dict]:
    """Apply the same lightweight ARF filters used by arf_parser."""
    filtered_spots = []
    keyword = annotation_keyword.lower() if annotation_keyword else None
    for spot in features:
        height = spot.get("HeightAverage")
        if height is not None and height < min_intensity:
            continue

        if keyword:
            name = spot.get("Name", "")
            if not name or keyword not in name.lower():
                continue

        filtered_spots.append(spot)
    return filtered_spots
