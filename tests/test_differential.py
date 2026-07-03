import math
import unittest

import numpy as np

import differential as diff


class TestBhFdr(unittest.TestCase):
    def test_monotone_and_bounded(self):
        q = diff.bh_fdr([0.001, 0.01, 0.02, 0.5])
        self.assertTrue(all(0.0 <= v <= 1.0 for v in q))
        # smallest p gets smallest q
        self.assertLessEqual(q[0], q[3])

    def test_nan_preserved(self):
        q = diff.bh_fdr([0.01, float("nan"), 0.02])
        self.assertTrue(math.isnan(q[1]))
        self.assertFalse(math.isnan(q[0]))


class TestTwoGroup(unittest.TestCase):
    def test_clear_difference_significant(self):
        # feature 0 differs strongly between A and B; feature 1 does not.
        matrix = np.array([
            [10.0, 5.0],  # A
            [11.0, 5.1],  # A
            [ 9.5, 4.9],  # A
            [50.0, 5.0],  # B
            [52.0, 5.2],  # B
            [48.0, 4.8],  # B
        ])
        labels = ["A", "A", "A", "B", "B", "B"]
        res = diff.two_group_test(matrix, ["f0", "f1"], labels, "A", "B")
        by_feat = {r["feature"]: r for r in res}
        self.assertLess(by_feat["f0"]["p"], 0.05)
        self.assertGreater(by_feat["f1"]["p"], 0.05)
        # log2fc = log2(mean_a / mean_b); f0 is much lower in A (~10) than B (50),
        # so a strong negative fold change is expected. (Plan convention: A>B => +.)
        self.assertLess(by_feat["f0"]["log2fc"], -1.0)

    def test_missing_group_member_gives_nan(self):
        matrix = np.array([[10.0], [np.nan]])
        res = diff.two_group_test(matrix, ["f0"], ["A", "B"], "A", "B")
        self.assertTrue(math.isnan(res[0]["p"]))


class TestAnova(unittest.TestCase):
    def test_three_group_difference(self):
        matrix = np.array([
            [1.0], [1.1], [0.9],     # G1
            [5.0], [5.1], [4.9],     # G2
            [9.0], [9.2], [8.8],     # G3
        ])
        labels = ["G1"] * 3 + ["G2"] * 3 + ["G3"] * 3
        res = diff.one_way_anova(matrix, ["f0"], labels)
        self.assertLess(res[0]["p"], 0.01)
        self.assertEqual(res[0]["n_groups"], 3)


class TestVolcanoAndConfound(unittest.TestCase):
    def test_add_fdr_and_volcano_flags(self):
        results = [
            {"feature": "f0", "log2fc": 2.0, "p": 0.001, "mean_a": 4, "mean_b": 1},
            {"feature": "f1", "log2fc": -3.0, "p": 0.002, "mean_a": 1, "mean_b": 8},
            {"feature": "f2", "log2fc": 0.1, "p": 0.9, "mean_a": 1, "mean_b": 1},
        ]
        withq = diff.add_fdr(results)
        self.assertIn("q", withq[0])
        pts = diff.volcano_data(withq, q_thr=0.05, log2fc_thr=1.0)
        flags = {p["feature"]: p["sig"] for p in pts}
        self.assertEqual(flags["f0"], "up")
        self.assertEqual(flags["f1"], "down")
        self.assertEqual(flags["f2"], "ns")

    def test_confounding_detected(self):
        groups = ["ctrl", "ctrl", "trt", "trt"]
        batches = ["d1", "d1", "d2", "d2"]  # group perfectly aligns with batch
        out = diff.check_confounding(groups, batches)
        self.assertTrue(out["confounded"])


if __name__ == "__main__":
    unittest.main()
