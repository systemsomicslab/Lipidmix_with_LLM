"""意味論 caveat ガード（output-format 未 pull 時に1回だけ前置）の単体テスト。

条件付きガードの4条件（未読時のみ / 1回のみ / リソース読了で解除 / MODE=off で無効）
と、フラグがデータ切替（reset_analysis_state / load_data）で消えないことを検証する。
"""
import os
import unittest
from unittest import mock

from lipidmix.arf import reader as arf_reader
from lipidmix.core import session_state


class TestSemanticsCaveatGuard(unittest.TestCase):
    def setUp(self):
        session_state.session = session_state.AnalysisSession()

    def test_defaults_flags_false(self):
        self.assertFalse(session_state.session.output_format_seen)
        self.assertFalse(session_state.session.caveat_emitted)

    def test_prepends_once_then_stops(self):
        s = session_state.session
        first = s.maybe_prepend_caveat("BODY-1")
        self.assertTrue(first.startswith(session_state.SEMANTICS_CAVEAT))
        self.assertIn("BODY-1", first)
        self.assertTrue(s.caveat_emitted)
        # 2回目は前置しない（本文そのまま）
        second = s.maybe_prepend_caveat("BODY-2")
        self.assertEqual(second, "BODY-2")

    def test_resource_read_suppresses_prepend(self):
        s = session_state.session
        s.output_format_seen = True  # output-format を fetch 済み相当
        out = s.maybe_prepend_caveat("BODY")
        self.assertEqual(out, "BODY")
        self.assertFalse(s.caveat_emitted)

    def test_mode_off_disables(self):
        s = session_state.session
        with mock.patch.dict(os.environ, {"LIPIDMIX_CAVEAT_MODE": "off"}):
            out = s.maybe_prepend_caveat("BODY")
        self.assertEqual(out, "BODY")
        self.assertFalse(s.caveat_emitted)

    def test_mode_default_is_digest(self):
        s = session_state.session
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIPIDMIX_CAVEAT_MODE", None)
            out = s.maybe_prepend_caveat("BODY")
        self.assertTrue(out.startswith(session_state.SEMANTICS_CAVEAT))

    def test_flags_survive_arf_reset_analysis(self):
        s = session_state.session
        s.maybe_prepend_caveat("BODY")  # caveat_emitted -> True
        s.output_format_seen = True
        s.arf.reset_analysis()
        # データ切替のたびに再注入しないため、両フラグは保持される
        self.assertTrue(s.caveat_emitted)
        self.assertTrue(s.output_format_seen)

    def test_flags_survive_new_file_load(self):
        s = session_state.session
        s.caveat_emitted = True
        s.output_format_seen = True
        orig = arf_reader.deserialize
        arf_reader.deserialize = lambda buf: []
        self.addCleanup(setattr, arf_reader, "deserialize", orig)
        try:
            s.load_data("dummy_path.arf")
        except Exception:
            pass  # discover 系がファイル不在で落ちても、フラグは触られない
        self.assertTrue(s.caveat_emitted)
        self.assertTrue(s.output_format_seen)


class TestResourceReadClearsGuard(unittest.TestCase):
    """output_format_reference() を実際に呼ぶと output_format_seen が立つこと。"""

    def setUp(self):
        session_state.session = session_state.AnalysisSession()

    def test_reading_resource_sets_seen(self):
        from lipidmix.tools import resources as tools_resources
        self.assertFalse(session_state.session.output_format_seen)
        text = tools_resources.output_format_reference()
        self.assertIn("オントロジー", text)
        self.assertTrue(session_state.session.output_format_seen)


if __name__ == "__main__":
    unittest.main()
