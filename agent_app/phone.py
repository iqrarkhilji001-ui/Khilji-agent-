"""Phone actions: a strict allowlist, argument validation, and the tools the agent can call.

The agent never talks to the phone directly. It queues a *command* for a paired device; the Android companion
polls over HTTPS, runs the command with its own OS permissions, and reports the result. A command is only
described as done to the user when the phone itself reported success.

Allowlist:
  notify             show a notification                         (runs automatically)
  schedule_reminder  schedule a local reminder on the phone      (runs automatically)
  cancel_reminder    cancel a reminder this assistant scheduled  (runs automatically)
  open_url           show a notification that opens an https URL (needs the user's approval)
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from .db import COMMAND_TERMINAL
from .tools import ToolContext, ToolError, _int, _str

ACTIONS = ("notify", "schedule_reminder", "cancel_reminder", "open_url")
APPROVAL_ACTIONS = ("open_url",)
MAX_TITLE = 80
MAX_BODY = 500
MIN_REMINDER_SECONDS = 5
MAX_REMINDER_SECONDS = 365 * 86400
DELIVERY_TTL_SECONDS = 15 * 60
MAX_COMMAND_TTL_SECONDS = 24 * 3600
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean_text(value: Any, field: str, max_len: int, required: bool) -> str:
    if value is None or value == "":
        if required:
            raise ToolError(f"Missing required field: {field}")
        return ""
    if not isinstance(value, str):
        raise ToolError(f"Field {field} must be a string.")
    text = _CONTROL.sub("", value).strip()
    if required and not text:
        raise ToolError(f"Field {field} must not be empty.")
    if len(text) > max_len:
        raise ToolError(f"Field {field} is too long (max {max_len} characters).")
    return text


def validate_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ToolError("Missing required field: url")
    url = url.strip()
    if len(url) > 1000 or re.search(r"\s", url):
        raise ToolError("URL is too long or contains whitespace.")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ToolError("Only https URLs are allowed.")
    if parsed.username or parsed.password:
        raise ToolError("URLs with embedded credentials are not allowed.")
    host = parsed.hostname
    if not host or "." not in host:
        raise ToolError("URL must have a public host name.")
    try:
        ipaddress.ip_address(host)
        raise ToolError("URLs that use an IP address are not allowed.")
    except ValueError:
        pass
    try:
        port = parsed.port
    except ValueError:
        raise ToolError("Invalid port in URL.") from None
    if port not in (None, 443):
        raise ToolError("Only the default HTTPS port is allowed.")
    return url


def parse_when(inp: dict, now_ts: float) -> int:
    """Return the reminder time as epoch milliseconds. Exactly one of 'at' (ISO-8601 with UTC offset) or
    'in_seconds' must be given."""
    at, delay = inp.get("at"), inp.get("in_seconds")
    if (at in (None, "")) == (delay is None):
        raise ToolError("Give exactly one of 'at' (ISO-8601 time with UTC offset) or 'in_seconds'.")
    if delay is not None:
        if isinstance(delay, bool) or not isinstance(delay, int):
            raise ToolError("Field in_seconds must be an integer.")
        target = now_ts + delay
    else:
        if not isinstance(at, str):
            raise ToolError("Field at must be a string.")
        try:
            moment = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ToolError("Field at is not a valid ISO-8601 time, e.g. 2026-11-01T09:00:00+05:00.") from None
        if moment.tzinfo is None:
            raise ToolError("Field at must include a UTC offset, e.g. +05:00 for Pakistan.")
        target = moment.astimezone(timezone.utc).timestamp()
    if target < now_ts + MIN_REMINDER_SECONDS:
        raise ToolError(f"The reminder time must be at least {MIN_REMINDER_SECONDS} seconds in the future.")
    if target > now_ts + MAX_REMINDER_SECONDS:
        raise ToolError("The reminder time is more than a year away.")
    return int(target * 1000)


def build_command(action: Any, inp: dict, now_ts: float) -> tuple[dict, float]:
    """Validate arguments for an allowlisted action. Returns (args, delivery_ttl_seconds)."""
    if action not in ACTIONS:
        raise ToolError(f"Unsupported phone action. Allowed actions: {', '.join(ACTIONS)}.")
    if action == "notify":
        return ({"title": _clean_text(inp.get("title"), "title", MAX_TITLE, True),
                 "body": _clean_text(inp.get("body"), "body", MAX_BODY, False)}, DELIVERY_TTL_SECONDS)
    if action == "schedule_reminder":
        at_ms = parse_when(inp, now_ts)
        args = {"title": _clean_text(inp.get("title"), "title", MAX_TITLE, True),
                "body": _clean_text(inp.get("body"), "body", MAX_BODY, False), "at_epoch_ms": at_ms}
        ttl = min(MAX_COMMAND_TTL_SECONDS, max(30.0, at_ms / 1000 - now_ts))
        return args, ttl
    if action == "cancel_reminder":
        rid = inp.get("reminder_id")
        if isinstance(rid, bool) or not isinstance(rid, int) or rid < 1:
            raise ToolError("Field reminder_id must be the command id of an earlier schedule_reminder.")
        return {"reminder_id": rid}, DELIVERY_TTL_SECONDS
    return {"url": validate_url(inp.get("url"))}, DELIVERY_TTL_SECONDS


# ----------------------------------------------------------------- tools
def _pick_device(inp: dict, ctx: ToolContext) -> dict:
    devices = ctx.store.active_devices()
    if not devices:
        raise ToolError("No paired Android device. Open the Devices tab, create a pairing code, and enter it in "
                        "the Android companion app. The assistant cannot pair a device by itself.")
    wanted = inp.get("device_id")
    if wanted is not None:
        if isinstance(wanted, bool) or not isinstance(wanted, int):
            raise ToolError("Field device_id must be an integer.")
        for d in devices:
            if d["id"] == wanted:
                return d
        raise ToolError(f"No active device with id {wanted}.")
    if len(devices) > 1:
        names = ", ".join(f"{d['id']} ({d['name']})" for d in devices)
        raise ToolError(f"Several devices are paired; pass device_id. Active devices: {names}.")
    return devices[0]


def phone_status(inp: dict, ctx: ToolContext) -> str:
    devices = ctx.store.list_devices()
    if not devices:
        return "No Android device has been paired."
    lines = []
    for d in devices:
        state = d["state"]
        lines.append(f"device {d['id']} '{d['name']}': {d['status']}; last contact: {d['last_seen'] or 'never'}; "
                     f"notifications allowed: {state.get('notifications_enabled', 'unknown')}; "
                     f"reminders pending on phone: {state.get('pending_reminders', 'unknown')}")
    recent = ctx.store.list_commands(10)
    if recent:
        lines.append("recent commands:")
        lines += [f"  #{c['id']} {c['action']} -> {c['status']}" + (f" ({c['result']})" if c["result"] else "")
                  for c in recent]
    return "\n".join(lines)


_FINISHED_TEXT = {
    "completed_verified": ("Assistant task finished", "completed"),
    "completed_unverified": ("Assistant task finished", "completed (not independently verified)"),
    "failed": ("Assistant task failed", "failed"),
}


def notify_task_finished(cfg: Any, store: Any, task_id: int, status: str) -> int:
    """Opt-in (AGENT_NOTIFY_ON_COMPLETE=1): queue ONE generic notification per active paired phone when a task ends.

    The text never contains the request or the result, because notifications can show on a locked screen. This is a
    notification (a safe action), so it needs no approval; the phone still reports whether it was really shown.
    Returns how many commands were newly queued. Repeating the call for the same task queues nothing new."""
    if not getattr(cfg, "notify_on_complete", False) or status not in _FINISHED_TEXT:
        return 0
    title, word = _FINISHED_TEXT[status]
    args = {"title": title, "body": f"Task #{task_id} {word}. Open the assistant to read the result."}
    queued = 0
    for device in store.active_devices():
        _, created = store.create_command(device["id"], task_id, "notify", args, DELIVERY_TTL_SECONDS,
                                          f"task-finished:{task_id}:{device['id']}")
        queued += 1 if created else 0
    return queued


def phone_action_approval(inp: dict, ctx: ToolContext) -> Optional[str]:
    if inp.get("action") not in APPROVAL_ACTIONS:
        return None
    url = validate_url(inp.get("url"))  # ToolError -> no approval card; run() then rejects the input
    return (f"SHOW A LINK on your phone. A notification will appear; if you tap it, your phone opens: {url}. "
            "Nothing opens until you tap.")


def _wait_for_result(ctx: ToolContext, command_id: int, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        cmd = ctx.store.get_command(command_id)
        if cmd["status"] in COMMAND_TERMINAL or time.monotonic() >= deadline:
            return cmd
        time.sleep(0.5)


def phone_action(inp: dict, ctx: ToolContext) -> str:
    action = inp.get("action")
    device = _pick_device(inp, ctx)
    args, ttl = build_command(action, inp, time.time())
    if action == "cancel_reminder":
        target = ctx.store.get_command(args["reminder_id"])
        if not target or target["device_id"] != device["id"] or target["action"] != "schedule_reminder":
            raise ToolError("reminder_id does not match a reminder this assistant scheduled on that device.")
    key_src = f"{ctx.task_id}:{ctx.tool_use_id}" if ctx.tool_use_id else f"manual:{secrets.token_hex(8)}"
    idem = hashlib.sha256(key_src.encode()).hexdigest()
    cmd, created = ctx.store.create_command(device["id"], ctx.task_id, action, args, ttl, idem)
    cmd = _wait_for_result(ctx, cmd["id"], ctx.cfg.phone_wait_seconds)
    head = f"command_id: {cmd['id']}\naction: {action}\nstatus: {cmd['status']}"
    if not created:
        head += "\n(this exact call was already queued earlier; no duplicate was created)"
    if cmd["status"] == "completed":
        return f"{head}\nphone_reported: {cmd['result'] or 'ok'}"
    if cmd["status"] in ("failed", "cancelled", "expired"):
        raise ToolError(f"{head}\nThe phone action did NOT succeed: {cmd['result'] or cmd['status']}. "
                        "Tell the user it failed; do not say it was done.")
    seen = device["last_seen"] or "never"
    return (f"{head}\nThe command is queued but the phone has NOT confirmed it yet (phone last contacted the "
            f"server: {seen}). Tell the user it is queued and will run when the phone next connects; do not say it "
            f"was done. You can check later with phone_status.")
