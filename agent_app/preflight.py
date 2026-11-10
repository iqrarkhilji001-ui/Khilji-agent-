"""Startup / configuration checks. Nothing here ever prints or returns a secret value."""
from __future__ import annotations

from typing import Any

from .config import Config
from .llm import LLMError
from .redact import redact

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def config_warnings(cfg: Config) -> list[dict]:
    """Offline checks (no network). Each item: {"level": "error"|"warn"|"info", "message": str}."""
    out: list[dict] = []
    if not cfg.api_key:
        out.append({"level": "error", "message": "ANTHROPIC_API_KEY is not set. The page will open, but tasks "
                    "cannot run. Set it in .env (local) or in your host's environment-variable settings."})
    elif not cfg.api_key.startswith("sk-ant-"):
        out.append({"level": "warn", "message": "ANTHROPIC_API_KEY does not start with 'sk-ant-'. Anthropic API "
                    "keys normally do; check that you copied the whole key."})
    if len(cfg.token) < 32:
        out.append({"level": "warn", "message": "AGENT_TOKEN is shorter than 32 characters. Use a longer random "
                    "token before exposing this on the internet."})
    if cfg.ephemeral_storage or str(cfg.data_dir).startswith("/tmp"):
        out.append({"level": "warn", "message": "Storage is temporary on this host: task history, saved memory and "
                    "workspace files, and paired phones can be erased at any restart, redeploy or sleep. Do not rely on them; paired phones must then be paired again."})
    if cfg.enable_run_python and cfg.host not in LOCAL_HOSTS:
        out.append({"level": "warn", "message": "Code execution (run_python) is enabled while the server listens "
                    "beyond localhost. Set AGENT_ENABLE_RUN_PYTHON=0 on shared or public hosts."})
    if cfg.brave_api_key:
        out.append({"level": "info", "message": "BRAVE_API_KEY is set: web_search will use Brave's metered API."})
    else:
        out.append({"level": "info", "message": "web_search uses DuckDuckGo's unofficial HTML page. It is untested "
                    "against the live service until you press 'Test web search'."})
    return out


def check_api(cfg: Config, llm: Any) -> dict:
    """One tiny real model call to prove key + model id work. Costs a few tokens."""
    if not cfg.api_key:
        return {"ok": False, "message": "ANTHROPIC_API_KEY is not set."}
    try:
        resp = llm.create("Reply with the single word OK.", [{"role": "user", "content": "ping"}], None, 256)
    except LLMError as exc:
        text = redact(str(exc), cfg.secrets_list())
        hint = ""
        for code, advice in (("401", "The API key was rejected. Re-copy it from console.anthropic.com."),
                             ("403", "The key is not allowed to use this model or API."),
                             ("404", f"Model id '{cfg.model}' was not found. Check ANTHROPIC_MODEL."),
                             ("429", "Rate limit or no remaining credit. Check your Anthropic billing and limits."),
                             ("400", "The API rejected the request; see the message above.")):
            if f"error {code}" in text:
                hint = " " + advice
                break
        return {"ok": False, "message": text + hint}
    return {"ok": True, "message": f"API call succeeded with model '{resp.get('model') or cfg.model}'."}


def check_search(cfg: Config) -> dict:
    """One real search request through the same code the agent uses."""
    from .search import web_search
    from .tools import ToolContext, ToolError
    try:
        out = web_search({"query": "open source software", "count": 1}, ToolContext(cfg, None))  # type: ignore[arg-type]
    except ToolError as exc:
        return {"ok": False, "message": redact(str(exc), cfg.secrets_list())}
    if out.startswith("No results"):
        return {"ok": False, "message": "The search request worked but returned no results; the page format "
                                        "may have changed."}
    return {"ok": True, "message": "Live search returned results."}
