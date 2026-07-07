import json
import unittest
from unittest import mock

import httpx

import phase_router
from phase_router import RouterState

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

    def test_non_serializable_return_is_caught(self):
        # 非JSON直列化な戻り値でも raise せず error JSON を返す（never-raises 契約）
        with mock.patch.object(ac.server, "arf_list_classes", return_value=object()):
            out = json.loads(ac.execute_tool("arf_list_classes", {}))
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


class TestRunTurn(unittest.TestCase):
    def setUp(self):
        # route が返しうる全ツール名にダミースキーマを用意
        all_names = {n for names in phase_router.PHASES.values() for n in names}
        self.schemas = {n: {"type": "function", "function": {"name": n}} for n in all_names}

    @staticmethod
    def _chat_from(script):
        state = {"i": 0}
        def chat_fn(messages, tools):
            msg = script[state["i"]]
            state["i"] += 1
            return msg
        return chat_fn

    @staticmethod
    def _recording_execute(load_result='{"status":"success"}'):
        calls = []
        def execute_fn(name, args):
            calls.append((name, args))
            if name == "load_dataset":
                return load_result
            return '{"status":"success","tool":"' + name + '"}'
        execute_fn.calls = calls
        return execute_fn

    def _agent(self, chat_fn, execute_fn, phase="ARF", interp_fn=None):
        return ac.Agent(
            tool_schemas=self.schemas,
            chat_fn=chat_fn,
            execute_fn=execute_fn,
            classify_fn=lambda q, c: phase,
            interp_fn=interp_fn,
        )

    def test_tool_then_final_answer(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_list_classes", "arguments": {}}}]},
            {"role": "assistant", "content": "クラスは3種です"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex).run_turn("ARFのクラス一覧", state, conv)
        self.assertEqual(out, "クラスは3種です")
        self.assertEqual(ex.calls, [("arf_list_classes", {})])
        self.assertEqual(state.last_phase, "ARF")
        # 会話順序: user -> assistant(tool_calls) -> tool -> assistant(content)
        self.assertEqual([m["role"] for m in conv], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(conv[2]["tool_name"], "arf_list_classes")

    def test_load_dataset_sets_flag(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "load_dataset", "arguments": {"directory": "C:/d"}}}]},
            {"role": "assistant", "content": "読み込みました"},
        ])
        ex = self._recording_execute(load_result="## 📂 データセット読み込み: C:/d\n...")
        state = RouterState(dataset_loaded=False)
        out = self._agent(chat, ex).run_turn("C:/d を読み込んで", state, [])
        self.assertTrue(state.dataset_loaded)
        self.assertEqual(out, "読み込みました")

    def test_load_dataset_failure_does_not_set_flag(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "load_dataset", "arguments": {"directory": "C:/bad"}}}]},
            {"role": "assistant", "content": "失敗しました"},
        ])
        ex = self._recording_execute(load_result='["データディレクトリが存在しません: C:/bad"]')
        state = RouterState(dataset_loaded=False)
        self._agent(chat, ex).run_turn("C:/bad を読み込んで", state, [])
        self.assertFalse(state.dataset_loaded)

    def test_max_rounds_stops_infinite_loop(self):
        # 常に tool_call を返し続けるフェイク
        def chat_fn(messages, tools):
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "arf_list_classes", "arguments": {}}}]}
        ex = self._recording_execute()
        agent = ac.Agent(self.schemas, chat_fn, ex, lambda q, c: "ARF", max_rounds=3)
        out = agent.run_turn("ループ", RouterState(dataset_loaded=True), [])
        self.assertEqual(out, "（ツール呼び出しが上限に達しました）")
        self.assertEqual(len(ex.calls), 3)

    def test_no_tool_call_returns_content_directly(self):
        chat = self._chat_from([{"role": "assistant", "content": "こんにちは"}])
        ex = self._recording_execute()
        out = self._agent(chat, ex).run_turn("やあ", RouterState(dataset_loaded=True), [])
        self.assertEqual(out, "こんにちは")
        self.assertEqual(ex.calls, [])

    def test_high_value_tool_escalates_to_cloud(self):
        captured = {}
        def interp_fn(messages):
            captured["messages"] = messages
            return "クラウド解釈です"
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("PCAを解釈して", state, conv)
        self.assertEqual(out, "クラウド解釈です")
        self.assertEqual(state.last_arm, "cloud")
        # 会話末尾はクラウド解釈（ローカル散文は積まれない）
        self.assertEqual(conv[-1], {"role": "assistant", "content": "クラウド解釈です"})
        self.assertNotIn("ローカル解釈", [m.get("content") for m in conv])
        # interp_fn への messages: 先頭が INTERP_SYSTEM、今ターンの証拠のみ
        import interp_eval
        self.assertEqual(captured["messages"][0],
                         {"role": "system", "content": interp_eval.INTERP_SYSTEM})
        self.assertEqual(captured["messages"][1], {"role": "user", "content": "PCAを解釈して"})

    def test_differential_veto_stays_local(self):
        called = {"n": 0}
        def interp_fn(messages):
            called["n"] += 1
            return "クラウド"
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_preprocess", "arguments": {}}},
                            {"function": {"name": "arf_differential", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル差次解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("差次を解釈して", state, [])
        self.assertEqual(out, "ローカル差次解釈")
        self.assertEqual(state.last_arm, "local")
        self.assertEqual(called["n"], 0)  # veto で interp_fn は呼ばれない

    def test_cloud_failure_falls_back_to_local(self):
        def interp_fn(messages):
            raise httpx.HTTPError("boom")
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        conv = []
        out = self._agent(chat, ex, interp_fn=interp_fn).run_turn("PCAを解釈して", state, conv)
        self.assertEqual(out, "ローカル解釈")
        self.assertEqual(state.last_arm, "local")
        self.assertEqual(conv[-1], {"role": "assistant", "content": "ローカル解釈"})

    def test_empty_cloud_response_falls_back_to_local(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "paper_search", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル文献解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex, phase="LITERATURE",
                          interp_fn=lambda m: "   ").run_turn("文献を解釈して", state, [])
        self.assertEqual(out, "ローカル文献解釈")
        self.assertEqual(state.last_arm, "local")

    def test_high_value_without_interp_fn_stays_local(self):
        chat = self._chat_from([
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "arf_re_pca", "arguments": {}}}]},
            {"role": "assistant", "content": "ローカル解釈"},
        ])
        ex = self._recording_execute()
        state = RouterState(dataset_loaded=True)
        out = self._agent(chat, ex).run_turn("PCAを解釈して", state, [])  # interp_fn=None（既定）
        self.assertEqual(out, "ローカル解釈")
        self.assertEqual(state.last_arm, "local")


