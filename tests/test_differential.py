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


if __name__ == "__main__":
    unittest.main()
