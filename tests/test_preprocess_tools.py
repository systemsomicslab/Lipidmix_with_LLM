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


if __name__ == "__main__":
    unittest.main()
