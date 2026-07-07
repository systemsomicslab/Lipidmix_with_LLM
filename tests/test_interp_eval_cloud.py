import os
import unittest
from unittest import mock

import interp_eval as ie


def _resp(content):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value={"choices": [{"message": {"content": content}}]})
    return r


class TestAzureGenerate(unittest.TestCase):
    def test_builds_azure_rest_request_and_parses_content(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        with mock.patch.object(ie.httpx, "post", return_value=_resp("解釈X")) as post:
            out = ie.azure_generate(msgs, deployment="gpt4omini",
                                    endpoint="https://ex.openai.azure.com/",
                                    api_key="KEY", api_version="2024-10-21")
        self.assertEqual(out, "解釈X")
        _, kwargs = post.call_args
        url = post.call_args.args[0] if post.call_args.args else kwargs["url"]
        self.assertEqual(
            url,
            "https://ex.openai.azure.com/openai/deployments/gpt4omini/"
            "chat/completions?api-version=2024-10-21")
        self.assertEqual(kwargs["headers"]["api-key"], "KEY")
        self.assertEqual(kwargs["json"]["messages"], msgs)
        self.assertEqual(kwargs["json"]["temperature"], 0.0)

    def test_v1_foundry_endpoint_puts_model_in_body_and_omits_api_version(self):
        # Azure AI Foundry の OpenAI 互換サーフェス（endpoint が /openai/v1 で終わる）では
        # deployment は URL パスでなく body の model に載り、api-version クエリは付かない。
        msgs = [{"role": "user", "content": "U"}]
        with mock.patch.object(ie.httpx, "post", return_value=_resp("解釈Y")) as post:
            out = ie.azure_generate(
                msgs, deployment="gpt-5.4-mini-kamegai",
                endpoint="https://test1-endpoint.services.ai.azure.com/openai/v1",
                api_key="KEY")
        self.assertEqual(out, "解釈Y")
        _, kwargs = post.call_args
        url = post.call_args.args[0] if post.call_args.args else kwargs["url"]
        self.assertEqual(
            url,
            "https://test1-endpoint.services.ai.azure.com/openai/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "gpt-5.4-mini-kamegai")
        self.assertEqual(kwargs["json"]["messages"], msgs)

    def test_missing_credentials_raises_without_calling_api(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(ie.httpx, "post") as post:
            with self.assertRaises(RuntimeError):
                ie.azure_generate([{"role": "user", "content": "U"}])
            post.assert_not_called()


class TestOpenAIMessageConversion(unittest.TestCase):
    def test_folds_ollama_tool_exchange_into_plain_user_turn(self):
        # build_interp_messages 形式（assistant.tool_calls + tool ロール）を OpenAI 有効な
        # role/content のみへ変換。tool 出力は user ターンに畳み込み、tool_name を保持する。
        msgs = ie.build_interp_messages(
            "SYS", ie.FrozenCase("id", "PCA", "NEG", "この結果を解釈して",
                                 "arf_re_pca", {"top_features": 10}, "PCA_OUTPUT_BODY"))
        out = ie._openai_messages(msgs)
        self.assertTrue(all(set(m) <= {"role", "content"} for m in out))
        self.assertNotIn("tool", [m["role"] for m in out])
        self.assertFalse(any(m["role"] == "assistant" and not m["content"] for m in out))
        self.assertEqual(out[0], {"role": "system", "content": "SYS"})
        joined = "\n".join(m["content"] for m in out)
        self.assertIn("この結果を解釈して", joined)
        self.assertIn("PCA_OUTPUT_BODY", joined)
        self.assertIn("arf_re_pca", joined)

    def test_plain_messages_pass_through_unchanged(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        self.assertEqual(ie._openai_messages(msgs), msgs)


class TestGenerateDispatch(unittest.TestCase):
    def test_azure_key_routes_to_azure_generate(self):
        msgs = [{"role": "user", "content": "U"}]
        with mock.patch.object(ie, "azure_generate", return_value="AZ") as az, \
             mock.patch.object(ie, "ollama_generate", return_value="OL") as ol:
            out = ie.generate_interp("azure_4omini", "gpt-4o-mini", None, msgs)
        self.assertEqual(out, "AZ")
        az.assert_called_once()
        ol.assert_not_called()

    def test_local_key_routes_to_ollama_generate(self):
        msgs = [{"role": "user", "content": "U"}]
        with mock.patch.object(ie, "azure_generate", return_value="AZ") as az, \
             mock.patch.object(ie, "ollama_generate", return_value="OL") as ol:
            out = ie.generate_interp("qwen3_on", "qwen3:14b", True, msgs)
        self.assertEqual(out, "OL")
        ol.assert_called_once()
        az.assert_not_called()


if __name__ == "__main__":
    unittest.main()
