import json
import unittest

import server
import session_state
import agent_core as ac


class TestExecuteTool(unittest.TestCase):
    def setUp(self):
        session_state.session = server.AnalysisSession()

    def test_unknown_tool_returns_error_json(self):
        out = json.loads(ac.execute_tool("no_such_tool", {}))
        self.assertEqual(out["status"], "error")

    def test_tool_error_is_returned_as_string(self):
        # arf_differential は行列未ロードなら自前の error JSON を返す
        out = json.loads(ac.execute_tool("arf_differential", {"group_a": "A", "group_b": "B"}))
        self.assertEqual(out["status"], "error")

    def test_list_return_is_json_stringified(self):
        # load_dataset は list を返す（不正ディレクトリでも list）。JSON 文字列化される。
        out = ac.execute_tool("load_dataset", {"directory": "C:/nonexistent_xyz_123"})
        self.assertIsInstance(json.loads(out), list)

    def test_bad_kwarg_exception_is_caught(self):
        out = json.loads(ac.execute_tool("arf_list_classes", {"unexpected_kw": 1}))
        self.assertEqual(out["status"], "error")


class TestTruncate(unittest.TestCase):
    def test_within_limit_passthrough(self):
        self.assertEqual(ac._truncate("abc", 10), "abc")

    def test_over_limit_truncated_with_marker(self):
        out = ac._truncate("x" * 100, 10)
        self.assertIn("切り詰め", out)
        self.assertTrue(out.startswith("x" * 10))


class TestIsError(unittest.TestCase):
    def test_status_error_true(self):
        self.assertTrue(ac._is_error('{"status":"error","error":"x"}'))

    def test_success_dict_false(self):
        self.assertFalse(ac._is_error('{"status":"success"}'))

    def test_non_json_false(self):
        self.assertFalse(ac._is_error("plain text"))

    def test_list_json_false(self):
        self.assertFalse(ac._is_error('["a","b"]'))
