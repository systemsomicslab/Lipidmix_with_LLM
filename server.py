"""ms-data-parser MCP サーバのファサード。

実体は機能別モジュールへ分割されている（依存の浅い順）:
    mcp_core        … FastMCP インスタンス・設定・状態ディレクトリ・レポート先解決
    session_state   … AnalysisSession と session シングルトン
    path_resolvers  … データファイルのパス解決・バッチ選択
    tool_helpers    … 整形/identity/検証ドシエ/前処理行列ヘルパ
    tools_resources … @mcp.resource ×6
    tools_objective / tools_reports / tools_pai2 / tools_arf / tools_arf2 /
    tools_eic / tools_dataset … @mcp.tool 群

このファイルは薄い層に徹する:
  (1) 各モジュールを import して mcp にツール/リソースを登録する
  (2) テスト・外部が参照する公開面（server.<tool> / server.<helper> / server.mcp /
      server.arf_reader / server.AnalysisSession / server.KNOWLEDGE_DIR 等）を再エクスポートする
  (3) __main__ で mcp.run() する

可変状態（DATA_DIR / KNOWLEDGE_DIR / ANALYSES_DIR / session）の**正準**は mcp_core /
session_state 側にある。ここでの再エクスポートは読み取り用の束縛にすぎないため、
差し替えは必ず正準モジュール（mcp_core.DATA_DIR / mcp_core.KNOWLEDGE_DIR /
mcp_core.ANALYSES_DIR / session_state.session）に対して行うこと。
"""
import os

# patch.object(server.arf_reader, ...) が共有 module 経由で効くよう、module を公開する。
import arf_reader

# --- 設定・状態・ヘルパの再エクスポート（読み取り用） ---
from mcp_core import (
    mcp,
    BASE_DIR,
    OUTPUT_FORMAT_DOC,
    MCP_INSTRUCTIONS,
    KNOWLEDGE_DIR,
    PLAYBOOK_DIR,
    ANALYSES_DIR,
    _build_report_meta,
    _dir_is_writable,
    _first_writable_dir,
)
from session_state import AnalysisSession, _build_sample_meta
from path_resolvers import (
    resolve_arf_file_path,
    resolve_arf2_file_path,
    resolve_eicaef_file_path,
    resolve_pai2_file_path,
    _select_latest_batch,
    _describe_batch_selection,
    _filter_arf_spots,
)
from tool_helpers import (
    _identity_tables,
    _build_verification_dossier,
    _pca_scatter_arrays,
    _remember_arf_pca_plot,
    _pp_build_matrix,
    _pp_has_preprocessed,
)

# --- ツール/リソースの登録＋公開面の再エクスポート ---
# import 副作用で @mcp.tool / @mcp.resource が mcp に登録される。star import は各
# モジュールの __all__（＝そのモジュールのツール名）だけを取り込む。tools_dataset は
# arf/arf2 に依存するため最後に読み込む。
import tools_resources  # 6 リソース
from tools_objective import *
from tools_reports import *
from tools_pai2 import *
from tools_arf import *
from tools_arf2 import *
from tools_eic import *
from tools_dataset import *  # list_data_files, load_dataset


if __name__ == "__main__":
    # 既定は stdio（ローカル開発: Claude がサブプロセスとして起動）。
    # NAS常駐では LIPIDMIX_TRANSPORT=streamable-http を設定し HTTP で待受ける。
    transport = os.environ.get("LIPIDMIX_TRANSPORT", "stdio")
    mcp.run(transport=transport)
