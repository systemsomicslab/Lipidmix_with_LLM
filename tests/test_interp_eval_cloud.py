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

    def test_missing_credentials_raises_without_calling_api(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(ie.httpx, "post") as post:
            with self.assertRaises(RuntimeError):
                ie.azure_generate([{"role": "user", "content": "U"}])
            post.assert_not_called()


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
