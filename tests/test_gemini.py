"""Focused tests for Gemini request/response translation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_app.config import Config
from agent_app.llm import GeminiClient, LLMError


class GeminiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.from_env(env={
            "AGENT_DATA_DIR": self.tmp.name,
            "AGENT_TOKEN": "t" * 32,
            "GEMINI_API_KEY": "AIza-test-key",
        }, load_dotenv=False)
        self.client = GeminiClient(self.cfg, sleep=lambda _: None, max_retries=0)

    def test_tool_result_maps_back_to_original_function_name(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "I will inspect."},
                {"type": "tool_use", "id": "call-1", "name": "list_files", "input": {"path": "."}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "a.txt"},
            ]},
        ]
        contents = self.client._contents(messages)
        self.assertEqual(contents[0]["role"], "model")
        self.assertEqual(contents[1]["role"], "user")
        self.assertEqual(contents[1]["parts"][0]["functionResponse"]["name"], "list_files")
        self.assertEqual(contents[1]["parts"][0]["functionResponse"]["response"]["result"], "a.txt")

    def test_schema_drops_unsupported_anthropic_fields_recursively(self):
        schema = self.client._schema({
            "type": "object", "additionalProperties": False,
            "properties": {"path": {"type": "string", "description": "File path"},
                           "count": {"type": "integer", "minimum": 1}},
            "required": ["path"],
        })
        self.assertEqual(schema["type"], "OBJECT")
        self.assertEqual(schema["properties"]["path"]["type"], "STRING")
        self.assertNotIn("minimum", schema["properties"]["count"])
        self.assertNotIn("additionalProperties", schema)

    @patch("agent_app.llm.requests.post")
    def test_generate_content_response_maps_function_call(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"text": "Working."},
                {"functionCall": {"name": "list_files", "args": {"path": "."}}},
            ]}}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
        }
        post.return_value = response
        result = self.client.create("system", [{"role": "user", "content": "list files"}], [], 128)
        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertEqual(result["content"][1]["type"], "tool_use")
        self.assertEqual(result["content"][1]["name"], "list_files")
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "AIza-test-key")
        self.assertNotIn("AIza-test-key", str(result))

    @patch("agent_app.llm.requests.post")
    def test_api_error_does_not_leak_key(self, post):
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"error": {"message": "invalid key AIza-test-key"}}
        post.return_value = response
        with self.assertRaises(LLMError) as caught:
            self.client.create("system", [{"role": "user", "content": "hi"}])
        self.assertNotIn("AIza-test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
