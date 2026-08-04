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


class _ParserFakeSession:
    """test_server_class_filter.FakeSession と同型だが、サンプル名を差し替えられる版。"""

    def __init__(self, names):
        self.current_file_path = None
        self.features = [{
            "MasterAlignmentID": 10,
            "AlignedPeakProperties": [[i, n, 100.0 + i] for i, n in enumerate(names)],
        }]
        self.filtered_features = None
        self.pca_result = None
        self.arf_tag_index = {}
        self.arf_class_index = None
        self.excluded_samples = set()
        self.excluded_spots = set()

    def load_data(self, file_path, tag_directory=None):
        self.current_file_path = file_path
        return self.features

    def maybe_prepend_caveat(self, text, topic=None):
        return text


class ArfParserFactorTests(unittest.TestCase):
    def setUp(self):
        self.session = _ParserFakeSession([
            "20220901_RAW_control_0h_1_NEG",
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_0h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220901_QC_RAW_NEG_1",
        ])
        self.patches = [
            patch.object(session_state, "session", self.session),
            patch.object(server.arf_reader, "extract_peak_properties", fake_extract_peak_properties),
            patch.object(server.arf_reader, "build_pca_matrix", fake_build_pca_matrix),
            patch.object(server.arf_reader, "run_pca", fake_run_pca),
            patch.object(server.arf_reader, "get_pca_loading_features", return_value=[]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def _run(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            return server.arf_parser(str(arf_path), **kwargs)

    def test_group_factors_produce_cross_product_counts(self):
        result = self._run(group_factors=[["control", "ILG"], ["0h", "6h"]])
        self.assertIn("control|0h=1", result)
        self.assertIn("ILG|6h=1", result)
        self.assertIn("qc=1", result)

    def test_class_ids_filter_by_name_only_token(self):
        result = self._run(class_ids=["6h"])
        self.assertIn("Class IDフィルタ**: `6h`", result)
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertEqual(
            [row[1] for row in rows],
            ["20220901_RAW_control_6h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"],
        )

    def test_qc_excluded_by_role_is_disclosed(self):
        result = self._run(class_ids=["raw"])
        self.assertIn("role により除外", result)
        self.assertIn("20220901_QC_RAW_NEG_1", result)

    def test_include_roles_brings_qc_back(self):
        self._run(class_ids=["raw"], include_roles=["sample", "qc"])
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertIn("20220901_QC_RAW_NEG_1", [row[1] for row in rows])


class ArfPcaPreprocessedFactorTests(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        names = ["20220901_RAW_control_0h_1_NEG", "20220902_RAW_ILG_6h_1_NEG"]
        session_state.session.feature_matrix = np.array([[1.0, 2.0], [3.0, 4.0]])
        session_state.session.pp_sample_names = names
        session_state.session.pp_feature_names = ["Spot_0_height", "Spot_1_height"]
        session_state.session.preprocessing_recipe = {"normalize": "median"}
        session_state.session.features = []

    def test_group_factors_label_the_plot(self):
        with patch.object(server.arf_reader, "run_pca", fake_run_pca), \
             patch.object(server.arf_reader, "get_pca_loading_features", return_value=[]):
            result = server.arf_pca_preprocessed(
                group_factors=[["control", "ILG"], ["0h", "6h"]])
        self.assertIn("control|0h=1", result)
        self.assertIn("ILG|6h=1", result)


class ArfListClassesVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.session = _ParserFakeSession([
            "20220901_RAW_control_6h_1_NEG",
            "20220902_RAW_ILG_6h_1_NEG",
            "20220901_QC_RAW_NEG_1",
        ])
        self.session.current_file_path = "test.arf"
        self.patcher = patch.object(session_state, "session", self.session)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_reports_sample_token_vocabulary(self):
        payload = json.loads(server.arf_list_classes())
        tokens = payload["sample_token_vocabulary"]["tokens"]
        self.assertEqual(tokens["6h"]["samples"], 2)
        self.assertEqual(tokens["ilg"]["samples"], 1)

    def test_vocabulary_breaks_down_by_role(self):
        payload = json.loads(server.arf_list_classes())
        tokens = payload["sample_token_vocabulary"]["tokens"]
        self.assertEqual(tokens["raw"]["roles"], {"qc": 1, "sample": 2})

    def test_works_without_mddata(self):
        payload = json.loads(server.arf_list_classes())
        self.assertIsNone(payload["mddata_path"])
        self.assertEqual(payload["class_counts"], {})


if __name__ == "__main__":
    unittest.main()
