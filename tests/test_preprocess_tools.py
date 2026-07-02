# tests/test_preprocess_tools.py
import json
import unittest
import numpy as np
import server


class TestPreprocessTools(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_list_sample_roles_requires_data(self):
        out = json.loads(server.arf_list_sample_roles())
        self.assertEqual(out["status"], "error")

    def test_preprocess_sets_session_matrix(self):
        # minimal fake ARF matrix path: inject via monkeypatch of build helper
        server.session.filtered_features = [{"AlignedPeakProperties": []}]
        server.session.arf_class_index = None

        def fake_matrix(*a, **k):
            m = np.array([[10.0, 1.0], [20.0, 2.0], [15.0, 1.5]])
            return m, ["s1", "s2", "q1"], ["Spot_0_height", "Spot_1_height"]

        server._pp_build_matrix = fake_matrix  # helper indirection (see Step 3)
        out = json.loads(server.arf_preprocess(normalize="median", impute="half_min"))
        self.assertEqual(out["status"], "success")
        self.assertIsNotNone(server.session.feature_matrix)
        self.assertEqual(server.session.preprocessing_recipe["normalize"], "median")


class TestPcaPreprocessed(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_preprocessed_matrix(self):
        out = server.arf_pca_preprocessed()
        self.assertIn("前処理", out[0])

    def test_runs_pca_on_preprocessed_matrix(self):
        import numpy as np
        server.session.feature_matrix = np.array(
            [[1.0, 2.0, 3.0], [2.0, 1.0, 0.0], [3.0, 3.0, 3.0], [0.0, 1.0, 2.0]])
        server.session.pp_sample_names = ["a", "b", "c", "d"]
        server.session.pp_feature_names = ["Spot_0_height", "Spot_1_height", "Spot_2_height"]
        server.session.arf_class_index = None
        server.session.features = []  # get_pca_loading_features tolerates empty spots
        server.session.preprocessing_recipe = {"normalize": "median"}
        self.assertTrue(server._pp_has_preprocessed())
        out = server.arf_pca_preprocessed()
        self.assertIn("PCA", out[0])
        self.assertIn("前処理レシピ", out[0])


if __name__ == "__main__":
    unittest.main()
