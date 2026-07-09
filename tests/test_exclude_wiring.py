import json
import unittest

import server
import session_state


def _spot(master_id, entries):
    # arf_re_pca の loadings 整形（get_pca_loading_features）が spot.Name/MassCenter/RT を
    # 参照するため、Spot メタデータも付す。MasterAlignmentID は features のリスト位置に一致させる。
    return {
        "MasterAlignmentID": master_id,
        "Name": f"Lipid_{master_id}",
        "MassCenter": 700.0 + master_id,
        "RT": 5.0 + master_id,
        "AlignedPeakProperties": list(entries),
    }


def _fixture():
    # 4 サンプル x 3 スポット。build_pca_matrix が実際に消費できる生 list 行。
    # MasterAlignmentID は 0..2（実データ同様 features のリスト位置＝group_index に一致）。
    samples = ["sA", "sB", "sC", "sD"]
    spots = []
    for mid in (0, 1, 2):
        rows = []
        for i, name in enumerate(samples):
            row = [i] * 40  # data[18]=height を確保する長さ
            row[1] = name           # file_name（先頭付近の文字列）
            row[2] = 100 + mid       # master_peak_id（>=0 → 非ギャップフィル）
            row[18] = float(10 * (mid + 1) + i)  # height（サンプル間で分散を持たせる）
            rows.append(row)
        spots.append(_spot(mid, rows))
    return spots


class TestPreprocessExcludeWiring(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        session_state.session.filtered_features = _fixture()
        session_state.session.arf_class_index = None

    def test_preprocess_without_exclusion_uses_all_samples(self):
        out = json.loads(server.arf_preprocess())
        # matrix_shape = [n_samples, n_features]
        self.assertEqual(out["matrix_shape"][0], 4)

    def test_preprocess_honors_excluded_sample(self):
        session_state.session.excluded_samples.add("sB")
        out = json.loads(server.arf_preprocess())
        self.assertEqual(out["matrix_shape"][0], 3)
        self.assertTrue(any("手動除外" in c for c in out.get("caveats", [])))

    def test_preprocess_honors_excluded_spot(self):
        session_state.session.excluded_spots.add(1)
        out = json.loads(server.arf_preprocess())
        # 3 スポット → 2 スポット（列数が減る。各スポット1プロパティ height）
        self.assertEqual(out["matrix_shape"][1], 2)


class TestRePcaExcludeWiring(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()
        spots = _fixture()
        session_state.session.features = spots
        session_state.session.filtered_features = spots
        session_state.session.current_file_path = "dummy.arf"
        session_state.session.arf_class_index = None
        session_state.session.arf_tag_index = None

    def test_re_pca_honors_excluded_sample(self):
        session_state.session.excluded_samples.add("sB")
        out = server.arf_re_pca()
        text = out[0]
        # 行列形状 (サンプル数 x 特徴量数) に 3 サンプルが反映される
        self.assertIn("(3,", text)
        self.assertIn("手動除外", text)


if __name__ == "__main__":
    unittest.main()
