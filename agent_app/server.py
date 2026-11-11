"""Flask web server: authenticated JSON API + mobile web UI."""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import preflight
from .agent import AgentRunner
from .config import Config
from .db import MEMORY_KINDS, Store, now
from .llm import GeminiClient
from .ratelimit import RateLimiter
from .redact import RedactingFilter, looks_like_secret
from .tools import NOT_IMPLEMENTED, ToolRegistry, build_registry

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_REQUEST_CHARS = 8000
STATIC_FILES = {"app.js": "application/javascript", "voice.js": "application/javascript"}
FAIL_LIMIT, FAIL_WINDOW = 10, 300          # failed logins per client address before a temporary block
POLL_SECONDS = 30
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def create_app(cfg: Config, llm: Any = None, registry: Optional[ToolRegistry] = None,
               inline: bool = False, http_get: Any = None, resolver: Any = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    if cfg.trust_proxy:  # behind Render's proxy: use the real client address for rate limiting
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]
    limiter = RateLimiter()
    seen_requests: dict[str, tuple[float, int]] = {}
    seen_lock = threading.Lock()
    logging.getLogger().addFilter(RedactingFilter(cfg.secrets_list()))

    store = Store(cfg.db_path)
    store.mark_interrupted()
    store.trial_status(cfg.trial_days)  # starts once; persisted in SQLite
    registry = registry or build_registry(cfg.enable_run_python)
    llm = llm or GeminiClient(cfg)
    runner = AgentRunner(cfg, store, llm, registry, inline=inline, http_get=http_get, resolver=resolver)
    app.extensions["agent"] = {"store": store, "runner": runner, "cfg": cfg, "limiter": limiter}

    # ------------------------------------------------------------ security
    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return resp

    def _bearer() -> str:
        header = request.headers.get("Authorization", "")
        return header[7:].strip() if header.lower().startswith("bearer ") else ""

    def _client_ip() -> str:
        return request.remote_addr or "unknown"

    def _too_many(retry: int):
        resp = jsonify(error="Too many requests. Try again later.", retry_after=retry)
        resp.status_code = 429
        resp.headers["Retry-After"] = str(retry)
        return resp

    def _limit(bucket: str, limit: int, window: float, key: Optional[str] = None):
        k = key or _client_ip()
        if limiter.allow(bucket, k, limit, window):
            return None
        return _too_many(limiter.retry_after(bucket, k, window))

    def _fail_auth():
        limiter.record("authfail", _client_ip(), FAIL_WINDOW)
        time.sleep(cfg.auth_fail_delay)  # slows token guessing

    @app.before_request
    def _auth() -> Optional[Response]:
        path = request.path
        if not path.startswith("/api/") or path == "/api/health":
            return None
        ip = _client_ip()
        if limiter.count("authfail", ip, FAIL_WINDOW) >= FAIL_LIMIT:
            return _too_many(limiter.retry_after("authfail", ip, FAIL_WINDOW))
        if path == "/api/companion/pair":
            return None  # uses a one-time pairing code instead of a bearer token; limited in the handler
        supplied = _bearer()
        if path.startswith("/api/companion/"):
            status, device = store.device_by_token(supplied)
            if status == "ok":
                g.device = device
                return None
            if status == "unknown":
                _fail_auth()
                return jsonify(error="Unauthorized"), 401
            return jsonify(error="Unauthorized", reason=status), 401  # revoked or expired: re-pair
        if supplied and hmac.compare_digest(supplied.encode(), cfg.token.encode()):
            return None
        _fail_auth()
        return jsonify(error="Unauthorized"), 401

    def _err(msg: str, code: int = 400):
        return jsonify(error=msg), code

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index() -> Response:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return Response(html, mimetype="text/html")

    @app.get("/static/<name>")
    def static_file(name: str):
        mime = STATIC_FILES.get(name)
        if mime is None:
            return _err("Not found.", 404)
        return Response((STATIC_DIR / name).read_text(encoding="utf-8"), mimetype=mime)

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.post("/api/selfcheck")
    def selfcheck():
        what = (request.get_json(silent=True) or {}).get("what")
        limited = _limit("selfcheck", 6, 60)
        if limited:
            return limited
        if what == "api":
            return jsonify(preflight.check_api(cfg, llm))
        if what == "search":
            return jsonify(preflight.check_search(cfg))
        return _err("what must be 'api' or 'search'.")

    @app.get("/api/status")
    def status():
        used_seconds = store.daily_ai_seconds_used()
        trial = store.trial_status(cfg.trial_days)
        return jsonify(
            warnings=preflight.config_warnings(cfg),
            model=cfg.model,
            api_key_configured=bool(cfg.api_key),
            tools=registry.names(),
            not_implemented=NOT_IMPLEMENTED,
            max_steps=cfg.max_steps,
            daily_ai_limit_seconds=cfg.daily_ai_limit_seconds,
            daily_ai_used_seconds=round(used_seconds, 1),
            daily_ai_remaining_seconds=round(max(0, cfg.daily_ai_limit_seconds - used_seconds), 1),
            daily_ai_resets_at="00:00 Asia/Karachi",
            trial=trial,
        )

    # ------------------------------------------------------------ tasks
    @app.post("/api/tasks")
    def create_task():
        body = request.get_json(silent=True) or {}
        text = body.get("request")
        if not isinstance(text, str) or not text.strip():
            return _err("Field 'request' is required.")
        text = text.strip()
        if len(text) > MAX_REQUEST_CHARS:
            return _err(f"Request is too long (max {MAX_REQUEST_CHARS} characters).")
        if not cfg.api_key:
            return _err("GEMINI_API_KEY is not configured on the server. See the README.", 503)
        trial = store.trial_status(cfg.trial_days)
        if not trial["active"]:
            return _err("The 30-day AI trial has ended. AI tasks are paused.", 403)
        limited = _limit("task", 30, 60)
        if limited:
            return limited
        rid = body.get("client_request_id")
        rid = rid if isinstance(rid, str) and 0 < len(rid) <= 64 else None
        with seen_lock:  # a retried or double-fired request with the same id returns the same task
            for k in [k for k, (ts, _) in seen_requests.items() if time.monotonic() - ts > 600]:
                seen_requests.pop(k, None)
            if rid and rid in seen_requests:
                return jsonify(id=seen_requests[rid][1], duplicate=True), 200
            task_id = runner.submit(text)
            if rid:
                seen_requests[rid] = (time.monotonic(), task_id)
        return jsonify(id=task_id), 202

    @app.get("/api/tasks")
    def list_tasks():
        return jsonify(tasks=store.list_tasks(30))

    @app.get("/api/tasks/<int:task_id>")
    def get_task(task_id: int):
        task = store.get_task(task_id)
        if task is None:
            return _err("Task not found.", 404)
        return jsonify(task=task, steps=store.get_steps(task_id),
                       approvals=store.pending_approvals(task_id))

    @app.post("/api/tasks/<int:task_id>/cancel")
    def cancel_task(task_id: int):
        if not runner.cancel(task_id):
            return _err("Task is not active.", 409)
        return jsonify(ok=True)

    @app.delete("/api/tasks/<int:task_id>")
    def delete_task(task_id: int):
        task = store.get_task(task_id)
        if task is None:
            return _err("Task not found.", 404)
        if task["status"] in ("pending", "running", "waiting_approval"):
            return _err("Cancel the task before deleting it.", 409)
        store.delete_task(task_id)
        return jsonify(ok=True)

    @app.delete("/api/tasks")
    def clear_tasks():
        return jsonify(deleted=store.clear_history())

    # ------------------------------------------------------------ approvals
    @app.get("/api/approvals")
    def approvals():
        return jsonify(approvals=store.pending_approvals())

    @app.post("/api/approvals/<int:approval_id>")
    def decide(approval_id: int):
        body = request.get_json(silent=True) or {}
        decision = body.get("decision")
        if decision not in ("approve", "deny"):
            return _err("decision must be 'approve' or 'deny'.")
        appr = store.get_approval(approval_id)
        if appr is None:
            return _err("Approval not found.", 404)
        if not store.decide_approval(approval_id, "approved" if decision == "approve" else "denied"):
            return _err("Already decided.", 409)
        store.add_step(appr["task_id"], "approval",
                       f"You {'approved' if decision == 'approve' else 'denied'}: {appr['description']}")
        if not store.pending_approvals(appr["task_id"]):
            runner.resume(appr["task_id"])
        return jsonify(ok=True)

    # ------------------------------------------------------------ memory
    @app.get("/api/memory")
    def memory_list():
        return jsonify(memory=store.list_memory(), kinds=list(MEMORY_KINDS))

    @app.post("/api/memory")
    def memory_add():
        body = request.get_json(silent=True) or {}
        kind, text = body.get("kind", "note"), body.get("text")
        if kind not in MEMORY_KINDS:
            return _err("Invalid kind.")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return _err("text is required (max 500 characters).")
        if looks_like_secret(text):
            return _err("Refusing to store text that looks like a secret.")
        return jsonify(id=store.add_memory(kind, text.strip())), 201

    @app.delete("/api/memory/<int:memory_id>")
    def memory_delete(memory_id: int):
        if not store.delete_memory(memory_id):
            return _err("Not found.", 404)
        return jsonify(ok=True)

    @app.delete("/api/memory")
    def memory_clear():
        return jsonify(deleted=store.clear_memory())

    # ------------------------------------------------------------ devices (user side)
    @app.post("/api/devices/pairing")
    def devices_pairing():
        limited = _limit("pairing", 5, 600)
        if limited:
            return limited
        code, expires = store.create_pairing_code()
        return jsonify(code=code, expires_at=expires, valid_minutes=10), 201

    @app.get("/api/devices")
    def devices_list():
        return jsonify(devices=store.list_devices(), commands=store.list_commands(15))

    @app.post("/api/devices/<int:device_id>/revoke")
    def devices_revoke(device_id: int):
        if store.get_device(device_id) is None:
            return _err("Device not found.", 404)
        store.revoke_device(device_id)
        return jsonify(ok=True)

    @app.get("/api/commands")
    def commands_list():
        return jsonify(commands=store.list_commands(30))

    @app.post("/api/commands/<int:command_id>/cancel")
    def commands_cancel(command_id: int):
        ok, row = store.cancel_command(command_id)
        if row is None:
            return _err("Command not found.", 404)
        if not ok:
            return _err(f"Command is {row['status']}; only commands the phone has not received can be cancelled.", 409)
        return jsonify(ok=True)

    # ------------------------------------------------------------ companion (phone side)
    def _clean_state(raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        out: dict = {}
        if isinstance(raw.get("notifications_enabled"), bool):
            out["notifications_enabled"] = raw["notifications_enabled"]
        for key, hi in (("pending_reminders", 1000), ("android_sdk", 100)):
            val = raw.get(key)
            if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= hi:
                out[key] = val
        if isinstance(raw.get("app_version"), str):
            out["app_version"] = _CONTROL.sub("", raw["app_version"])[:20]
        return out

    @app.post("/api/companion/pair")
    def companion_pair():
        limited = _limit("pair", 5, 60)
        if limited:
            return limited
        body = request.get_json(silent=True) or {}
        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            return _err("Field 'code' is required.")
        name = _CONTROL.sub("", str(body.get("name") or "Android phone")).strip()[:60] or "Android phone"
        result = store.consume_pairing_code(code, name, cfg.device_token_days)
        if result is None:
            _fail_auth()
            return jsonify(error="Invalid, used or expired pairing code."), 401
        device_id, token, expires = result
        return jsonify(device_id=device_id, token=token, expires_at=expires, poll_seconds=POLL_SECONDS), 201

    @app.post("/api/companion/poll")
    def companion_poll():
        device = g.device
        limited = _limit("poll", 30, 60, key=f"device-{device['id']}")
        if limited:
            return limited
        body = request.get_json(silent=True) or {}
        store.touch_device(device["id"], _clean_state(body.get("state")))
        cmds = store.claim_commands(device["id"], 10)
        return jsonify(commands=[{"id": c["id"], "action": c["action"], "args": c["args"],
                                  "expires_at": c["expires_at"]} for c in cmds],
                       server_time=now(), poll_seconds=POLL_SECONDS)

    @app.post("/api/companion/commands/<int:command_id>/result")
    def companion_result(command_id: int):
        body = request.get_json(silent=True) or {}
        status, detail = body.get("status"), body.get("detail", "")
        if status not in ("completed", "failed"):
            return _err("status must be 'completed' or 'failed'.")
        if not isinstance(detail, str):
            return _err("detail must be a string.")
        outcome, row = store.report_result(g.device["id"], command_id, status, _CONTROL.sub(" ", detail).strip())
        if outcome == "not_found":
            return _err("Command not found.", 404)
        if outcome == "conflict":
            return jsonify(error="Command is not awaiting a result.", status=row["status"]), 409
        return jsonify(ok=True, status=row["status"], duplicate=outcome == "duplicate")

    @app.post("/api/companion/unpair")
    def companion_unpair():
        store.revoke_device(g.device["id"])
        return jsonify(ok=True)

    return app
