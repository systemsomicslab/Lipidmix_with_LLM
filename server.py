from html import parser
import json
import os
import re
import sys
from datetime import date as _date
from pathlib import Path
import io
from mcp.server.fastmcp import FastMCP, Image
import matplotlib.pyplot as plt
import numpy as np
import pprint
import sys
import base64
import csv
import math
import pandas as pd

import arf_reader
import knowledge_store
import paper_ingest
import preprocessing
import differential
import lipid_identity
from msdial_classes import (
    assign_sample_groups,
    attach_class_ids_to_spots,
    discover_arf_class_index,
    filter_arf_by_class_ids,
)
from msdial_tags import (
    attach_tags_to_spots,
    discover_arf_tag_index,
    filter_arf_by_tags,
    normalize_sample_name,
)
from arf2_reader import (
    deserialize,
    summarize_arf2_data,
    generate_text_summary,
)
from eic_aef_reader import (
    parse_eic_aef_css1,
    summarize_eic_data,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
    top_eic_spots_by_peak_top,
)
from pai2_reader import (
    perform_pca_summary,
    filter_features_by_params,
    inspect_metabolite_details,
    get_top_contributors,
    get_signal_to_noise,
)
import peak_verification as pv

import mcp_core
from mcp_core import (
    mcp,
    BASE_DIR,
    OUTPUT_FORMAT_DOC,
    MCP_INSTRUCTIONS,
    KNOWLEDGE_DIR,
    PLAYBOOK_DIR,
    ANALYSES_DIR,
    _state_dir,
    _dir_is_writable,
    _first_writable_dir,
    _build_report_meta,
    _report_dir_candidates,
    _resolve_report_dir,
)
# DATA_DIR は load_dataset が実行時に差し替える可変状態。スナップショット束縛を避け、
# 参照は mcp_core.DATA_DIR（module 修飾・動的）で行う。


import tools_resources  # 6 リソースを import 副作用で mcp に登録


from tools_objective import *  # objective/文献探索ツール群 + 登録


from tools_reports import *  # レポート/図ツール群 + 登録


import session_state
from session_state import AnalysisSession, _build_sample_meta
# session は全ツール共有の可変シングルトン。参照は session_state.session（動的）で行い、
# スナップショット束縛（from session_state import session）は作らない。

import path_resolvers
from path_resolvers import (
    resolve_arf_file_path,
    resolve_arf2_file_path,
    resolve_eicaef_file_path,
    resolve_pai2_file_path,
    _select_latest_batch,
    _describe_batch_selection,
    _filter_arf_spots,
)
# list_data_files は path_resolvers の純関数を MCP ツールとして登録する（Phase 8 で
# tools_dataset へ移設）。同一関数オブジェクトなので resolve_* の直接呼び出しと一貫する。
list_data_files = mcp.tool()(path_resolvers.list_data_files)

import tool_helpers
from tool_helpers import (
    _identity_tables,
    _build_verification_dossier,
    _pca_scatter_arrays,
    _remember_arf_pca_plot,
    _format_pca_plot_block,
    _format_pca_loadings_md,
    _format_arf_tag_summary,
    _class_factors_by_position,
    _format_arf_class_summary,
    _format_arf_parse_summary,
    _format_arf_class_filter,
    _format_arf_tag_filter,
    _pp_build_matrix,
    _pp_has_preprocessed,
)


@mcp.tool()
def load_dataset(directory: str | None = None) -> list:
    """データフォルダを指定して、最初の標準解析（arf2 概観 → arf 詳細）を一括実行します。

    MS-DIAL出力フォルダを解析する際の **入口** です。フォルダのパスを渡すと:
    1. `.arf2`（データセット全体のカタログ＝概観）を要約し、
    2. サンプル別強度を持つ `.arf` で PCA を実行します。フォルダに DriftSpots.arf と
       PeakProperties.arf が併存する場合は、解析に使う **PeakProperties.arf を自動選択** します。
    以降の `arf_list_classes` / `arf_re_pca` 等はこのセッション状態をそのまま利用できます。

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
            return [f"データディレクトリが存在しません: {target_dir}"]
        if not target_dir.is_dir():
            return [f"指定されたパスはディレクトリではありません: {target_dir}"]
        mcp_core.DATA_DIR = target_dir  # 以降のツールの既定探索先を更新（正準は mcp_core 側）

    arf2_path = resolve_arf2_file_path()
    arf_path = resolve_arf_file_path()

    outputs: list = [
        f"## 📂 データセット読み込み: {mcp_core.DATA_DIR}\n"
        "標準の初期解析として **arf2（全体概観）→ arf（PeakProperties, サンプル別PCA）** を実行します。\n"
        "この出力（群構造・脂質クラス・極性など）は、解釈に進む前の『実験目的の推測とユーザー確認』"
        "（GATEWAY手順1）の材料になります。"
    ]

    batch_note = _describe_batch_selection(mcp_core.DATA_DIR)
    if batch_note:
        outputs.append(batch_note)

    if arf2_path:
        outputs.extend(arf2_parser(file_path=arf2_path))
    else:
        outputs.append("⚠️ .arf2 ファイルが見つかりませんでした（全体概観をスキップ）。")

    if arf_path:
        outputs.extend(arf_parser(file_path=arf_path))
    else:
        outputs.append(
            "⚠️ 解析対象の .arf（PeakProperties.arf 等）が見つかりませんでした。"
        )

    return outputs



from tools_pai2 import *  # PAI2/検証ツール群 + 登録






from tools_arf import *  # ARF ツール群 + 登録




from tools_arf2 import *  # ARF2 ツール群 + 登録
from tools_eic import *  # EIC/AEF ツール群 + 登録





if __name__ == "__main__":
    # 既定は stdio（ローカル開発: Claude がサブプロセスとして起動）。
    # NAS常駐では LIPIDMIX_TRANSPORT=streamable-http を設定し HTTP で待受ける。
    transport = os.environ.get("LIPIDMIX_TRANSPORT", "stdio")
    mcp.run(transport=transport)
