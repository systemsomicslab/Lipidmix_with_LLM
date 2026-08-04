"""MCP ツールレベルの因子トークン挙動（sample_factors 純関数は test_sample_factors.py）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import server
import session_state
from tests.test_server_class_filter import (
    fake_build_pca_matrix,
    fake_extract_peak_properties,
    fake_run_pca,
)


class DifferentialByNameTokenTests(unittest.TestCase):
    """Class ID は処置だけ、時点はサンプル名にしかない構成で時点を揃えた2群比較。"""

    def setUp(self):
        session_state.session = server.AnalysisSession()
        names = [
            "20220902_RAW_ILG_6h_1_NEG", "20220902_RAW_ILG_6h_2_NEG",
            "20220902_RAW_ILG_6h_3_NEG",
            "20220901_RAW_control_6h_1_NEG", "20220901_RAW_control_6h_2_NEG",
            "20220901_RAW_control_6h_3_NEG",
            "20220902_RAW_ILG_0h_1_NEG", "20220901_RAW_control_0h_1_NEG",
        ]
        session_state.session.feature_matrix = np.array([
            [50.0, 5.0], [52.0, 5.1], [48.0, 4.9],
            [10.0, 5.0], [11.0, 5.2], [9.5, 4.8],
            [30.0, 5.0], [30.0, 5.0],
        ])
        session_state.session.pp_sample_names = names
        session_state.session.pp_feature_names = ["Spot_0_height", "Spot_1_height"]
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        session_state.session.sample_meta = {
            n: {"group": ("ILG" if "ILG" in n else "control"),
                "role": "sample", "batch": "d1"}
            for n in names
        }

    def test_time_matched_two_group_comparison(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["n_a"], 3)
        self.assertEqual(out["n_b"], 3)
        self.assertEqual(out["summary"]["n_significant"], 1)

    def test_payload_lists_resolved_samples(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        self.assertEqual(out["resolved_samples"]["group_a"], [
            "20220902_RAW_ILG_6h_1_NEG",
            "20220902_RAW_ILG_6h_2_NEG",
            "20220902_RAW_ILG_6h_3_NEG",
        ])
        self.assertTrue(any("比較サンプル" in c for c in out["caveats"]))

    def test_0h_samples_are_not_pooled_in(self):
        out = json.loads(server.arf_differential(group_a="ILG_6h", group_b="control_6h"))
        joined = " ".join(out["resolved_samples"]["group_a"] + out["resolved_samples"]["group_b"])
        self.assertNotIn("_0h_", joined)

    def test_treatment_only_spec_still_pools_all_timepoints(self):
        out = json.loads(server.arf_differential(group_a="ILG", group_b="control"))
        self.assertEqual(out["n_a"], 4)
        self.assertEqual(out["n_b"], 4)


if __name__ == "__main__":
    unittest.main()
