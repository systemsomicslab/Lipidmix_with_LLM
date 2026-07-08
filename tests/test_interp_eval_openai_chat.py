import unittest
from unittest import mock

import interp_eval as ie


def _resp(tool_calls=None, content=None):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value={
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}]})
    return r


class TestOpenAIChat(unittest.TestCase):
    def test_parses_tool_calls_and_sends_tools_in_body(self):
        tools = [{"type": "function",
                  "function": {"name": "arf_re_pca", "description": "",
                               "parameters": {"type": "object", "properties": {}}}}]
        tc = [{"id": "c1", "type": "function",
               "function": {"name": "arf_re_pca",
                            "arguments": '{"top_features": 10}'}}]
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=tc, content="")) as post:
            out = ie.openai_chat([{"role": "user", "content": "PCAして"}], tools,
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out["tool_calls"],
                         [{"function": {"name": "arf_re_pca",
                                        "arguments": {"top_features": 10}}}])
        self.assertEqual(out["content"], "")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["tools"], tools)
        self.assertEqual(kwargs["json"]["temperature"], 0.0)

    def test_returns_content_when_no_tool_calls(self):
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=None, content="解釈Z")):
            out = ie.openai_chat([{"role": "user", "content": "U"}], [],
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out, {"content": "解釈Z", "tool_calls": []})

    def test_malformed_arguments_string_degrades_to_empty_dict(self):
        tc = [{"function": {"name": "x", "arguments": "not json"}}]
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(tool_calls=tc, content="")):
            out = ie.openai_chat([{"role": "user", "content": "U"}], [],
                                 deployment="d",
                                 endpoint="https://ex.openai.azure.com/", api_key="K")
        self.assertEqual(out["tool_calls"][0]["function"]["arguments"], {})

    def test_v1_foundry_puts_model_in_body(self):
        with mock.patch.object(ie.httpx, "post",
                               return_value=_resp(content="Y")) as post:
            ie.openai_chat([{"role": "user", "content": "U"}], [],
                           deployment="gpt-5.4-mini-kamegai",
                           endpoint="https://t.services.ai.azure.com/openai/v1",
                           api_key="K")
        _, kwargs = post.call_args
        url = post.call_args.args[0] if post.call_args.args else kwargs["url"]
        self.assertEqual(
            url, "https://t.services.ai.azure.com/openai/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "gpt-5.4-mini-kamegai")


if __name__ == "__main__":
    unittest.main()
