# tests/test_preprocess_tools.py
import json
import unittest
import numpy as np
import server
import session_state


class TestPreprocessTools(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_list_sample_roles_requires_data(self):
        out = json.loads(server.arf_list_sample_roles())
        self.assertEqual(out["status"], "error")

    def test_preprocess_sets_session_matrix(self):
        # minimal fake ARF matrix path: inject via monkeypatch of build helper
        session_state.session.filtered_features = [{"AlignedPeakProperties": []}]
        session_state.session.arf_class_index = None

        def fake_matrix(*a, **k):
            m = np.array([[10.0, 1.0], [20.0, 2.0], [15.0, 1.5]])
            return m, ["s1", "s2", "q1"], ["Spot_0_height", "Spot_1_height"]

        import tool_helpers
        # _pp_build_matrix の正準定義元は tool_helpers。arf_preprocess はそこを module 修飾で
        # 参照するため、差し替え・復元も tool_helpers 側で行う（他テストへの漏れを防ぐ）。
        _orig_build = tool_helpers._pp_build_matrix
        tool_helpers._pp_build_matrix = fake_matrix
        self.addCleanup(setattr, tool_helpers, "_pp_build_matrix", _orig_build)
        out = json.loads(server.arf_preprocess(normalize="median", impute="half_min"))
        self.assertEqual(out["status"], "success")
        self.assertIsNotNone(session_state.session.feature_matrix)
        self.assertEqual(session_state.session.preprocessing_recipe["normalize"], "median")


class TestPcaPreprocessed(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_requires_preprocessed_matrix(self):
        out = server.arf_pca_preprocessed()
        self.assertIn("前処理", out[0])

    def test_runs_pca_on_preprocessed_matrix(self):
        import numpy as np
        session_state.session.feature_matrix = np.array(
            [[1.0, 2.0, 3.0], [2.0, 1.0, 0.0], [3.0, 3.0, 3.0], [0.0, 1.0, 2.0]])
        session_state.session.pp_sample_names = ["a", "b", "c", "d"]
        session_state.session.pp_feature_names = ["Spot_0_height", "Spot_1_height", "Spot_2_height"]
        session_state.session.arf_class_index = None
        session_state.session.features = []  # get_pca_loading_features tolerates empty spots
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        self.assertTrue(server._pp_has_preprocessed())
        out = server.arf_pca_preprocessed()
        self.assertIn("PCA", out[0])
        self.assertIn("前処理レシピ", out[0])


if __name__ == "__main__":
    unittest.main()