class TestShouldEscalate(unittest.TestCase):
    def test_pca_tool_escalates(self):
        self.assertTrue(ac.should_escalate({"load_dataset", "arf_re_pca"}))

    def test_pca_preprocessed_escalates(self):
        self.assertTrue(ac.should_escalate({"arf_pca_preprocessed"}))

    def test_qc_preprocess_escalates(self):
        self.assertTrue(ac.should_escalate({"load_dataset", "arf_preprocess"}))

    def test_literature_escalates(self):
        self.assertTrue(ac.should_escalate({"paper_search"}))

    def test_differential_vetoes_even_with_preprocess(self):
        # arf_preprocess は cloud-tier だが arf_differential 同居で local へ降格
        self.assertFalse(
            ac.should_escalate({"load_dataset", "arf_preprocess", "arf_differential"}))

    def test_pca_and_differential_together_vetoes(self):
        self.assertFalse(ac.should_escalate({"arf_re_pca", "arf_differential"}))

    def test_identity_stays_local(self):
        self.assertFalse(ac.should_escalate({"load_dataset", "arf2_annotate_identities"}))

    def test_no_cloud_tier_stays_local(self):
        self.assertFalse(ac.should_escalate({"load_dataset"}))

    def test_empty_set_stays_local(self):
        self.assertFalse(ac.should_escalate(set()))
