import unittest
import numpy as np
import preprocessing as pp


class TestDetectSampleRoles(unittest.TestCase):
    def test_qc_and_blank_detected_from_filename(self):
        names = [
            "20240311_QC_Cerebellum_ICR_NEG_1",
            "20240311_blank_Cerebellum_NEG_1",
            "20240311_Cerebellum_gf_AIN_1_NEG",
        ]
        roles = pp.detect_sample_roles(names)
        self.assertEqual(roles[names[0]], "qc")
        self.assertEqual(roles[names[1]], "blank")
        self.assertEqual(roles[names[2]], "sample")

    def test_role_from_class_id_token(self):
        names = ["s1"]
        roles = pp.detect_sample_roles(names, class_ids={"s1": "QC_pool"})
        self.assertEqual(roles["s1"], "qc")

    def test_config_overrides_tokens(self):
        names = ["ctrl_pooledqc_1"]
        roles = pp.detect_sample_roles(names, config={"qc_tokens": ["pooledqc"]})
        self.assertEqual(roles["ctrl_pooledqc_1"], "qc")


class TestNormalize(unittest.TestCase):
    def test_none_is_identity(self):
        m = np.array([[1.0, 2.0], [3.0, 4.0]])
        out, factors, report = pp.normalize(m, "none")
        np.testing.assert_array_equal(out, m)
        self.assertEqual(report["method"], "none")

    def test_tic_divides_by_row_sum(self):
        m = np.array([[1.0, 1.0], [2.0, 2.0]])
        out, factors, report = pp.normalize(m, "tic")
        # each row sums to 2 and 4 -> scaled so row sums equal the mean row sum (3)
        self.assertAlmostEqual(out[0, 0] / out[1, 0], (1 / 2) / (2 / 4))

    def test_median_uses_row_median(self):
        m = np.array([[2.0, 4.0], [10.0, 20.0]])
        out, factors, report = pp.normalize(m, "median")
        self.assertAlmostEqual(factors[0], 3.0)   # median(2,4)=3
        self.assertAlmostEqual(factors[1], 15.0)  # median(10,20)=15

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            pp.normalize(np.zeros((2, 2)), "bogus")


if __name__ == "__main__":
    unittest.main()
