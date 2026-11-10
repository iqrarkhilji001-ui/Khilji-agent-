"""SQLite persistence: tasks, step log, approvals, memory."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

STATUSES = (
    "pending",
    "running",
    "waiting_approval",
    "completed_verified",
    "completed_unverified",
    "failed",
    "cancelled",
)
ACTIVE_STATUSES = ("pending", "running", "waiting_approval")
MEMORY_KINDS = ("project", "preference", "decision", "done", "problem", "next_step", "note")

_TASK_FIELDS = {
    "status", "phase", "plan", "result", "error", "verification", "verified",
    "messages", "partial", "input_tokens", "output_tokens",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    phase TEXT NOT NULL DEFAULT 'receive',
    plan TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    verification TEXT NOT NULL DEFAULT '',
    verified INTEGER,
    messages TEXT NOT NULL DEFAULT '[]',
    partial TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tool_use_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT,
    state TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    task_id INTEGER,
    action TEXT NOT NULL,
    args TEXT NOT NULL,
    status TEXT NOT NULL,
    idem_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_commands_device ON commands(device_id, status);
"""


COMMAND_STATUSES = ("approved", "running", "completed", "failed", "cancelled", "expired")
COMMAND_TERMINAL = ("completed", "failed", "cancelled", "expired")
REDELIVER_AFTER_SECONDS = 60
PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1
PAIRING_CODE_TTL_SECONDS = 600


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def hash_secret(value: str) -> str:
    """Device tokens and pairing codes are high-entropy random strings, so a plain SHA-256 is sufficient."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


class Store:
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        try:
            self._db.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ---- helpers -------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- tasks ---------------------------------------------------------
    def create_task(self, request: str) -> int:
        ts = now()
        messages = json.dumps([{"role": "user", "content": request}])
        cur = self._exec(
            "INSERT INTO tasks (request, messages, created_at, updated_at) VALUES (?,?,?,?)",
            (request, messages, ts, ts),
        )
        return int(cur.lastrowid)

    def get_task(self, task_id: int, include_messages: bool = False) -> Optional[dict]:
        row = self._one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return None
        if include_messages:
            row["messages"] = json.loads(row["messages"])
            row["partial"] = json.loads(row["partial"])
        else:
            row.pop("messages", None)
            row.pop("partial", None)
        return row

    def list_tasks(self, limit: int = 30) -> list[dict]:
        rows = self._all(
            "SELECT id, request, status, phase, verified, created_at, updated_at "
            "FROM tasks ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        )
        return rows

    def update_task(self, task_id: int, **fields: Any) -> None:
        bad = set(fields) - _TASK_FIELDS
        if bad:
            raise ValueError(f"unknown task fields: {sorted(bad)}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"bad status: {fields['status']}")
        for key in ("messages", "partial"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(
            f"UPDATE tasks SET {cols}, updated_at = ? WHERE id = ?",
            (*fields.values(), now(), task_id),
        )

    def add_tokens(self, task_id: int, input_tokens: int, output_tokens: int) -> None:
        self._exec(
            "UPDATE tasks SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
            "updated_at = ? WHERE id = ?",
            (int(input_tokens or 0), int(output_tokens or 0), now(), task_id),
        )

    def delete_task(self, task_id: int) -> bool:
        return self._exec("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0

    def clear_history(self) -> int:
        return self._exec(
            "DELETE FROM tasks WHERE status NOT IN ('pending','running','waiting_approval')"
        ).rowcount

    def mark_interrupted(self) -> int:
        """After a restart, tasks that were 'running' or 'pending' cannot continue; waiting ones can."""
        return self._exec(
            "UPDATE tasks SET status='failed', error='Interrupted by a server restart.', updated_at=? "
            "WHERE status IN ('running','pending')",
            (now(),),
        ).rowcount

    # ---- steps ---------------------------------------------------------
    def add_step(self, task_id: int, kind: str, summary: str, detail: str = "") -> None:
        self._exec(
            "INSERT INTO steps (task_id, ts, kind, summary, detail) VALUES (?,?,?,?,?)",
            (task_id, now(), kind, summary[:500], detail[:4000]),
        )

    def get_steps(self, task_id: int) -> list[dict]:
        return self._all("SELECT * FROM steps WHERE task_id = ? ORDER BY id", (task_id,))

    # ---- approvals -----------------------------------------------------
    def create_approval(self, task_id: int, tool_use_id: str, tool_name: str,
                        tool_input: dict, description: str) -> int:
        cur = self._exec(
            "INSERT INTO approvals (task_id, tool_use_id, tool_name, tool_input, description, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, tool_use_id, tool_name, json.dumps(tool_input, sort_keys=True), description[:2500], now()),
        )
        return int(cur.lastrowid)

    def get_approval(self, approval_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM approvals WHERE id = ?", (approval_id,))

    def find_approval(self, task_id: int, tool_use_id: str) -> Optional[dict]:
        return self._one(
            "SELECT * FROM approvals WHERE task_id = ? AND tool_use_id = ? ORDER BY id DESC LIMIT 1",
            (task_id, tool_use_id),
        )

    def pending_approvals(self, task_id: Optional[int] = None) -> list[dict]:
        if task_id is None:
            return self._all("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id")
        return self._all(
            "SELECT * FROM approvals WHERE status = 'pending' AND task_id = ? ORDER BY id", (task_id,)
        )

    def decide_approval(self, approval_id: int, status: str) -> bool:
        if status not in ("approved", "denied"):
            raise ValueError("status must be approved or denied")
        return self._exec(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, now(), approval_id),
        ).rowcount > 0

    # ---- memory --------------------------------------------------------
    def add_memory(self, kind: str, text: str) -> int:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"kind must be one of {MEMORY_KINDS}")
        cur = self._exec(
            "INSERT INTO memory (kind, text, created_at) VALUES (?,?,?)", (kind, text, now())
        )
        return int(cur.lastrowid)

    def list_memory(self) -> list[dict]:
        return self._all("SELECT * FROM memory ORDER BY id")

    def delete_memory(self, memory_id: int) -> bool:
        return self._exec("DELETE FROM memory WHERE id = ?", (memory_id,)).rowcount > 0

    def clear_memory(self) -> int:
        return self._exec("DELETE FROM memory").rowcount

    # ---- devices (Android companion) -----------------------------------
    def create_pairing_code(self) -> tuple[str, str]:
        """Return (display_code, expires_at). Older unused codes are invalidated; only a hash is stored."""
        raw = "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(8))
        ts, exp = now(), iso_in(PAIRING_CODE_TTL_SECONDS)
        with self._lock:
            self._db.execute("UPDATE pairing_codes SET used_at = ? WHERE used_at IS NULL", (ts,))
            self._db.execute("INSERT INTO pairing_codes (code_hash, created_at, expires_at) VALUES (?,?,?)",
                             (hash_secret(raw), ts, exp))
            self._db.commit()
        return f"{raw[:4]}-{raw[4:]}", exp

    def consume_pairing_code(self, code: str, name: str, token_days: int) -> Optional[tuple[int, str, str]]:
        """Atomically use a one-time code. Returns (device_id, plaintext_token, expires_at) or None."""
        normalized = normalize_code(code)
        if len(normalized) != 8:
            return None
        ts = now()
        with self._lock:
            cur = self._db.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                (ts, hash_secret(normalized), ts))
            if cur.rowcount != 1:
                self._db.commit()
                return None
            token = "dvc_" + secrets.token_urlsafe(32)
            expires = iso_in(token_days * 86400)
            cur = self._db.execute(
                "INSERT INTO devices (name, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
                (name[:60], hash_secret(token), ts, expires))
            self._db.commit()
            return int(cur.lastrowid), token, expires

    def device_by_token(self, token: str) -> tuple[str, Optional[dict]]:
        """Return (status, device). status is 'ok', 'revoked', 'expired' or 'unknown'."""
        if not token or len(token) > 200:
            return "unknown", None
        row = self._one("SELECT * FROM devices WHERE token_hash = ?", (hash_secret(token),))
        if row is None:
            return "unknown", None
        if row["revoked_at"]:
            return "revoked", row
        if row["expires_at"] <= now():
            return "expired", row
        return "ok", row

    def get_device(self, device_id: int) -> Optional[dict]:
        row = self._one("SELECT * FROM devices WHERE id = ?", (device_id,))
        return self._public_device(row) if row else None

    @staticmethod
    def _public_device(row: dict) -> dict:
        out = {k: v for k, v in row.items() if k != "token_hash"}
        try:
            out["state"] = json.loads(out.get("state") or "{}")
        except ValueError:
            out["state"] = {}
        if out["revoked_at"]:
            out["status"] = "revoked"
        elif out["expires_at"] <= now():
            out["status"] = "expired"
        else:
            out["status"] = "active"
        return out

    def list_devices(self) -> list[dict]:
        return [self._public_device(r) for r in self._all("SELECT * FROM devices ORDER BY id DESC")]

    def active_devices(self) -> list[dict]:
        return [d for d in self.list_devices() if d["status"] == "active"]

    def touch_device(self, device_id: int, state: Optional[dict] = None) -> None:
        if state is None:
            self._exec("UPDATE devices SET last_seen = ? WHERE id = ?", (now(), device_id))
        else:
            self._exec("UPDATE devices SET last_seen = ?, state = ? WHERE id = ?",
                       (now(), json.dumps(state), device_id))

    def revoke_device(self, device_id: int) -> bool:
        ts = now()
        with self._lock:
            cur = self._db.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                   (ts, device_id))
            if cur.rowcount:
                self._db.execute(
                    "UPDATE commands SET status = 'cancelled', result = 'Device was revoked.', updated_at = ? "
                    "WHERE device_id = ? AND status IN ('approved','running')", (ts, device_id))
            self._db.commit()
            return cur.rowcount > 0

    # ---- phone commands -------------------------------------------------
    @staticmethod
    def _command(row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        row = dict(row)
        try:
            row["args"] = json.loads(row["args"])
        except ValueError:
            row["args"] = {}
        return row

    def create_command(self, device_id: int, task_id: Optional[int], action: str, args: dict,
                       ttl_seconds: float, idem_key: Optional[str]) -> tuple[dict, bool]:
        """Insert a command in 'approved' state. A repeated idem_key returns the existing row (created=False)."""
        ts = now()
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO commands (device_id, task_id, action, args, status, idem_key, created_at, "
                "updated_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (device_id, task_id, action, json.dumps(args, sort_keys=True), "approved", idem_key, ts, ts,
                 iso_in(ttl_seconds)))
            self._db.commit()
            created = cur.rowcount == 1
            row = self._one("SELECT * FROM commands WHERE id = ?", (cur.lastrowid,)) if created else \
                self._one("SELECT * FROM commands WHERE idem_key = ?", (idem_key,))
        return self._command(row), created

    def get_command(self, command_id: int) -> Optional[dict]:
        self.expire_commands()
        return self._command(self._one("SELECT * FROM commands WHERE id = ?", (command_id,)))

    def list_commands(self, limit: int = 30, device_id: Optional[int] = None) -> list[dict]:
        self.expire_commands()
        limit = max(1, min(int(limit), 200))
        if device_id is None:
            rows = self._all("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self._all("SELECT * FROM commands WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                             (device_id, limit))
        return [self._command(r) for r in rows]

    def expire_commands(self) -> int:
        ts = now()
        with self._lock:
            a = self._db.execute(
                "UPDATE commands SET status='expired', result='Not delivered before it expired.', updated_at=? "
                "WHERE status='approved' AND expires_at <= ?", (ts, ts)).rowcount
            b = self._db.execute(
                "UPDATE commands SET status='failed', result='The phone never reported a result.', updated_at=? "
                "WHERE status='running' AND expires_at <= ?", (ts, ts)).rowcount
            self._db.commit()
        return a + b

    def claim_commands(self, device_id: int, limit: int = 10) -> list[dict]:
        """Hand pending commands to the device and mark them running. Commands already 'running' are
        re-delivered after REDELIVER_AFTER_SECONDS (the device must treat command ids idempotently)."""
        self.expire_commands()
        ts = now()
        cutoff = iso_in(-REDELIVER_AFTER_SECONDS)
        with self._lock:
            rows = self._all(
                "SELECT * FROM commands WHERE device_id = ? AND (status = 'approved' OR "
                "(status = 'running' AND delivered_at <= ?)) ORDER BY id LIMIT ?", (device_id, cutoff, limit))
            for r in rows:
                self._db.execute(
                    "UPDATE commands SET status='running', delivered_at=?, attempts=attempts+1, updated_at=? "
                    "WHERE id = ?", (ts, ts, r["id"]))
            self._db.commit()
        return [self._command(self._one("SELECT * FROM commands WHERE id = ?", (r["id"],))) for r in rows]

    def report_result(self, device_id: int, command_id: int, status: str, detail: str) -> tuple[str, Optional[dict]]:
        """Return ('ok'|'duplicate'|'not_found'|'conflict', command)."""
        if status not in ("completed", "failed"):
            raise ValueError("status must be completed or failed")
        with self._lock:
            row = self._one("SELECT * FROM commands WHERE id = ? AND device_id = ?", (command_id, device_id))
            if row is None:
                return "not_found", None
            if row["status"] in COMMAND_TERMINAL:
                return ("duplicate" if row["status"] == status else "conflict"), self._command(row)
            if row["status"] != "running":
                return "conflict", self._command(row)
            self._db.execute("UPDATE commands SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                             (status, detail[:500], now(), command_id))
            self._db.commit()
            return "ok", self._command(self._one("SELECT * FROM commands WHERE id = ?", (command_id,)))

    def cancel_command(self, command_id: int) -> tuple[bool, Optional[dict]]:
        """Only a command the phone has not received yet can be cancelled."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE commands SET status='cancelled', result='Cancelled by you before delivery.', updated_at=? "
                "WHERE id = ? AND status = 'approved'", (now(), command_id))
            self._db.commit()
            row = self._command(self._one("SELECT * FROM commands WHERE id = ?", (command_id,)))
        return cur.rowcount > 0, row
