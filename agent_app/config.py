"""Configuration. Secrets come from environment variables or a local .env file, never from code."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    data_dir: Path
    workspace: Path
    token: str
    api_key: str = ""
    model: str = "claude-sonnet-5-5"
    api_base: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    host: str = "127.0.0.1"
    port: int = 8080
    max_steps: int = 12
    max_tokens: int = 4096
    request_timeout: int = 120
    daily_ai_limit_seconds: int = 600
    auth_fail_delay: float = 0.5
    token_generated: bool = False
    brave_api_key: str = ""          # optional; Brave's API is paid/metered, so it is off unless you set it
    enable_run_python: bool = True   # set AGENT_ENABLE_RUN_PYTHON=0 on any shared/public host
    ephemeral_storage: bool = False  # True when the host wipes local files (e.g. Render free tier)
    trust_proxy: bool = False        # True behind a reverse proxy (Render): use X-Forwarded-For for client IPs
    phone_wait_seconds: int = 20     # how long the phone_action tool waits for the phone to confirm
    device_token_days: int = 90      # lifetime of a paired device's credential
    notify_on_complete: bool = False  # opt-in: show a generic "task finished" notification on paired phones

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def token_file(self) -> Path:
        return self.data_dir / ".agent_token"

    def secrets_list(self) -> list[str]:
        """Exact secret values that must never appear in logs or stored step records."""
        return [s for s in (self.api_key, self.token, self.brave_api_key) if s]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, load_dotenv: bool = True) -> "Config":
        if env is None:
            if load_dotenv:
                load_env_file(PROJECT_DIR / ".env")
            env = os.environ

        data_dir = Path(env.get("AGENT_DATA_DIR") or PROJECT_DIR / "data").expanduser().resolve()
        workspace = Path(env.get("AGENT_WORKSPACE") or data_dir / "workspace").expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        token = (env.get("AGENT_TOKEN") or "").strip()
        generated = False
        token_file = data_dir / ".agent_token"
        if not token:
            if token_file.is_file():
                token = token_file.read_text(encoding="utf-8").strip()
            if not token:
                token = secrets.token_urlsafe(32)
                fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(token)
                generated = True
        if len(token) < 16:
            raise ConfigError("AGENT_TOKEN must be at least 16 characters.")

        def _int(name: str, default: int, lo: int, hi: int) -> int:
            raw = env.get(name)
            if raw in (None, ""):
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} must be an integer.") from exc
            if not lo <= value <= hi:
                raise ConfigError(f"{name} must be between {lo} and {hi}.")
            return value

        return cls(
            data_dir=data_dir,
            workspace=workspace,
            token=token,
            api_key=(env.get("ANTHROPIC_API_KEY") or "").strip(),
            model=(env.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5").strip(),
            api_base=(env.get("ANTHROPIC_API_BASE") or "https://api.anthropic.com").rstrip("/"),
            host=(env.get("AGENT_HOST") or "127.0.0.1").strip(),
            port=_int("AGENT_PORT", 8080, 1, 65535),
            max_steps=_int("AGENT_MAX_STEPS", 12, 1, 50),
            max_tokens=_int("AGENT_MAX_TOKENS", 8000, 256, 16000),  # Sonnet 5.5 thinking counts toward this
            daily_ai_limit_seconds=_int("AGENT_DAILY_AI_SECONDS", 600, 60, 86400),
            token_generated=generated,
            brave_api_key=(env.get("BRAVE_API_KEY") or "").strip(),
            enable_run_python=(env.get("AGENT_ENABLE_RUN_PYTHON", "1").strip().lower() not in ("0", "false", "no", "off")),
            ephemeral_storage=(env.get("AGENT_EPHEMERAL_STORAGE", "0").strip().lower() in ("1", "true", "yes", "on")),
            trust_proxy=(env.get("AGENT_TRUST_PROXY", "0").strip().lower() in ("1", "true", "yes", "on")),
            phone_wait_seconds=_int("AGENT_PHONE_WAIT", 20, 0, 60),
            device_token_days=_int("AGENT_DEVICE_TOKEN_DAYS", 90, 1, 365),
            notify_on_complete=(env.get("AGENT_NOTIFY_ON_COMPLETE", "0").strip().lower() in ("1", "true", "yes", "on")),
        )
