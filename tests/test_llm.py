"""AnthropicClient error handling. MOCKED: requests.post is replaced, so these tests prove the retry/redaction logic
only. They do NOT prove that a real API key is valid or that the live Anthropic API is reachable."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_app.config import Config  # noqa: E402
from agent_app.llm import AnthropicClient, LLMError  # noqa: E402

KEY = "sk-ant-FAKEKEY-0123456789abcdef"


class Resp:
    def __init__(self, status, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._body


def make(key=KEY):
    tmp = tempfile.TemporaryDirectory()
    env = {"AGENT_DATA_DIR": tmp.name, "AGENT_TOKEN": "t" * 40}
    if key:
        env["ANTHROPIC_API_KEY"] = key
    cfg = Config.from_env(env=env, load_dotenv=False)
    sleeps = []
    return tmp, cfg, AnthropicClient(cfg, sleep=sleeps.append), sleeps


OK = Resp(200, {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"})


class LLMTests(unittest.TestCase):
    def call(self, side_effect, key=KEY):
        tmp, cfg, client, sleeps = make(key)
        self.addCleanup(tmp.cleanup)
        with mock.patch("agent_app.llm.requests.post", side_effect=side_effect) as p:
            try:
                return client.create("sys", [{"role": "user", "content": "x"}]), p, sleeps, None
            except LLMError as exc:
                return None, p, sleeps, exc

    def test_missing_key(self):
        _, p, _, err = self.call([OK], key=None)
        self.assertIn("ANTHROPIC_API_KEY", str(err))
        p.assert_not_called()

    def test_rate_limit_then_success_retries_with_backoff(self):
        data, p, sleeps, err = self.call([Resp(429, {"error": {"message": "slow down"}}), Resp(529, {}), OK])
        self.assertIsNone(err)
        self.assertEqual(p.call_count, 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(data["stop_reason"], "end_turn")

    def test_retries_are_bounded_and_error_has_no_secret(self):
        msg = {"error": {"message": f"bad key {KEY}"}}
        _, p, _, err = self.call([Resp(500, msg)] * 4)
        self.assertEqual(p.call_count, 4)
        self.assertIn("500", str(err))
        self.assertNotIn(KEY, str(err))

    def test_non_retryable_400_fails_immediately(self):
        _, p, sleeps, err = self.call([Resp(400, {"error": {"message": "invalid request"}})])
        self.assertEqual(p.call_count, 1)
        self.assertEqual(sleeps, [])
        self.assertIn("400", str(err))

    def test_timeout_retries_then_reports_without_secret(self):
        _, p, _, err = self.call(requests.Timeout(f"timed out {KEY}"))
        self.assertEqual(p.call_count, 4)
        self.assertIn("Timeout", str(err))
        self.assertNotIn(KEY, str(err))

    def test_invalid_json_and_unexpected_shape(self):
        _, _, _, err = self.call([Resp(200, bad_json=True)])
        self.assertIn("invalid JSON", str(err))
        _, _, _, err = self.call([Resp(200, {"nope": 1})])
        self.assertIn("unexpected", str(err))

    def test_key_only_in_header_never_in_body(self):
        _, p, _, _ = self.call([OK])
        kwargs = p.call_args.kwargs
        self.assertEqual(kwargs["headers"]["x-api-key"], KEY)
        self.assertNotIn(KEY, str(kwargs["json"]))


if __name__ == "__main__":
    unittest.main()
