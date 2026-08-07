"""状態不足13箇所が missing_state エンベロープを返すことの契約テスト。

message は既存の日本語文面を保持する（エンベロープを解釈しないクライアントでも
LLM が読む内容が変わらないこと）。
"""
import json
import unittest

import server
import session_state


def envelope(raw):
    """本文がエンベロープならその error 部を返す。違えば None。"""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and error.get("code") == "missing_state":
        return error
    return None


class MissingStateContractTests(unittest.TestCase):
    """空セッションで各ツールを呼び、エンベロープが返ることを確認する。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()

    def assert_missing(self, raw, state, required_tools, message_contains):
        error = envelope(raw)
        self.assertIsNotNone(error, f"エンベロープでない: {raw[:200]}")
        self.assertEqual(error["state"], state)
        self.assertEqual(error["required_tools"], required_tools)
        self.assertIn(message_contains, error["message"])

    def test_arf_list_tags(self):
        self.assert_missing(
            server.arf_list_tags(), "arf_dataset", ["arf_parser", "load_dataset"], "タグファイル"
        )

    def test_arf_list_classes(self):
        self.assert_missing(
            server.arf_list_classes(), "arf_dataset", ["arf_parser", "load_dataset"], "ARFデータ"
        )

    def test_arf_list_sample_roles(self):
        self.assert_missing(
            server.arf_list_sample_roles(), "arf_dataset", ["arf_parser", "load_dataset"], "arf_parser"
        )

    def test_arf_exclude(self):
        self.assert_missing(
            server.arf_exclude(exclude_samples=["x"]), "arf_dataset", ["arf_parser", "load_dataset"], "arf_parser"
        )

    def test_arf_preprocess(self):
        self.assert_missing(
            server.arf_preprocess(), "arf_dataset", ["arf_parser", "load_dataset"], "arf_parser"
        )

    def test_arf_pca_preprocessed(self):
        self.assert_missing(
            server.arf_pca_preprocessed(),
            "preprocessed_matrix",
            ["arf_preprocess"],
            "前処理後の行列がありません",
        )

    def test_arf_differential(self):
        self.assert_missing(
            server.arf_differential(group_a="a", group_b="b"),
            "preprocessed_matrix",
            ["arf_preprocess"],
            "arf_preprocess",
        )

    def test_arf_plot_volcano_puts_the_envelope_in_the_exception(self):
        """戻り値が構造化ペイロードなので、ここだけ例外の本文にエンベロープを載せる。"""
        with self.assertRaises(ValueError) as ctx:
            server.arf_plot_volcano()
        error = envelope(str(ctx.exception))
        self.assertIsNotNone(error)
        self.assertEqual(error["state"], "differential_result")
        self.assertEqual(error["required_tools"], ["arf_differential"])

    def test_pai2_inspect_peak(self):
        self.assert_missing(
            server.pai2_inspect_peak(peak_name="PC 34:1"),
            "pai2_dataset",
            ["pai2_parser"],
            "pai2_parser",
        )

    def test_verify_peak_annotation(self):
        self.assert_missing(
            server.verify_peak_annotation(peak_name="PC 34:1"),
            "pai2_dataset",
            ["pai2_parser"],
            "pai2_parser",
        )

    def test_save_pca_figure(self):
        self.assert_missing(
            server.save_pca_figure(analysis_id="x"),
            "pca_result",
            ["arf_parser", "arf_pca_preprocessed", "load_dataset"],
            "PCA",
        )

    def test_save_volcano_figure(self):
        self.assert_missing(
            server.save_volcano_figure(analysis_id="x"),
            "differential_result",
            ["arf_differential"],
            "arf_differential",
        )

    def test_save_eic_figure_offers_both_producers(self):
        """eic_plot は2つのツールのどちらでも作れる（OR の代替候補）。"""
        self.assert_missing(
            server.save_eic_figure(analysis_id="x"),
            "eic_plot",
            ["eic_plot_chromatograms", "eic_plot_compounds"],
            "eic_plot_chromatograms",
        )


class SampleSearchIsNotAMissingStateTests(unittest.TestCase):
    """sample_search は ARF 未ロードでも動くのが要件。エンベロープ化してはいけない。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_sample_search_failure_is_not_an_envelope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as empty:
            raw = server.sample_search(directory=empty)
        self.assertIsNone(
            envelope(raw),
            "sample_search をエンベロープ化すると、ARF を必要としない検索のために "
            "arf_parser の自動リプレイが走ってしまう",
        )


if __name__ == "__main__":
    unittest.main()
