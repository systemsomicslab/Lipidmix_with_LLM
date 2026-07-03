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


if __name__ == "__main__":
    unittest.main()
