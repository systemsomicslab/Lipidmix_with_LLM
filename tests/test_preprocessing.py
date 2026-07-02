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
        # raw row medians are 3.0 and 15.0; factors is normalized by their
        # median, but the ratio between rows must be preserved.
        self.assertAlmostEqual(factors[0] / factors[1], 3.0 / 15.0)

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            pp.normalize(np.zeros((2, 2)), "bogus")

    def test_tic_factors_match_applied_divisor(self):
        m = np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 9.0]])
        out, factors, report = pp.normalize(m, "tic")
        np.testing.assert_allclose(out, m / factors[:, None])

    def test_median_factors_match_applied_divisor(self):
        m = np.array([[2.0, 4.0], [10.0, 20.0], [1.0, 7.0]])
        out, factors, report = pp.normalize(m, "median")
        np.testing.assert_allclose(out, m / factors[:, None])

    def test_pqn_uses_qc_reference_when_roles_provided(self):
        m = np.array([
            [2.0, 4.0],
            [4.0, 8.0],
            [1.0, 2.0],
        ])
        sample_names = ["s1_qc", "s2_qc", "s3_sample"]
        roles = pp.detect_sample_roles(sample_names)
        out, factors, report = pp.normalize(
            m, "pqn", roles=roles, sample_names=sample_names
        )
        self.assertEqual(report["pqn_reference"], "qc_median")

    def test_pqn_uses_all_sample_reference_without_roles(self):
        m = np.array([[2.0, 4.0], [4.0, 8.0], [1.0, 2.0]])
        out, factors, report = pp.normalize(m, "pqn")
        self.assertEqual(report["pqn_reference"], "all_sample_median")

    def test_pqn_handles_zero_in_reference_row_without_inf_or_crash(self):
        # column 1's reference (median) is 0.0, which would otherwise cause
        # a division-by-zero when computing quotients.
        m = np.array([[2.0, 0.0], [4.0, 0.0], [1.0, 3.0]])
        out, factors, report = pp.normalize(m, "pqn")
        # the zero-reference column becomes NaN (not inf), other columns stay finite.
        self.assertTrue(np.all(np.isfinite(out[:, 0])))
        self.assertFalse(np.any(np.isinf(out)))


class TestBlankFilter(unittest.TestCase):
    def test_background_feature_removed(self):
        # col0: sample >> blank (keep). col1: sample ~ blank (remove).
        m = np.array([
            [100.0, 10.0],  # sample
            [120.0, 11.0],  # sample
            [5.0, 9.0],     # blank
        ])
        roles = {"s1": "sample", "s2": "sample", "b1": "blank"}
        names = ["s1", "s2", "b1"]
        mask, report = pp.blank_filter(m, roles, names, min_fold=3.0)
        self.assertTrue(mask[0])
        self.assertFalse(mask[1])
        self.assertEqual(report["removed"], 1)

    def test_no_blank_keeps_all_with_caveat(self):
        m = np.array([[1.0, 2.0]])
        mask, report = pp.blank_filter(m, {"s1": "sample"}, ["s1"])
        self.assertTrue(mask.all())
        self.assertIn("caveat", report)


class TestDriftCorrect(unittest.TestCase):
    def test_linear_drift_flattened(self):
        # feature drifts linearly with run order across QC; correction should
        # bring QC intensities close to a constant level.
        order = [1, 2, 3, 4, 5, 6]
        names = [f"n{i}" for i in order]
        roles = {n: ("qc" if i % 2 == 1 else "sample") for n, i in zip(names, order)}
        run_order = dict(zip(names, order))
        base = np.array([o * 10.0 for o in order])  # 10,20,30,40,50,60
        m = base.reshape(-1, 1)
        out, report = pp.qc_drift_correct(m, roles, names, run_order, min_qc=3)
        qc_vals = [out[i, 0] for i, n in enumerate(names) if roles[n] == "qc"]
        self.assertLess(np.std(qc_vals), np.std([10, 30, 50]))
        self.assertEqual(report["status"], "applied")

    def test_no_run_order_skips(self):
        m = np.array([[1.0], [2.0]])
        names = ["a", "b"]
        roles = {"a": "qc", "b": "sample"}
        out, report = pp.qc_drift_correct(m, roles, names, {"a": None, "b": None})
        np.testing.assert_array_equal(out, m)
        self.assertEqual(report["status"], "skipped")


class TestQcRsdFilter(unittest.TestCase):
    def test_high_rsd_feature_removed(self):
        # col0: stable in QC (keep). col1: highly variable in QC (remove).
        m = np.array([
            [100.0, 10.0],   # qc
            [101.0, 90.0],   # qc
            [ 99.0, 50.0],   # qc
            [ 50.0, 40.0],   # sample (ignored for RSD)
        ])
        names = ["q1", "q2", "q3", "s1"]
        roles = {"q1": "qc", "q2": "qc", "q3": "qc", "s1": "sample"}
        mask, report = pp.qc_rsd_filter(m, roles, names, max_rsd=0.30)
        self.assertTrue(mask[0])
        self.assertFalse(mask[1])

    def test_no_qc_keeps_all_with_caveat(self):
        m = np.array([[1.0, 2.0]])
        mask, report = pp.qc_rsd_filter(m, {"s1": "sample"}, ["s1"])
        self.assertTrue(mask.all())
        self.assertIn("caveat", report)


class TestImpute(unittest.TestCase):
    def test_half_min_fills_per_feature(self):
        m = np.array([[10.0, np.nan], [20.0, 4.0], [30.0, 8.0]])
        out, report = pp.impute(m, "half_min")
        self.assertAlmostEqual(out[0, 1], 2.0)  # half of column-1 min (4) = 2
        self.assertFalse(np.isnan(out).any())

    def test_none_keeps_nan(self):
        m = np.array([[np.nan, 1.0]])
        out, report = pp.impute(m, "none")
        self.assertTrue(np.isnan(out[0, 0]))


if __name__ == "__main__":
    unittest.main()
