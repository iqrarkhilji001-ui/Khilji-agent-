"""Thin client for the Anthropic Messages API (plain HTTPS via requests; no SDK required)."""
from __future__ import annotations

import time
from typing import Callable, Optional

import requests

from .config import Config
from .redact import redact


class LLMError(Exception):
    """Raised when the model call fails. Messages never contain the API key."""


class AnthropicClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504, 529}

    def __init__(self, cfg: Config, sleep: Callable[[float], None] = time.sleep, max_retries: int = 3):
        self.cfg = cfg
        self._sleep = sleep
        self._max_retries = max_retries

    def create(self, system: str, messages: list, tools: Optional[list] = None,
               max_tokens: Optional[int] = None) -> dict:
        if not self.cfg.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not configured.")
        body: dict = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            # tool_choice is left at the API default ("auto"). Sonnet 5.5 rejects forced tool use, and the
            # agent loop handles several tool calls in one turn, so no extra tool_choice options are sent.
            body["tools"] = tools
        headers = {
            "x-api-key": self.cfg.api_key,
            "anthropic-version": self.cfg.anthropic_version,
            "content-type": "application/json",
        }
        url = f"{self.cfg.api_base}/v1/messages"
        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=self.cfg.request_timeout)
            except requests.RequestException as exc:
                last_error = f"network error: {type(exc).__name__}"
                if attempt < self._max_retries:
                    self._sleep(min(2 ** attempt, 8))
                    continue
                raise LLMError(last_error) from None
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise LLMError("model API returned invalid JSON") from None
                if not isinstance(data, dict) or "content" not in data:
                    raise LLMError("model API returned an unexpected response shape")
                return data
            detail = ""
            try:
                err = resp.json().get("error", {})
                detail = str(err.get("message", ""))[:300]
            except Exception:
                pass
            last_error = f"model API error {resp.status_code}" + (f": {detail}" if detail else "")
            if resp.status_code in self.RETRY_STATUSES and attempt < self._max_retries:
                self._sleep(min(2 ** attempt, 8))
                continue
            raise LLMError(redact(last_error, self.cfg.secrets_list()))
        raise LLMError(redact(last_error, self.cfg.secrets_list()))
