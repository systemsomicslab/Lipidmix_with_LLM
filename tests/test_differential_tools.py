import json
import unittest

import numpy as np

import server


class TestArfDifferential(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_matrix(self):
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["status"], "error")

    def test_two_group_reports_significant_and_caveats(self):
        server.session.feature_matrix = np.array([
            [10.0, 5.0], [11.0, 5.1], [9.5, 4.9],
            [50.0, 5.0], [52.0, 5.2], [48.0, 4.8],
        ])
        names = ["a1", "a2", "a3", "b1", "b2", "b3"]
        server.session.pp_sample_names = names
        server.session.pp_feature_names = ["f0", "f1"]
        server.session.preprocessing_recipe = {"normalize": "median"}
        server.session.sample_meta = {
            n: {"group": ("A" if n.startswith("a") else "B"),
                "batch": ("d1" if n.startswith("a") else "d2")}
            for n in names
        }
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["status"], "success")
        self.assertGreaterEqual(out["summary"]["n_significant"], 1)
        self.assertTrue(any("バッチ" in c or "交絡" in c for c in out["caveats"]))


class TestArfDifferentialDegenerate(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def _prime(self, groups, matrix=None):
        n = len(groups)
        if matrix is None:
            matrix = np.arange(1, n * 2 + 1, dtype=float).reshape(n, 2)
        server.session.feature_matrix = matrix
        names = [f"s{i}" for i in range(n)]
        server.session.pp_sample_names = names
        server.session.pp_feature_names = ["f0", "f1"]
        server.session.preprocessing_recipe = {"normalize": "median"}
        server.session.sample_meta = {names[i]: {"group": groups[i]} for i in range(n)}

    def test_empty_requested_group_is_flagged(self):
        self._prime(["A", "A", "A"])  # no B members at all
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["status"], "success")
        self.assertTrue(any("B" in c for c in out["caveats"]),
                        f"expected caveat naming empty group B, got {out['caveats']}")

    def test_zero_testable_features_is_flagged(self):
        # A and B each have 2 members but all values identical -> zero variance ->
        # every feature p=NaN -> n_tested 0. Must warn, not read as "no differences".
        self._prime(["A", "A", "B", "B"], matrix=np.ones((4, 2)))
        out = json.loads(server.arf_differential(group_a="A", group_b="B"))
        self.assertEqual(out["summary"]["n_tested"], 0)
        self.assertTrue(any("検定" in c for c in out["caveats"]),
                        f"expected zero-tested caveat, got {out['caveats']}")


class TestSaveVolcano(unittest.TestCase):
    def setUp(self):
        server.session = server.AnalysisSession()

    def test_requires_last_differential(self):
        out = server.save_volcano_figure("A1")
        self.assertIn("error", out.lower())

    def test_writes_png(self):
        server.session.last_differential = {
            "kind": "two_group", "a": "A", "b": "B",
            "volcano": [
                {"feature": "f0", "log2fc": 2.0, "neg_log10_p": 3.0, "sig": "up"},
                {"feature": "f1", "log2fc": -2.0, "neg_log10_p": 3.0, "sig": "down"},
                {"feature": "f2", "log2fc": 0.0, "neg_log10_p": 0.1, "sig": "ns"},
            ],
        }
        rel = server.save_volcano_figure("A1")
        self.assertIn("volcano", rel)


if __name__ == "__main__":
    unittest.main()
