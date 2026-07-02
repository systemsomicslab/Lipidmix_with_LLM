# tests/test_preprocessing_session.py
import unittest
import server


class TestSampleMeta(unittest.TestCase):
    def test_build_sample_meta_roles_and_run_order(self):
        class_index = {
            "records": [
                {"file_id": 0, "file_name": "20240311_QC_Cerebellum_ICR_NEG_1",
                 "class_id": "QC", "analytical_order": 5},
                {"file_id": 1, "file_name": "20240311_Cerebellum_gf_AIN_1_NEG",
                 "class_id": "Cerebellum_gf_AIN", "analytical_order": 6},
            ],
            "by_file_id": {}, "by_file_name": {},
        }
        names = ["20240311_QC_Cerebellum_ICR_NEG_1",
                 "20240311_Cerebellum_gf_AIN_1_NEG"]
        meta = server._build_sample_meta(names, class_index)
        self.assertEqual(meta[names[0]]["role"], "qc")
        self.assertEqual(meta[names[1]]["role"], "sample")
        self.assertEqual(meta[names[1]]["run_order"], 6)
        self.assertEqual(meta[names[0]]["batch"], "20240311")

    def test_session_has_new_attrs(self):
        s = server.AnalysisSession()
        self.assertIsNone(s.feature_matrix)
        self.assertEqual(s.preprocessing_recipe, {})


if __name__ == "__main__":
    unittest.main()
