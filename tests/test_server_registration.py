"""特性化テスト（安全網）: server の MCP 登録面を固定する。

server.py の分割リファクタ中、ツール/リソースの登録漏れや名前変化を即検出するための
スナップショット。挙動は変えない前提なので、ここが赤くなったら「外形が壊れた」合図。
`import server` が例外を出さないこと自体も回帰対象（循環 import 等の早期検出）。
"""
import asyncio

import server


# MCP 登録ツールの正準スナップショット（sorted）。arf_exclude 追加で 36 件。
EXPECTED_TOOLS = sorted([
    "arf2_annotate_identities",
    "arf2_parser",
    "arf_differential",
    "arf_exclude",
    "arf_list_classes",
    "arf_list_sample_roles",
    "arf_list_tags",
    "arf_parser",
    "arf_pca_preprocessed",
    "arf_preprocess",
    "arf_re_pca",
    "eicaef_parser",
    "eicaef_search_by_mz_range",
    "eicaef_search_by_rt_range",
    "eicaef_top_peak_tops",
    "ingest_promote",
    "ingest_reject",
    "ingest_review_queue",
    "ingest_stage",
    "knowledge_coverage",
    "list_data_files",
    "list_reports",
    "load_dataset",
    "log_search",
    "pai2_get_top_metabolites",
    "pai2_inspect_metabolite_details",
    "pai2_parser",
    "pai2_update_analysis_filter",
    "paper_search",
    "read_report",
    "record_objective",
    "save_pca_figure",
    "save_volcano_figure",
    "update_objective",
    "verify_peak_annotation",
    "write_report",
])

EXPECTED_RESOURCES = sorted([
    "lipidmix://docs/output-format",
    "lipidmix://knowledge/index",
    "lipidmix://playbook/index",
    "lipidmix://knowledge/inbox",
])

EXPECTED_TEMPLATES = sorted([
    "lipidmix://knowledge/expand/{slug}",
    "lipidmix://playbook/expand/{slug}",
])


def test_tool_count_is_stable():
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 36


def test_tool_names_snapshot():
    tools = asyncio.run(server.mcp.list_tools())
    assert sorted(t.name for t in tools) == EXPECTED_TOOLS


def test_static_resources_snapshot():
    resources = asyncio.run(server.mcp.list_resources())
    assert sorted(str(r.uri) for r in resources) == EXPECTED_RESOURCES


def test_resource_templates_snapshot():
    templates = asyncio.run(server.mcp.list_resource_templates())
    assert sorted(t.uriTemplate for t in templates) == EXPECTED_TEMPLATES


def test_import_server_does_not_raise():
    # import server 自体が副作用で例外を出さないことの回帰（循環 import の早期検出）。
    import importlib

    importlib.reload(server)
