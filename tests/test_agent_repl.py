import unittest
from unittest import mock

import httpx

import agent_repl


class TestSafeClassify(unittest.TestCase):
    def test_returns_empty_on_httpx_error(self):
        with mock.patch.object(agent_repl.phase_router, "ollama_classify_fn",
                               side_effect=httpx.ConnectError("down")):
            self.assertEqual(agent_repl.safe_classify("q", ["ARF"]), "")

    def test_passes_through_normal_result(self):
        with mock.patch.object(agent_repl.phase_router, "ollama_classify_fn",
                               return_value="ARF"):
            self.assertEqual(agent_repl.safe_classify("q", ["ARF"]), "ARF")

    def test_returns_empty_on_malformed_response(self):
        with mock.patch.object(agent_repl.phase_router, "ollama_classify_fn",
                               side_effect=KeyError("message")):
            self.assertEqual(agent_repl.safe_classify("q", ["ARF"]), "")


class BuildInterpFnGateTests(unittest.TestCase):
    _CREDS = {
        "AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "k",
        "AZURE_OPENAI_DEPLOYMENT": "d",
    }

    def test_kill_switch_returns_none(self):
        env = dict(self._CREDS, LIPIDMIX_CLOUD_INTERP="0")
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_missing_creds_returns_none(self):
        # creds を消し、トグルは既定（未設定）
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_partial_creds_returns_none(self):
        env = {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com"}  # key/deployment 欠落
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(agent_repl._build_interp_fn())

    def test_creds_present_returns_callable(self):
        with mock.patch.dict("os.environ", dict(self._CREDS), clear=True):
            fn = agent_repl._build_interp_fn()
        self.assertTrue(callable(fn))

    def test_creds_present_dispatches_to_azure_generate(self):
        with mock.patch.dict("os.environ", dict(self._CREDS), clear=True):
            fn = agent_repl._build_interp_fn()
        with mock.patch("interp_eval.azure_generate", return_value="解釈X") as m:
            out = fn([{"role": "system", "content": "s"}])
        self.assertEqual(out, "解釈X")
        m.assert_called_once()
