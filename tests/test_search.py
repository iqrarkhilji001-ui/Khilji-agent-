"""Search tests use FIXTURES shaped like DuckDuckGo's HTML results and Brave's JSON.
They prove our parsing/wiring; they do NOT prove the live services still return this shape."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from agent_app.search import parse_ddg, web_search  # noqa: E402
from agent_app.tools import ToolContext, ToolError, build_registry  # noqa: E402
from test_agent import Base  # noqa: E402

DDG_HTML = """
<div class="results">
 <div class="result results_links result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>
   <a class="result__snippet">Buy now</a></div>
 <div class="result"><h2><a class="result__a" rel="nofollow"
   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1&amp;rut=abc">Example <b>Domain</b></a></h2>
   <a class="result__snippet" href="x">This domain is for <b>use</b> in examples.</a></div>
 <div class="result"><h2><a class="result__a" href="https://other.org/">Other</a></h2>
   <a class="result__snippet">Second snippet</a></div>
</div>"""

BRAVE_JSON = {"web": {"results": [
    {"title": "Brave One", "url": "https://one.example/", "description": "first"},
    {"title": "Brave Two", "url": "https://two.example/", "description": "second"}]}}


def resp(status=200, text="", js=None):
    r = mock.Mock(status_code=status, text=text)
    r.json.return_value = js
    return r


class SearchTests(Base):
    def ctx(self, brave=""):
        self.cfg.brave_api_key = brave
        return ToolContext(self.cfg, mock.Mock())

    def test_parse_ddg_unwraps_links_and_skips_ads(self):
        res = parse_ddg(DDG_HTML, 5)
        self.assertEqual([r["url"] for r in res], ["https://example.com/page?a=1", "https://other.org/"])
        self.assertEqual(res[0]["title"], "Example Domain")
        self.assertEqual(res[0]["snippet"], "This domain is for use in examples.")

    def test_count_limit(self):
        self.assertEqual(len(parse_ddg(DDG_HTML, 1)), 1)

    def test_bot_check_page_gives_clear_error(self):
        with self.assertRaises(ToolError) as cm:
            parse_ddg("<html>Unfortunately, bots use DuckDuckGo too. anomaly-modal</html>", 5)
        self.assertIn("bot check", str(cm.exception))

    def test_default_provider_is_duckduckgo_and_output_is_labelled(self):
        with mock.patch("agent_app.search.requests.request", return_value=resp(text=DDG_HTML)) as m:
            out = web_search({"query": "example"}, self.ctx())
        self.assertEqual(m.call_args.args[:2], ("POST", "https://html.duckduckgo.com/html/"))
        self.assertIn("UNTRUSTED SEARCH RESULTS via duckduckgo", out)
        self.assertIn("https://example.com/page?a=1", out)

    def test_brave_used_only_when_key_set_and_key_sent_as_header(self):
        with mock.patch("agent_app.search.requests.request", return_value=resp(js=BRAVE_JSON)) as m:
            out = web_search({"query": "q", "count": 2}, self.ctx(brave="BSA-test-key-123456"))
        self.assertEqual(m.call_args.kwargs["headers"]["X-Subscription-Token"], "BSA-test-key-123456")
        self.assertIn("Brave Two", out)
        self.assertNotIn("BSA-test-key-123456", out)

    def test_http_and_network_errors(self):
        with mock.patch("agent_app.search.requests.request", return_value=resp(status=503)):
            with self.assertRaises(ToolError):
                web_search({"query": "q"}, self.ctx())
        with mock.patch("agent_app.search.requests.request", side_effect=requests.ConnectionError("x")):
            with self.assertRaises(ToolError):
                web_search({"query": "q"}, self.ctx())

    def test_empty_results_and_bad_input(self):
        with mock.patch("agent_app.search.requests.request", return_value=resp(text="<html></html>")):
            self.assertIn("No results", web_search({"query": "zzz"}, self.ctx()))
        with self.assertRaises(ToolError):
            web_search({"query": ""}, self.ctx())
        with self.assertRaises(ToolError):
            web_search({"query": "x" * 400}, self.ctx())

    def test_registered_and_run_python_toggle(self):
        self.assertIn("web_search", build_registry().names())
        self.assertIn("run_python", build_registry().names())
        self.assertNotIn("run_python", build_registry(enable_run_python=False).names())

    def test_end_to_end_agent_uses_search(self):
        from test_agent import VERIFY_OK, text, tool
        c = self.app([tool("web_search", {"query": "example"}), text("Found example.com."), VERIFY_OK])
        with mock.patch("agent_app.search.requests.request", return_value=resp(text=DDG_HTML)):
            tid = self.run_task(c, "search example")
        t = self.task(c, tid)
        self.assertEqual(t["task"]["status"], "completed_verified")
        self.assertIn("UNTRUSTED SEARCH RESULTS", json.dumps(self.llm.calls[1][1]))

    def test_config_toggle_from_env(self):
        from agent_app.config import Config
        env = {"AGENT_DATA_DIR": self.tmp.name, "AGENT_TOKEN": "t" * 32, "AGENT_ENABLE_RUN_PYTHON": "0",
               "BRAVE_API_KEY": "BSA-xxxxxxxxxxxx"}
        cfg = Config.from_env(env=env, load_dotenv=False)
        self.assertFalse(cfg.enable_run_python)
        self.assertIn("BSA-xxxxxxxxxxxx", cfg.secrets_list())


if __name__ == "__main__":
    unittest.main(verbosity=2)
