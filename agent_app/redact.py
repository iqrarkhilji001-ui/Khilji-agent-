"""Keep secrets out of logs, step records and memory."""
from __future__ import annotations

import logging
import re
from typing import Iterable

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bdvc_[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

MARK = "[REDACTED]"


def redact(text: str, known_secrets: Iterable[str] = ()) -> str:
    if not text:
        return text
    for secret in known_secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, MARK)
    for pattern in _PATTERNS:
        text = pattern.sub(MARK, text)
    return text


def scrub_known(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Replace only exact known secret values (used for user-visible results, where pattern-based
    redaction could mangle legitimate text)."""
    for secret in known_secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, MARK)
    return text


def looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _PATTERNS)


class RedactingFilter(logging.Filter):
    def __init__(self, known_secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets = list(known_secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage(), self._secrets)
            record.args = ()
        except Exception:  # never let logging break the app
            pass
        return True
