"""Tests for the model-compatibility fixes, startup checks and deployment guards."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_app import preflight  # noqa: E402
from agent_app.agent import _clean_content  # noqa: E402
from agent_app.config import Config  # noqa: E402
from agent_app.llm import AnthropicClient, LLMError  # noqa: E402
from test_agent import FAKE_KEY, VERIFY_OK, Base, text  # noqa: E402

THINK = {"type": "thinking", "thinking": "summary", "signature": "SIG123"}
REDACTED = {"type": "redacted_thinking", "data": "ENCRYPTED"}


def api_response(body):
    r = mock.Mock(status_code=200)
    r.json.return_value = body
    return r


class ModelCompatTests(Base):
    def test_default_model_id_is_current_sonnet(self):
        # Verified against platform.claude.com/docs/en/models/sonnet-5-5/overview on 2026-10-28.
        self.assertEqual(self.cfg.model, "claude-sonnet-5-5")

    def test_request_body_has_no_rejected_parameters(self):
        client = AnthropicClient(self.cfg)
        with mock.patch("agent_app.llm.requests.post",
                        return_value=api_response({"content": [], "usage": {}})) as post:
            client.create("sys", [{"role": "user", "content": "hi"}], [{"name": "t", "description": "d",
                                                                         "input_schema": {"type": "object"}}])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        for banned in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
            self.assertNotIn(banned, body)
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], FAKE_KEY)

    def test_thinking_blocks_are_kept_verbatim(self):
        cleaned = _clean_content([THINK, {"type": "text", "text": "hi"}, REDACTED,
                                  {"type": "tool_use", "id": "a", "name": "n", "input": {}}])
        self.assertEqual(cleaned[0], THINK)
        self.assertEqual(cleaned[2], REDACTED)
        self.assertEqual([b["type"] for b in cleaned], ["thinking", "text", "redacted_thinking", "tool_use"])

    def test_thinking_blocks_are_sent_back_on_next_model_call(self):
        first = {"content": [THINK, {"type": "tool_use", "id": "tu_1", "name": "list_files", "input": {}}],
                 "stop_reason": "tool_use", "usage": {}}
        c = self.app([first, text("Done."), VERIFY_OK])
        t = self.task(c, self.run_task(c))["task"]
        self.assertEqual(t["status"], "completed_verified")
        second_call_messages = self.llm.calls[1][1]
        assistant = [m for m in second_call_messages if m["role"] == "assistant"][0]["content"]
        self.assertEqual(assistant[0], THINK)

    def test_parallel_tool_calls_in_one_turn(self):
        two = {"content": [{"type": "tool_use", "id": "a", "name": "make_dir", "input": {"path": "d1"}},
                           {"type": "tool_use", "id": "b", "name": "make_dir", "input": {"path": "d2"}}],
               "stop_reason": "tool_use", "usage": {}}
        c = self.app([two, text("Made both."), VERIFY_OK])
        t = self.task(c, self.run_task(c))["task"]
        self.assertEqual(t["status"], "completed_verified")
        self.assertTrue((self.ws / "d1").is_dir() and (self.ws / "d2").is_dir())
        results = self.llm.calls[1][1][-1]["content"]
        self.assertEqual({r["tool_use_id"] for r in results}, {"a", "b"})

    def test_verifier_gets_room_for_thinking(self):
        calls = []
        orig = self.__class__.app

        c = self.app([{"content": [{"type": "tool_use", "id": "a", "name": "list_files", "input": {}}],
                       "stop_reason": "tool_use", "usage": {}}, text("ok"), VERIFY_OK])
        orig_create = self.llm.create
        self.llm.create = lambda s, m, t=None, mt=None: (calls.append(mt), orig_create(s, m, t, mt))[1]
        self.run_task(c)
        self.assertIn(3000, calls)

    def test_default_max_tokens_leaves_room_for_thinking(self):
        self.assertGreaterEqual(self.cfg.max_tokens, 8000)


class PreflightTests(Base):
    def test_missing_key_is_reported_without_secrets(self):
        self.cfg.api_key = ""
        warns = preflight.config_warnings(self.cfg)
        self.assertTrue(any(w["level"] == "error" and "ANTHROPIC_API_KEY" in w["message"] for w in warns))
        self.assertEqual(preflight.check_api(self.cfg, mock.Mock())["ok"], False)

    def test_warnings_never_contain_secret_values(self):
        blob = json.dumps(preflight.config_warnings(self.cfg))
        self.assertNotIn(FAKE_KEY, blob)
        self.assertNotIn(self.cfg.token, blob)

    def test_ephemeral_and_run_python_warnings(self):
        self.cfg.ephemeral_storage = True
        self.cfg.enable_run_python, self.cfg.host = True, "0.0.0.0"
        msgs = " ".join(w["message"] for w in preflight.config_warnings(self.cfg))
        self.assertIn("temporary", msgs)
        self.assertIn("AGENT_ENABLE_RUN_PYTHON=0", msgs)

    def test_api_errors_are_explained_and_redacted(self):
        cases = {401: "rejected", 404: "not found", 429: "credit"}
        for code, word in cases.items():
            llm = mock.Mock()
            llm.create.side_effect = LLMError(f"model API error {code}: failed with {FAKE_KEY}")
            res = preflight.check_api(self.cfg, llm)
            self.assertFalse(res["ok"])
            self.assertIn(word, res["message"])
            self.assertNotIn(FAKE_KEY, res["message"])

    def test_api_success(self):
        llm = mock.Mock()
        llm.create.return_value = {"content": [], "model": "claude-sonnet-5-5"}
        self.assertTrue(preflight.check_api(self.cfg, llm)["ok"])

    def test_search_check_reports_failure_and_success(self):
        with mock.patch("agent_app.search.requests.request", side_effect=__import__("requests").ConnectionError()):
            self.assertFalse(preflight.check_search(self.cfg)["ok"])
        html = '<a class="result__a" href="https://example.com/">Ex</a><a class="result__snippet">s</a>'
        with mock.patch("agent_app.search.requests.request", return_value=mock.Mock(status_code=200, text=html)):
            self.assertTrue(preflight.check_search(self.cfg)["ok"])

    def test_selfcheck_endpoint_requires_auth_and_validates(self):
        c = self.app([])
        self.assertEqual(c.post("/api/selfcheck", json={"what": "api"}).status_code, 401)
        self.assertEqual(c.post("/api/selfcheck", json={"what": "x"}, headers=self.H).status_code, 400)
        r = c.get("/api/status", headers=self.H).get_json()
        self.assertIn("warnings", r)

    def test_selfcheck_api_uses_server_llm(self):
        c = self.app([{"content": [{"type": "text", "text": "OK"}], "model": "claude-sonnet-5-5", "usage": {}}])
        r = c.post("/api/selfcheck", json={"what": "api"}, headers=self.H).get_json()
        self.assertTrue(r["ok"])


class DeploymentGuardTests(unittest.TestCase):
    def run_wsgi(self, env_extra):
        d = tempfile.mkdtemp()
        env = {"PATH": os.environ.get("PATH", ""), "AGENT_DATA_DIR": d, **env_extra}
        if os.environ.get("PYTHONPATH"):  # keep the parent's package path; still no API key/token inherited
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        return subprocess.run([sys.executable, "-c", "import agent_app.wsgi as w; print('LOADED', w.app.name)"],
                              cwd=ROOT, env=env, capture_output=True, text=True)

    def test_wsgi_refuses_to_start_without_token(self):
        res = self.run_wsgi({})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("AGENT_TOKEN is not set", res.stderr)

    def test_wsgi_starts_with_token_and_logs_missing_key_without_secrets(self):
        res = self.run_wsgi({"AGENT_TOKEN": "z" * 40})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("LOADED", res.stdout)
        self.assertIn("ANTHROPIC_API_KEY is not set", res.stderr)
        self.assertNotIn("z" * 40, res.stderr + res.stdout)

    def tools_in_production(self, env_extra):
        d = tempfile.mkdtemp()
        env = {"PATH": os.environ.get("PATH", ""), "AGENT_DATA_DIR": d, "AGENT_TOKEN": "z" * 40, **env_extra}
        if os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        code = ("import agent_app.wsgi as w; "
                "print('TOOLS', ','.join(w.app.extensions['agent']['runner'].registry.names()))")
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_run_python_is_off_by_default_in_production(self):
        res = self.tools_in_production({})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("run_python", res.stdout)

    def test_run_python_enable_flag_is_ignored_in_production_without_override(self):
        res = self.tools_in_production({"AGENT_ENABLE_RUN_PYTHON": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("run_python", res.stdout)
        self.assertIn("was ignored", res.stderr)

    def test_run_python_needs_explicit_production_override(self):
        res = self.tools_in_production({"AGENT_ENABLE_RUN_PYTHON": "1", "AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("run_python", res.stdout)

    def test_dockerfile_trusts_proxy_so_lockout_is_per_client(self):
        self.assertIn("AGENT_TRUST_PROXY=1", (ROOT / "Dockerfile").read_text())

    def test_project_files_are_consistent(self):
        """Env var names used in deployment files must exist in config.py."""
        cfg_src = (ROOT / "agent_app" / "config.py").read_text()
        for name in ("ANTHROPIC_API_KEY", "AGENT_TOKEN", "AGENT_ENABLE_RUN_PYTHON", "AGENT_DATA_DIR",
                     "AGENT_EPHEMERAL_STORAGE", "BRAVE_API_KEY", "ANTHROPIC_MODEL"):
            self.assertIn(name, cfg_src, name)
        docker = (ROOT / "Dockerfile").read_text()
        render = (ROOT / "render.yaml").read_text()
        self.assertIn("AGENT_EPHEMERAL_STORAGE=1", docker)
        self.assertIn("agent_app.wsgi:app", docker)
        self.assertIn("ANTHROPIC_API_KEY", render)
        self.assertIn("AGENT_ENABLE_RUN_PYTHON", render)
        for f in ("agent_app", "static", "run.py", "requirements.txt"):
            self.assertTrue((ROOT / f).exists(), f)
        # every model id mentioned anywhere must be the verified one
        import re
        ids = set()
        for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.example")):
            if "tests" in p.parts or p.name == "DEPLOY.md" and False:
                continue
            ids |= set(re.findall(r"claude-(?:sonnet|opus|haiku|fable)-[0-9][0-9a-z-]*", p.read_text()))
        self.assertTrue(ids <= {"claude-sonnet-5-5", "claude-sonnet-5"}, ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
