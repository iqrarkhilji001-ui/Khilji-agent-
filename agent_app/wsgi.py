"""Production entry point:  gunicorn --workers 1 --threads 8 agent_app.wsgi:app

Use exactly ONE worker process: running tasks live in that process's memory."""
import logging
import os

from . import preflight
from .config import Config, ConfigError
from .server import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("agent.startup")


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


# On hosts with temporary storage a generated token would change at every restart and lock you out,
# so a fixed AGENT_TOKEN from the host's settings is mandatory here.
if not (os.environ.get("AGENT_TOKEN") or "").strip():
    raise ConfigError("AGENT_TOKEN is not set. Add it in your host's environment-variable settings "
                      "(a random string of 32+ characters), then redeploy.")

# Arbitrary code execution must not be reachable on a hosted deployment. The production entry point therefore
# keeps run_python OFF even when AGENT_ENABLE_RUN_PYTHON=1, unless the operator explicitly accepts the risk with
# AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION=1.
os.environ.setdefault("AGENT_ENABLE_RUN_PYTHON", "0")
_cfg = Config.from_env()
if _cfg.enable_run_python and not _truthy("AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION"):
    _cfg.enable_run_python = False
    _log.warning("AGENT_ENABLE_RUN_PYTHON=1 was ignored: code execution stays off in the production entry point. "
                 "Set AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION=1 only if you fully accept the risk.")

for _item in preflight.config_warnings(_cfg):
    _log.log(logging.ERROR if _item["level"] == "error" else logging.WARNING if _item["level"] == "warn"
             else logging.INFO, _item["message"])

app = create_app(_cfg)
