#!/usr/bin/env python3
"""Personal AI Agent - Termux companion (no APK needed; Python standard library only).

Runs inside Termux on your Android phone. It pairs with YOUR agent server, polls it over HTTPS for approved phone
commands, runs them with the Termux:API app, and reports the real outcome back.

  python companion.py pair https://YOUR-SERVER.onrender.com ABCD-2345   pair once with a code from the Phone tab
  python companion.py run                                               keep polling (leave Termux running)
  python companion.py once                                              one poll cycle, then exit
  python companion.py status                                            local state (token is never printed)
  python companion.py unpair                                            disconnect and forget the credential

Allowlist (anything else is rejected and reported as failed): notify, schedule_reminder, cancel_reminder, open_url.

Honest limits:
  * Reminders are kept in a local file and fired by this running process. If Termux is closed or the phone's
    battery optimisation kills it, reminders do not fire. (A native Android app can use the system alarm
    service instead; see android/.)
  * "completed" means the Termux:API command exited with status 0. It does not prove you looked at the screen.
  * The credential is stored in a file readable only by your Termux user; it is not hardware-backed.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

VERSION = "termux-0.1"
ACTIONS = ("notify", "schedule_reminder", "cancel_reminder", "open_url")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_DONE = 200
LATE_REMINDER_SECONDS = 3600


class CompanionError(Exception):
    pass


class Revoked(CompanionError):
    """The server no longer accepts this device credential (revoked or expired)."""


# ------------------------------------------------------------------ local storage
class Storage:
    def __init__(self, home: Optional[Path] = None):
        base = home or Path(os.environ.get("AGENT_COMPANION_HOME") or Path.home() / ".config" / "agent-companion")
        self.dir = Path(base)
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    def _path(self, name: str) -> Path:
        return self.dir / name

    def read(self, name: str, default: Any) -> Any:
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write(self, name: str, data: Any, private: bool = False) -> None:
        path = self._path(name)
        tmp = path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ server API (HTTPS only)
def check_server_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme == "https" and parsed.hostname:
        pass
    elif parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS:
        pass  # local testing only; the credential never crosses a network in this case
    else:
        raise CompanionError("The server address must start with https:// (plain http is only allowed for localhost).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CompanionError("The server address must not contain credentials, a query or a fragment.")
    return f"{parsed.scheme}://{parsed.netloc}"


class Api:
    def __init__(self, base: str, token: str = "", timeout: float = 20.0):
        self.base, self.token, self.timeout = check_server_url(base), token, timeout

    def call(self, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body or {}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": f"AgentCompanion/{VERSION}"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token   # header only, never the URL
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ssl.create_default_context()) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode() or "{}")
            except ValueError:
                payload = {}
            if exc.code == 401 and self.token:
                # A credential was sent and refused. Stop instead of retrying: repeated stale credentials also trip
                # the server's lockout. On free hosts the database can be wiped, which makes a valid phone "unknown".
                why = payload.get("reason") or "not recognised by the server (its data may have been reset)"
                raise Revoked(f"This phone was {why}. Pair it again with a new code.") from None
            if exc.code == 401:
                raise CompanionError("The server rejected the pairing code (wrong, already used or expired).") from None
            if exc.code == 429:
                raise CompanionError("Too many requests; the server asked this phone to slow down.") from None
            raise CompanionError(f"Server error {exc.code}: {str(payload.get('error', ''))[:200]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CompanionError(f"Could not reach the server ({type(exc).__name__}).") from None
        except ValueError:
            raise CompanionError("The server sent an unreadable reply.") from None


# ------------------------------------------------------------------ executors (Termux:API)
Runner = Callable[[list], "subprocess.CompletedProcess"]


def _default_runner(argv: list) -> "subprocess.CompletedProcess":
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


class TermuxExecutor:
    """Runs phone actions through Termux:API. `runner` and `which` are injectable so tests can use a fake."""

    def __init__(self, runner: Runner = _default_runner, which: Callable[[str], Optional[str]] = shutil.which):
        self.runner, self.which = runner, which

    def _notify(self, title: str, body: str, action: str = "") -> str:
        if not self.which("termux-notification"):
            raise CompanionError("Termux:API is not installed (command termux-notification not found). Install the "
                                 "Termux:API app and run: pkg install termux-api")
        argv = ["termux-notification", "--title", title, "--content", body or title]
        if action:
            argv += ["--action", action]
        try:
            proc = self.runner(argv)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise CompanionError(f"termux-notification could not run ({type(exc).__name__}).") from None
        if proc.returncode != 0:
            raise CompanionError(f"termux-notification failed with exit code {proc.returncode}.")
        return "termux-notification exited with status 0"

    def notify(self, args: dict) -> str:
        return self._notify(str(args["title"])[:80], str(args.get("body", ""))[:500])

    def show_link(self, args: dict) -> str:
        url = validate_https_url(args.get("url"))
        self._notify("Open link?", url, "termux-open-url " + shlex.quote(url))
        return "notification with a tap-to-open link was shown; nothing opens until you tap it"


def validate_https_url(url: Any) -> str:
    if not isinstance(url, str):
        raise CompanionError("url must be a string.")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname or parsed.username
            or parsed.password or len(url) > 1000 or any(c.isspace() or c in "'\"`\\" for c in url)):
        raise CompanionError("Only plain https links with a public host name are allowed.")
    return url


# ------------------------------------------------------------------ the companion
def _parse_iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


class Companion:
    def __init__(self, store: Storage, executor: Optional[TermuxExecutor] = None, clock: Callable[[], float] = time.time):
        self.store, self.exec, self.clock = store, executor or TermuxExecutor(), clock

    # -- configuration
    def config(self) -> dict:
        cfg = self.store.read("config.json", {})
        if not cfg.get("server") or not cfg.get("token"):
            raise CompanionError("Not paired. Run: python companion.py pair https://YOUR-SERVER CODE")
        return cfg

    def pair(self, server: str, code: str, name: str = "Android (Termux)") -> dict:
        api = Api(server)
        res = api.call("/api/companion/pair", {"code": code, "name": name})
        self.store.write("config.json", {"server": api.base, "token": res["token"], "device_id": res["device_id"],
                                         "expires_at": res.get("expires_at"), "poll_seconds": res.get("poll_seconds", 30)},
                         private=True)
        return {"device_id": res["device_id"], "expires_at": res.get("expires_at")}

    def unpair(self) -> None:
        try:
            cfg = self.config()
            Api(cfg["server"], cfg["token"]).call("/api/companion/unpair")
        except CompanionError:
            pass  # server unreachable or already revoked: still forget locally
        for name in ("config.json", "done.json", "reminders.json"):
            self.store.delete(name)

    # -- actions
    def _reminders(self) -> dict:
        return self.store.read("reminders.json", {})

    def _do(self, action: str, args: dict, command_id: int) -> str:
        if action not in ACTIONS:
            raise CompanionError("Unsupported action; this companion only runs the allowlist.")
        if action == "notify":
            return self.exec.notify(args)
        if action == "open_url":
            return self.exec.show_link(args)
        if action == "schedule_reminder":
            at = args.get("at_epoch_ms")
            if isinstance(at, bool) or not isinstance(at, int) or at <= self.clock() * 1000:
                raise CompanionError("The reminder time is missing or already in the past.")
            reminders = self._reminders()
            reminders[str(command_id)] = {"title": str(args.get("title", ""))[:80], "body": str(args.get("body", ""))[:500],
                                          "at_ms": at}
            self.store.write("reminders.json", reminders)
            return ("reminder saved on this phone; it fires only while this companion keeps running in Termux "
                    "(not a system alarm)")
        reminders = self._reminders()          # cancel_reminder
        rid = str(args.get("reminder_id"))
        if rid not in reminders:
            raise CompanionError("No pending reminder with that id on this phone (it may have already fired).")
        del reminders[rid]
        self.store.write("reminders.json", reminders)
        return "reminder cancelled"

    def handle(self, cmd: dict) -> tuple[str, str]:
        """Run one command idempotently. Returns (status, detail); a redelivered id repeats the stored outcome."""
        cid = str(cmd.get("id"))
        done = self.store.read("done.json", {})
        if cid in done:
            return done[cid]["status"], done[cid]["detail"]
        try:
            outcome = ("completed", self._do(str(cmd.get("action")), cmd.get("args") or {}, int(cmd["id"])))
        except CompanionError as exc:
            outcome = ("failed", str(exc)[:400])
        except Exception as exc:  # noqa: BLE001 - never crash the loop on one bad command
            outcome = ("failed", f"Unexpected error: {type(exc).__name__}")
        done[cid] = {"status": outcome[0], "detail": outcome[1]}
        for old in list(done)[:-MAX_DONE]:
            del done[old]
        self.store.write("done.json", done)
        return outcome

    def fire_due_reminders(self) -> int:
        reminders, fired, now_ms = self._reminders(), 0, self.clock() * 1000
        for rid, r in list(reminders.items()):
            if r["at_ms"] > now_ms:
                continue
            if now_ms - r["at_ms"] > LATE_REMINDER_SECONDS * 1000:
                del reminders[rid]       # far too late to be useful (phone was off); drop rather than surprise you
                continue
            try:
                self.exec.notify({"title": r["title"], "body": r["body"]})
                del reminders[rid]
                fired += 1
            except CompanionError:
                pass                      # try again on the next tick
        self.store.write("reminders.json", reminders)
        return fired

    # -- one poll cycle
    def poll_once(self) -> dict:
        cfg = self.config()
        api = Api(cfg["server"], cfg["token"])
        state = {"pending_reminders": min(len(self._reminders()), 1000), "app_version": VERSION}
        reply = api.call("/api/companion/poll", {"state": state})
        results = []
        for cmd in reply.get("commands", []):
            status, detail = self.handle(cmd)
            try:
                api.call(f"/api/companion/commands/{int(cmd['id'])}/result", {"status": status, "detail": detail})
            except Revoked:
                raise
            except CompanionError as exc:
                detail = f"{detail} (result not delivered yet: {exc})"   # redelivery will repeat the stored outcome
            results.append((cmd.get("id"), cmd.get("action"), status))
        return {"commands": results, "poll_seconds": int(reply.get("poll_seconds") or cfg.get("poll_seconds") or 30)}

    def run(self, sleep: Callable[[float], None] = time.sleep, max_cycles: Optional[int] = None,
            log: Callable[[str], None] = print) -> int:
        failures, cycles = 0, 0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            wait = 30.0
            try:
                self.fire_due_reminders()
                out = self.poll_once()
                failures, wait = 0, float(out["poll_seconds"])
                for cid, action, status in out["commands"]:
                    log(f"command {cid} {action}: {status}")
            except Revoked as exc:
                log(str(exc))
                return 3
            except CompanionError as exc:
                failures += 1
                wait = min(2 ** failures, 120)
                log(f"{exc} Retrying in {int(wait)} s.")
            # wake once a second near the end so due reminders fire on time
            slept = 0.0
            while slept < wait:
                step = min(1.0, wait - slept)
                sleep(step)
                slept += step
                try:
                    self.fire_due_reminders()
                except CompanionError:
                    pass
        return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Personal AI Agent - Termux companion")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("server")
    p.add_argument("code")
    p.add_argument("--name", default="Android (Termux)")
    for name in ("run", "once", "status", "unpair"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    comp = Companion(Storage())
    try:
        if args.cmd == "pair":
            info = comp.pair(args.server, args.code, args.name)
            print(f"Paired as device {info['device_id']}. Credential valid until {info['expires_at']}.")
        elif args.cmd == "once":
            print(json.dumps(comp.poll_once()))
        elif args.cmd == "run":
            print("Polling the server. Keep Termux open (run termux-wake-lock). Press Ctrl+C to stop.")
            return comp.run()
        elif args.cmd == "status":
            cfg = comp.store.read("config.json", {})
            print(json.dumps({"paired": bool(cfg.get("token")), "server": cfg.get("server"),
                              "device_id": cfg.get("device_id"), "expires_at": cfg.get("expires_at"),
                              "pending_reminders": len(comp._reminders())}))
        elif args.cmd == "unpair":
            comp.unpair()
            print("Disconnected and local credential deleted.")
    except Revoked as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except CompanionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
