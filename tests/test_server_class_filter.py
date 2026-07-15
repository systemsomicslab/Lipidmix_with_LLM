import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import server
import session_state
import path_resolvers


def make_class_index() -> dict:
    control = {"file_id": 0, "file_name": "sample_control", "class_id": "control"}
    treated = {"file_id": 1, "file_name": "sample_treated", "class_id": "treated"}
    return {
        "mddata_path": "dataset.mddata",
        "records": [control, treated],
        "by_file_id": {0: control, 1: treated},
        "by_file_name": {"sample_control": control, "sample_treated": treated},
        "class_counts": {"control": 1, "treated": 1},
    }


class FakeSession:
    def __init__(self):
        self.current_file_path = None
        self.features = [{
            "MasterAlignmentID": 10,
            "AlignedPeakProperties": [
                [0, "sample_control", 100.0],
                [1, "sample_treated", 200.0],
            ],
        }]
        self.filtered_features = None
        self.pca_result = None
        self.arf_tag_index = {}
        self.arf_class_index = make_class_index()
        # 手動除外集合（実 AnalysisSession をミラー。空集合なので prune_spots は恒等）
        self.excluded_samples = set()
        self.excluded_spots = set()

    def load_data(self, file_path, tag_directory=None):
        self.current_file_path = file_path
        return self.features


def fake_extract_peak_properties(features):
    rows = sum(len(spot["AlignedPeakProperties"]) for spot in features)
    return pd.DataFrame({"row": range(rows)})


def fake_build_pca_matrix(features, use_properties=None, min_detection_rate=0.0):
    rows = features[0]["AlignedPeakProperties"] if features else []
    names = [row[1] for row in rows]
    return np.ones((len(names), 2)), names, ["10_height", "11_height"]


def fake_run_pca(matrix, n_components=None, log_transform=False):
    return {
        "components": np.zeros((matrix.shape[0], 2)).tolist(),
        "explained_variance_ratio": [0.6, 0.4],
    }


class ServerClassFilterTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
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

    def test_arf_parser_filters_samples_by_class_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            result = server.arf_parser(str(arf_path), class_ids=["control"])

        self.assertIn("Class IDフィルタ**: `control`", result)
        self.assertIn("PCA入力行列の形状**: (1, 2)", result)
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertEqual([row[1] for row in rows], ["sample_control"])

    def test_arf_parser_combines_class_filter_with_intensity_keyword(self):
        # arf_re_pca が担っていた「強度/キーワードで絞って PCA し直す」を arf_parser が吸収。
        # _filter_arf_spots は恒等にパッチし、class フィルタ結果がそのまま PCA へ流れることを確認。
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            with patch.object(path_resolvers, "_filter_arf_spots",
                              side_effect=lambda feats, *a, **k: feats):
                result = server.arf_parser(
                    str(arf_path), class_ids=["treated"],
                    min_intensity=50.0, annotation_keyword="PC",
                )

        self.assertIn("Class IDフィルタ**: `treated`", result)
        self.assertIn("PCA入力行列の形状**: (1, 2)", result)
        self.assertIn("適用フィルタ条件", result)
        rows = self.session.filtered_features[0]["AlignedPeakProperties"]
        self.assertEqual([row[1] for row in rows], ["sample_treated"])

    def test_arf_list_classes_returns_counts(self):
        self.session.current_file_path = "test.arf"
        payload = json.loads(server.arf_list_classes())
        self.assertEqual(payload["class_counts"], {"control": 1, "treated": 1})

    def test_arf_list_classes_returns_factor_vocabulary(self):
        self.session.current_file_path = "test.arf"
        payload = json.loads(server.arf_list_classes())
        self.assertEqual(set(payload["factors_by_position"]["0"]), {"control", "treated"})

    def test_arf_parser_plot_includes_group_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            result = server.arf_parser(str(arf_path))
        self.assertIn("control=1", result)
        self.assertIn("treated=1", result)

    def test_arf_parser_group_levels_collapse(self):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            result = server.arf_parser(str(arf_path), group_levels=["control"])
        # control -> "control"; treated has no listed level -> "other"
        self.assertIn("control=1", result)
        self.assertIn("other=1", result)

    def test_arf_parser_orders_loadings_before_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            arf_path = Path(tmp) / "test.arf"
            arf_path.touch()
            result = server.arf_parser(str(arf_path))
        text = result
        self.assertIn("```json", text)
        self.assertIn('"pc1"', text)
        # loadings 節は座標 JSON より前（末尾截断で loadings を守る）
        self.assertLess(text.index("Loadings 寄与度分析"), text.index("```json"))


if __name__ == "__main__":
    unittest.main()
