import unittest

import interp_eval as ie


class TestTranscriptText(unittest.TestCase):
    def test_renders_tool_calls_and_final(self):
        conversation = [
            {"role": "user", "content": "PCAして"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca",
                                          "arguments": {"top_features": 10}}}]},
            {"role": "tool", "content": "PCA_RESULT", "tool_name": "arf_re_pca"},
            {"role": "assistant", "content": "PC1が分離"},
        ]
        out = ie.transcript_text(conversation, "PC1が分離")
        self.assertIn("arf_re_pca", out)
        self.assertIn("PCA_RESULT", out)
        self.assertIn("FINAL", out)
        self.assertIn("PC1が分離", out)


class TestScoreAggregate(unittest.TestCase):
    def _scores(self):
        full = {ax: 2 for ax in ie.AXES}   # 5軸合計=10
        half = {ax: 1 for ax in ie.AXES}   # 5軸合計=5
        return [
            {"case_id": "pca_neg", "phase_label": "PCA", "model": "azure", "axes": full},
            {"case_id": "qc_neg", "phase_label": "QC", "model": "azure", "axes": full},
            {"case_id": "pca_neg", "phase_label": "PCA", "model": "hybrid", "axes": half},
            {"case_id": "qc_neg", "phase_label": "QC", "model": "hybrid", "axes": half},
        ]

    def test_by_model_axis_mean(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["by_model"]["azure"]["accuracy"], 2.0)
        self.assertEqual(agg["by_model"]["hybrid"]["accuracy"], 1.0)

    def test_overall_mean_total(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["overall"]["azure"], 10.0)
        self.assertEqual(agg["overall"]["hybrid"], 5.0)

    def test_by_phase_mean_total(self):
        agg = ie.score_aggregate(self._scores(), ["azure", "hybrid"])
        self.assertEqual(agg["by_phase"]["PCA"]["azure"], 10.0)
        self.assertEqual(agg["by_phase"]["QC"]["hybrid"], 5.0)


if __name__ == "__main__":
    unittest.main()
