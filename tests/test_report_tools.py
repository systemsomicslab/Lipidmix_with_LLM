import os
import unittest
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # import server が pyplot を読む前にヘッドレス指定

import server
import knowledge_store


class WriteLocationHelpers(unittest.TestCase):
    def test_dir_is_writable_true_for_new_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "reports"
            self.assertTrue(server._dir_is_writable(target))
            self.assertTrue(target.is_dir())

    def test_dir_is_writable_false_when_parent_is_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            self.assertFalse(server._dir_is_writable(blocker / "reports"))

    def test_first_writable_dir_skips_unwritable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            bad = blocker / "reports"
            good = Path(d) / "ok"
            self.assertEqual(server._first_writable_dir([bad, good]), good)

    def test_first_writable_dir_raises_when_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            with self.assertRaises(OSError):
                server._first_writable_dir([blocker / "a", blocker / "b"])

    def test_build_report_meta_shape(self):
        meta = server._build_report_meta("a-1", "DS", "draft", ["s1", "s2"])
        self.assertEqual(meta["type"], "report")
        self.assertEqual(meta["analysis_id"], "a-1")
        self.assertEqual(meta["dataset"], "DS")
        self.assertEqual(meta["status"], "draft")
        self.assertEqual(meta["knowledge_refs"], ["s1", "s2"])
        self.assertRegex(meta["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_build_report_meta_defaults_refs_to_empty_list(self):
        meta = server._build_report_meta("a-1", "DS", "draft", None)
        self.assertEqual(meta["knowledge_refs"], [])


class ReportToolTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved_data_dir = server.DATA_DIR
        server.DATA_DIR = self.tmp
        self._saved_env = os.environ.pop("LIPIDMIX_REPORTS_DIR", None)

    def tearDown(self):
        server.DATA_DIR = self._saved_data_dir
        if self._saved_env is not None:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_env
        self._tmp.cleanup()

    def test_write_report_creates_file_with_frontmatter_and_body(self):
        msg = server.write_report("a-1", "NEG / DS", "## 目的\nグループ比較")
        path = self.tmp / "reports" / "a-1.md"
        self.assertTrue(path.is_file())
        self.assertIn("reports", msg)
        meta, body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["type"], "report")
        self.assertEqual(meta["analysis_id"], "a-1")
        self.assertEqual(meta["status"], "draft")
        self.assertIn("## 目的", body)

    def test_write_report_overwrites_on_second_call(self):
        server.write_report("a-1", "DS", "## 目的\n古い本文")
        server.write_report("a-1", "DS", "## 目的\n新しい本文", status="final")
        path = self.tmp / "reports" / "a-1.md"
        meta, body = knowledge_store.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "final")
        self.assertIn("新しい本文", body)
        self.assertNotIn("古い本文", body)

    def test_read_report_round_trips(self):
        server.write_report("a-1", "DS", "## 結論\nXがYより高い")
        text = server.read_report("a-1")
        self.assertIn("## 結論", text)
        self.assertIn("XがYより高い", text)

    def test_read_report_missing_returns_guidance(self):
        text = server.read_report("does-not-exist")
        self.assertIn("見つかりません", text)

    def test_list_reports_shows_written_report(self):
        server.write_report("a-1", "DS", "## 目的\nx", status="final")
        listing = server.list_reports()
        self.assertIn("a-1", listing)
        self.assertIn("status=final", listing)

    def test_list_reports_empty(self):
        listing = server.list_reports()
        self.assertIn("まだありません", listing)


if __name__ == "__main__":
    unittest.main()
