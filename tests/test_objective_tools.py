"""objective ライフサイクル（knowledge_store 関数 ＋ server ツール）の検証。"""

import tempfile
import unittest
from pathlib import Path

import knowledge_store as ks
import server


class ObjectiveStoreTests(unittest.TestCase):
    def _write(self, directory: Path) -> Path:
        return ks.write_objective(
            directory,
            "exp-1",
            {
                "dataset": "NEG / x",
                "polarity": "NEG",
                "groups": ["control", "treatment"],
                "comparison": "群間差",
                "biological_context": "",
                "inferred_objective": "推測",
                "confirmed_objective": "",
                "expected_biology": [],
            },
            ["どのクラスに差が出るか", "エーテル脂質は減るか"],
        )

    def test_write_and_parse_labeled_subquestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp))
            self.assertTrue(path.is_file())
            meta, subqs, _body = ks.parse_objective(path)
            self.assertEqual(meta["analysis_id"], "exp-1")
            self.assertEqual([label for label, _ in subqs], ["Q1", "Q2"])
            self.assertIn("どのクラス", subqs[0][1])
            self.assertEqual(str(meta["confirmed"]), "False")

    def test_update_meta_preserves_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp))
            ks.update_objective_meta(path, {"confirmed_objective": "確定した目的", "biological_context": "RAW macrophage"})
            meta, subqs, _ = ks.parse_objective(path)
            self.assertEqual(meta["confirmed_objective"], "確定した目的")
            self.assertEqual(meta["biological_context"], "RAW macrophage")
            self.assertEqual(str(meta["confirmed"]), "True")
            self.assertEqual(len(subqs), 2)  # 本文の小問は温存

    def test_add_subquestions_numbers_after_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp))
            ks.add_subquestions(path, ["創発した問い"])
            _meta, subqs, _ = ks.parse_objective(path)
            self.assertEqual([label for label, _ in subqs], ["Q1", "Q2", "Q3"])
            self.assertIn("創発した問い", dict(subqs)["Q3"])

    def test_search_log_roundtrip_with_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp))
            ks.append_search_log(path, "Q2", "2026-06-14", "plasmalogen, oxidation, macrophage", 8, 0)
            searched = ks.searched_labels(path)
            self.assertIn("Q2", searched)
            self.assertIn("hits=8", searched["Q2"])
            # 小問パースは探索ログ行に汚染されない
            _meta, subqs, _ = ks.parse_objective(path)
            self.assertEqual([label for label, _ in subqs], ["Q1", "Q2"])


class ObjectiveServerToolTests(unittest.TestCase):
    def setUp(self):
        self._orig_analyses = server.ANALYSES_DIR
        self._orig_knowledge = server.KNOWLEDGE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        server.ANALYSES_DIR = base / "analyses"
        server.KNOWLEDGE_DIR = base / "knowledge"
        server.ANALYSES_DIR.mkdir()
        server.KNOWLEDGE_DIR.mkdir()

    def tearDown(self):
        server.ANALYSES_DIR = self._orig_analyses
        server.KNOWLEDGE_DIR = self._orig_knowledge
        self._tmp.cleanup()

    def test_record_then_coverage_then_log_churn(self):
        server.record_objective(
            "exp-2", "NEG / x", "NEG", ["control", "treatment"], "群間差",
            ["糖代謝のグルコース取り込み", "エーテル脂質は減るか"],
            biological_context="RAW macrophage",
        )
        cov = server.knowledge_coverage("exp-2")
        self.assertIn("Q1", cov)
        self.assertIn("Q2", cov)
        self.assertIn("GAP", cov)  # knowledge 空なので GAP

        # 探索を記録すると coverage に already searched 注記が出る
        server.log_search("exp-2", "Q1", "glucose uptake", 5, 0)
        cov2 = server.knowledge_coverage("exp-2")
        self.assertIn("already searched", cov2)

    def test_update_adds_subquestion(self):
        server.record_objective("exp-3", "NEG", "NEG", ["a", "b"], "c", ["最初の問い"])
        server.update_objective("exp-3", add_subquestions=["創発の問い"], confirmed_objective="確定")
        cov = server.knowledge_coverage("exp-3")
        self.assertIn("Q2", cov)
        self.assertIn("創発の問い", cov)

    def test_missing_objective_message(self):
        self.assertIn("見つかりません", server.knowledge_coverage("nope"))
        self.assertIn("見つかりません", server.log_search("nope", "Q1", "q", 0, 0))


if __name__ == "__main__":
    unittest.main()
