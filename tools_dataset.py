"""データセット入口ツール: list_data_files, load_dataset。

load_dataset は arf2 概観 → arf PCA を一括実行するオーケストレータ。arf/arf2 ツールを
呼ぶため tools_arf / tools_arf2 に依存する（依存が最も深く、facade では最後に読み込む）。
DATA_DIR の差し替えは正準の mcp_core.DATA_DIR に対して行う。
"""
from pathlib import Path

import mcp_core
import path_resolvers
import session_state
from mcp.types import ToolAnnotations
from mcp_core import mcp
from path_resolvers import (
    resolve_arf_file_path,
    resolve_arf2_file_path,
    _describe_batch_selection,
)
from tools_arf import arf_parser
from tools_arf2 import arf2_parser

__all__ = ["list_data_files", "load_dataset"]

# list_data_files は path_resolvers の純関数を MCP ツールとして登録する（同一関数
# オブジェクトなので resolve_* からの直接呼び出しと一貫する）。
list_data_files = mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(
    path_resolvers.list_data_files
)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def load_dataset(directory: str | None = None) -> str:
    """データフォルダを指定して、最初の標準解析（arf2 概観 → arf 詳細）を一括実行します。

    MS-DIAL出力フォルダを解析する際の **入口** です。フォルダのパスを渡すと:
    1. `.arf2`（データセット全体のカタログ＝概観）を要約し、
    2. サンプル別強度を持つ `.arf` で PCA を実行します。フォルダに DriftSpots.arf と
       PeakProperties.arf が併存する場合は、解析に使う **PeakProperties.arf を自動選択** します。
    以降の `arf_list_classes` / `arf_parser` 等はこのセッション状態をそのまま利用できます。

    複数日付（複数回のMS-DIAL処理＝複数バッチ）のファイルが混在していても解析は
    止まりません。ファイル名の `AlignmentResult_<timestamp>` を見て **最新バッチを
    自動選択** し、選択結果を出力に明示します（旧バッチはスキップ）。

    - directory: MS-DIAL出力フォルダのパス。省略時は既定のデータディレクトリ
      (環境変数 LIPIDMIX_DATA_DIR または <project>/data) を使用します。
      明示した場合は以降のツールの既定探索先もこのフォルダに更新されます。
    """
    if directory:
        target_dir = Path(directory).expanduser()
        if not target_dir.exists():
            return f"データディレクトリが存在しません: {target_dir}"
        if not target_dir.is_dir():
            return f"指定されたパスはディレクトリではありません: {target_dir}"
        mcp_core.DATA_DIR = target_dir  # 以降のツールの既定探索先を更新（正準は mcp_core 側）

    arf2_path = resolve_arf2_file_path()
    arf_path = resolve_arf_file_path()

    blocks: list[str] = [
        f"## 📂 データセット読み込み: {mcp_core.DATA_DIR}\n"
        "標準の初期解析として **arf2（全体概観）→ arf（PeakProperties, サンプル別PCA）** を実行します。\n"
        "この出力（群構造・脂質クラス・極性など）は、解釈に進む前の『実験目的の推測とユーザー確認』"
        "（GATEWAY手順1）の材料になります。"
    ]

    # 意味論ダイジェストは入口の先頭で1回だけ前置する。ここで発火させておくと、
    # 後段で呼ぶ arf2_parser / arf_parser 内の同ガードは caveat_emitted により
    # no-op になり、ダイジェストが arf2 ブロック内へ埋没するのを防げる。
    blocks[0] = session_state.session.maybe_prepend_caveat(blocks[0])

    batch_note = _describe_batch_selection(mcp_core.DATA_DIR)
    if batch_note:
        blocks.append(batch_note)

    if arf2_path:
        blocks.append(arf2_parser(file_path=arf2_path))
    else:
        blocks.append("⚠️ .arf2 ファイルが見つかりませんでした（全体概観をスキップ）。")

    if arf_path:
        blocks.append(arf_parser(file_path=arf_path))
    else:
        blocks.append(
            "⚠️ 解析対象の .arf（PeakProperties.arf 等）が見つかりませんでした。"
        )

    return "\n\n".join(blocks)
