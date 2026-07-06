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
