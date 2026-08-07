"""全ツールが MCP 標準 annotations を宣言していることの回帰テスト。

汎用 MCP クライアントは annotations だけを見て承認要否とリプレイ安全性を決める。
annotations の無いツールはクライアント側で UNKNOWN 扱いになり、毎回承認待ちで
止まる。新しいツールを足したらここにも足すこと。

readOnlyHint の解釈: 「サーバの外に副作用が無い」＝ファイルとネットワークを
変更しない。サーバ自身の解析セッション状態の更新は副作用に数えない
（セッション状態の依存は missing_state エンベロープで伝えるため、
annotations で二重に表現しない）。
"""
import asyncio
import unittest

import server

READ_ONLY = {"readOnlyHint": True}
EXTERNAL = {"readOnlyHint": True, "openWorldHint": True}
LOCAL_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
STAGE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}

EXPECTED_ANNOTATIONS = {
    # --- ARF（多サンプル解析）。セッション状態は更新するが外部副作用は無い ---
    "arf_list_tags": READ_ONLY,
    "arf_list_classes": READ_ONLY,
    "arf_list_sample_roles": READ_ONLY,
    "arf_exclude": READ_ONLY,
    "arf_preprocess": READ_ONLY,
    "arf_pca_preprocessed": READ_ONLY,
    "arf_parser": READ_ONLY,
    "arf_differential": READ_ONLY,
    "arf_plot_volcano": READ_ONLY,
    "arf2_parser": READ_ONLY,
    "arf2_annotate_identities": READ_ONLY,
    # --- データセット入口 ---
    "list_data_files": READ_ONLY,
    "load_dataset": READ_ONLY,
    # --- DCL / PAI2 / EIC ---
    "dcl_parser": READ_ONLY,
    "dcl_find_msms": READ_ONLY,
    "pai2_parser": READ_ONLY,
    "pai2_inspect_peak": READ_ONLY,
    "verify_peak_annotation": READ_ONLY,
    "eic_parser": READ_ONLY,
    "eic_plot_chromatograms": READ_ONLY,
    "eic_plot_compounds": READ_ONLY,
    "eic_rank_by_max_intensity": READ_ONLY,
    "eic_search_by_mz_range": READ_ONLY,
    "eic_search_by_rt_range": READ_ONLY,
    # --- 読み取り系 ---
    "log_search": READ_ONLY,
    "knowledge_coverage": READ_ONLY,
    "read_report": READ_ONLY,
    "list_reports": READ_ONLY,
    "sample_search": READ_ONLY,
    "ingest_review_queue": READ_ONLY,
    # --- 外部ネットワーク ---
    "paper_search": EXTERNAL,
    # --- ローカル書き出し（同じ引数なら同じパスへ上書き＝idempotent） ---
    "record_objective": LOCAL_WRITE,
    "update_objective": LOCAL_WRITE,
    "write_report": LOCAL_WRITE,
    "save_pca_figure": LOCAL_WRITE,
    "save_volcano_figure": LOCAL_WRITE,
    "save_eic_figure": LOCAL_WRITE,
    # --- knowledge の変更 ---
    "ingest_stage": STAGE,
    "ingest_promote": DESTRUCTIVE,
    "ingest_reject": DESTRUCTIVE,
}


def actual_annotations():
    tools = asyncio.run(server.mcp.list_tools())
    out = {}
    for tool in tools:
        ann = tool.annotations
        out[tool.name] = (
            None
            if ann is None
            else ann.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
    return out


class ToolAnnotationTests(unittest.TestCase):
    def test_expectation_covers_every_registered_tool(self):
        self.assertEqual(set(actual_annotations()), set(EXPECTED_ANNOTATIONS))

    def test_every_tool_declares_annotations(self):
        missing = [name for name, ann in actual_annotations().items() if not ann]
        self.assertEqual(missing, [], f"annotations 未宣言のツール: {missing}")

    def test_annotations_match_the_expected_table(self):
        actual = actual_annotations()
        for name, expected in EXPECTED_ANNOTATIONS.items():
            self.assertEqual(actual[name], expected, name)

    def test_external_network_tool_also_declares_open_world(self):
        """openWorldHint が無いと汎用クライアントがネットワークゲートを迂回する。"""
        self.assertIs(actual_annotations()["paper_search"]["openWorldHint"], True)


if __name__ == "__main__":
    unittest.main()
