"""Small in-memory rate limiting. State is per process (the app runs one worker) and resets on restart."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple


class RateLimiter:
    """Sliding-window limiter: at most `limit` events per `window` seconds for each key."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 5000):
        self._clock = clock
        self._max_keys = max_keys
        self._events: Dict[Tuple[str, str], Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, q: Deque[float], window: float, now: float) -> None:
        while q and now - q[0] >= window:
            q.popleft()

    def count(self, bucket: str, key: str, window: float) -> int:
        with self._lock:
            q = self._events.get((bucket, key))
            if not q:
                return 0
            self._prune(q, window, self._clock())
            return len(q)

    def record(self, bucket: str, key: str, window: float) -> None:
        """Record an event without checking a limit (used for failed-login counting)."""
        with self._lock:
            now = self._clock()
            if len(self._events) >= self._max_keys:
                for k in [k for k, q in self._events.items() if not q or now - q[-1] >= 3600][:500]:
                    self._events.pop(k, None)
            q = self._events.setdefault((bucket, key), deque())
            self._prune(q, window, now)
            q.append(now)

    def allow(self, bucket: str, key: str, limit: int, window: float) -> bool:
        """Record the event and return False when it exceeds the limit."""
        with self._lock:
            now = self._clock()
            q = self._events.setdefault((bucket, key), deque())
            self._prune(q, window, now)
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def retry_after(self, bucket: str, key: str, window: float) -> int:
        with self._lock:
            q = self._events.get((bucket, key))
            if not q:
                return 1
            return max(1, int(window - (self._clock() - q[0])) + 1)
