"""データファイルのパス解決とバッチ選択（ファイル名の処理タイムスタンプ基準）。

依存は mcp_core（DATA_DIR）と stdlib のみの下位レイヤ。tools_* / server は import
しない。`list_data_files` はここに純関数として置き、MCP ツールとしての登録は上位
（tools_dataset）が担う — こうすることで resolve_* → list_data_files → DATA_DIR という
参照が下位で閉じ、tools_dataset との循環を避けられる。

DATA_DIR は実行時に差し替わるため `mcp_core.DATA_DIR` を動的参照する。
"""
import os
from pathlib import Path

import mcp_core

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


def list_data_files(extension: str | None = None, directory: str | None = None) -> list[str]:
    """
    指定したディレクトリ内にあるファイルパスの一覧を取得します。
    - directory: 探索するディレクトリのパス。省略時は既定のデータディレクトリ
      (環境変数 LIPIDMIX_DATA_DIR または <project>/data) を使用します。
    - extension: 指定された場合は、その拡張子(例: '.pai2', '.arf2', '.EIC.aef')のファイルのみをフィルタします。
    """
    target_dir = Path(directory).expanduser() if directory else mcp_core.DATA_DIR

    if not target_dir.exists():
        return [f"データディレクトリが存在しません: {target_dir}"]
    if not target_dir.is_dir():
        return [f"指定されたパスはディレクトリではありません: {target_dir}"]

    file_paths = []
    for file in target_dir.iterdir():
        if file.is_file():
            if extension is None or str(file).endswith(extension):
                file_paths.append(str(file.absolute()))

    if not file_paths:
        return [f"条件に一致するファイルが存在しません。 (ディレクトリ: {target_dir}, 拡張子: {extension})"]

    return file_paths


def resolve_arf_file_path(file_path: str | None = None) -> str | None:
    """.arfファイルのパスを解決するヘルパー。

    MS-DIAL出力フォルダには DriftSpots.arf と PeakProperties.arf が併存しうるが、
    PCA等に使うサンプル別強度を持つのは **PeakProperties.arf** の方。両者がある場合は
    PeakProperties.arf を自動選択する（無ければ先頭にフォールバック）。
    """
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".arf")
    if not file_paths or not isinstance(file_paths, list):
        return None
    # list_data_files はファイル不在時にエラーメッセージ文字列を1要素で返すため、
    # 実在するファイルパスだけに絞る（メッセージをパスとして掴まないように）。
    real_paths = [p for p in file_paths if os.path.isfile(p)]
    if not real_paths:
        return None
    # 複数日付（複数バッチ）が混在していれば最新バッチに絞る。
    real_paths = _select_latest_batch(real_paths)
    # 重複（旧版/新版）があれば最新版を選ぶ。PeakProperties を優先したうえで最新を採用。
    preferred = [p for p in real_paths if p.lower().endswith("peakproperties.arf")]
    if preferred:
        return _pick_latest(preferred)
    return _pick_latest(real_paths)


def resolve_arf2_file_path(file_path: str | None = None) -> str | None:
    """.arf2ファイルのパスを解決するヘルパー"""
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".arf2")
    if not file_paths or not isinstance(file_paths, list):
        return None
    real_paths = [p for p in file_paths if os.path.isfile(p)]
    if not real_paths:
        return None
    real_paths = _select_latest_batch(real_paths)  # 複数バッチ混在時は最新バッチへ
    return _pick_latest(real_paths)  # 重複時は最新版


def resolve_eicaef_file_path(file_path: str | None = None) -> str | None:
    """EIC.aefファイルのパスを解決するヘルパー"""
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".EIC.aef")
    if not file_paths or not isinstance(file_paths, list):
        return None
    real_paths = [p for p in file_paths if os.path.isfile(p)]
    if not real_paths:
        return None
    real_paths = _select_latest_batch(real_paths)  # 複数バッチ混在時は最新バッチへ
    return _pick_latest(real_paths)  # 重複時は最新版


def resolve_pai2_file_path(file_path: str | None = None) -> str | None:
    """.pai2ファイルのパスを解決するヘルパー。

    複数日付（複数バッチ）が混在していても最新バッチの .pai2 を自動選択する。
    """
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".pai2")
    if not file_paths or not isinstance(file_paths, list):
        return None
    real_paths = [p for p in file_paths if os.path.isfile(p)]
    if not real_paths:
        return None
    real_paths = _select_latest_batch(real_paths)  # 複数バッチ混在時は最新バッチへ
    return _pick_latest(real_paths)  # 重複時は最新版


def _filter_arf_spots(
    features: list[dict],
    min_intensity: float = 0.0,
    annotation_keyword: str | None = None,
) -> list[dict]:
    """Apply the same lightweight ARF filters used by arf_re_pca."""
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
