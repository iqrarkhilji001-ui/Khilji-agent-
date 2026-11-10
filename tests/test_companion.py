"""Companion tests. The Python companion talks to a REAL local HTTP server (the real Flask app on a loopback port),
so pairing, polling, result reporting, revocation and idempotency run over actual HTTP.

MOCKED: the Termux:API commands (termux-notification) - a fake runner records what would have been executed.
These tests do NOT prove Termux, Termux:API, a notification on a real screen, or anything on an Android device."""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "companion" / "termux"))

import companion as comp  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from agent_app.config import Config  # noqa: E402
from agent_app.server import create_app  # noqa: E402
from test_agent import FAKE_KEY, TOKEN, VERIFY_OK, FakeLLM, text, tool  # noqa: E402

H = {"Authorization": f"Bearer {TOKEN}"}


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls, self.returncode = [], returncode

    def __call__(self, argv):
        self.calls.append(list(argv))
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


class CompanionBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.from_env(env={"AGENT_DATA_DIR": self.tmp.name + "/srv", "AGENT_TOKEN": TOKEN,
                                        "ANTHROPIC_API_KEY": FAKE_KEY}, load_dotenv=False)
        self.cfg.auth_fail_delay = 0
        self.cfg.phone_wait_seconds = 0

    def serve(self, script=()):
        self.llm = FakeLLM(list(script))
        app = create_app(self.cfg, llm=self.llm)
        self.store = app.extensions["agent"]["store"]
        self.client = app.test_client()
        srv = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(self.store.close)
        self.base = f"http://127.0.0.1:{srv.server_port}"

    def companion(self, runner=None, clock=time.time, which=lambda n: "/usr/bin/" + n, sub="phone"):
        self.runner = runner or FakeRunner()
        store = comp.Storage(Path(self.tmp.name) / sub)
        return comp.Companion(store, comp.TermuxExecutor(self.runner, which), clock)

    def code(self):
        return self.client.post("/api/devices/pairing", headers=H).get_json()["code"]

    def paired(self, **kw):
        self.serve(kw.pop("script", ()))
        c = self.companion(**kw)
        c.pair(self.base, self.code())
        return c

    def queue(self, device_id, action, args, ttl=600):
        cmd, _ = self.store.create_command(device_id, None, action, args, ttl, f"k-{time.time_ns()}")
        return cmd


class PairingTests(CompanionBase):
    def test_pair_saves_private_credential_and_status_hides_it(self):
        c = self.paired()
        cfg_path = Path(self.tmp.name) / "phone" / "config.json"
        mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
        self.assertEqual(mode, 0o600)
        token = json.loads(cfg_path.read_text())["token"]
        self.assertTrue(len(token) >= 20)
        self.assertEqual(self.store.list_devices()[0]["status"], "active")
        self.assertNotIn(token, json.dumps(self.store.list_devices()))
        # the pairing code is single use
        c2 = self.companion(sub="second")
        with self.assertRaises(comp.CompanionError):
            c2.pair(self.base, "ZZZZ-2222")

    def test_plain_http_to_a_remote_host_is_refused(self):
        for bad in ("http://example.com", "ftp://x.y", "https://user:pw@example.com", "https://example.com/?a=1"):
            with self.assertRaises(comp.CompanionError, msg=bad):
                comp.check_server_url(bad)
        self.assertEqual(comp.check_server_url("https://my-agent.onrender.com/"), "https://my-agent.onrender.com")

    def test_unpair_revokes_on_server_and_deletes_local_files(self):
        c = self.paired()
        c.unpair()
        self.assertEqual(self.store.list_devices()[0]["status"], "revoked")
        self.assertFalse((Path(self.tmp.name) / "phone" / "config.json").exists())
        with self.assertRaises(comp.CompanionError):
            c.config()


class ActionTests(CompanionBase):
    def test_notify_runs_termux_and_server_records_completed(self):
        c = self.paired()
        dev = self.store.list_devices()[0]["id"]
        cmd = self.queue(dev, "notify", {"title": "Hello", "body": "From your agent"})
        out = c.poll_once()
        self.assertEqual(out["commands"], [(cmd["id"], "notify", "completed")])
        self.assertEqual(self.runner.calls[0][:5], ["termux-notification", "--title", "Hello", "--content", "From your agent"])
        row = self.store.get_command(cmd["id"])
        self.assertEqual(row["status"], "completed")
        self.assertIn("exited with status 0", row["result"])

    def test_failure_is_reported_as_failed_not_success(self):
        c = self.paired(runner=FakeRunner(returncode=1))
        dev = self.store.list_devices()[0]["id"]
        cmd = self.queue(dev, "notify", {"title": "x"})
        c.poll_once()
        row = self.store.get_command(cmd["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("exit code 1", row["result"])

    def test_missing_termux_api_is_reported_clearly(self):
        c = self.paired(which=lambda name: None)
        dev = self.store.list_devices()[0]["id"]
        cmd = self.queue(dev, "notify", {"title": "x"})
        c.poll_once()
        row = self.store.get_command(cmd["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("Termux:API is not installed", row["result"])
        self.assertEqual(self.runner.calls, [])

    def test_unsupported_action_is_rejected_on_the_phone_too(self):
        c = self.paired()
        out = c.handle({"id": 99, "action": "run_shell", "args": {"cmd": "id"}})
        self.assertEqual(out[0], "failed")
        self.assertEqual(self.runner.calls, [])

    def test_open_url_shows_a_tap_link_and_rejects_shell_tricks(self):
        c = self.paired()
        dev = self.store.list_devices()[0]["id"]
        self.queue(dev, "open_url", {"url": "https://example.com/page?a=1"})
        c.poll_once()
        argv = self.runner.calls[0]
        import shlex   # the URL is shell-quoted because Termux runs the --action string through a shell
        self.assertEqual(argv[argv.index("--action") + 1], "termux-open-url " + shlex.quote("https://example.com/page?a=1"))
        for bad in ("https://example.com/'; rm -rf ~; '", "http://example.com", "https://a b.com", "https://x.com/`id`",
                    "https://user:pw@example.com"):
            self.assertEqual(c.handle({"id": abs(hash(bad)), "action": "open_url", "args": {"url": bad}})[0], "failed", bad)
        self.assertEqual(len(self.runner.calls), 1)

    def test_redelivered_command_is_not_run_twice(self):
        c = self.paired()
        cmd = {"id": 7, "action": "notify", "args": {"title": "Once"}}
        first, second = c.handle(cmd), c.handle(cmd)
        self.assertEqual(first, second)
        self.assertEqual(len(self.runner.calls), 1)

    def test_reminder_is_saved_fires_once_when_due_and_can_be_cancelled(self):
        now = [1_000_000.0]
        c = self.paired(clock=lambda: now[0])
        due_ms = int((now[0] + 60) * 1000)
        self.assertEqual(c.handle({"id": 1, "action": "schedule_reminder",
                                   "args": {"title": "Tea", "body": "Now", "at_epoch_ms": due_ms}})[0], "completed")
        self.assertEqual(c.fire_due_reminders(), 0)
        self.assertEqual(self.runner.calls, [])
        now[0] += 61
        self.assertEqual(c.fire_due_reminders(), 1)
        self.assertEqual(c.fire_due_reminders(), 0, "a reminder fires only once")
        self.assertEqual(len(self.runner.calls), 1)
        # cancel path
        c.handle({"id": 2, "action": "schedule_reminder", "args": {"title": "B", "at_epoch_ms": int((now[0] + 60) * 1000)}})
        self.assertEqual(c.handle({"id": 3, "action": "cancel_reminder", "args": {"reminder_id": 2}})[0], "completed")
        now[0] += 120
        self.assertEqual(c.fire_due_reminders(), 0)
        status, detail = c.handle({"id": 4, "action": "cancel_reminder", "args": {"reminder_id": 2}})
        self.assertEqual(status, "failed")
        self.assertIn("No pending reminder", detail)

    def test_past_reminder_is_refused_and_very_late_ones_are_dropped(self):
        now = [2_000_000.0]
        c = self.paired(clock=lambda: now[0])
        self.assertEqual(c.handle({"id": 1, "action": "schedule_reminder",
                                   "args": {"title": "x", "at_epoch_ms": int((now[0] - 5) * 1000)}})[0], "failed")
        c.handle({"id": 2, "action": "schedule_reminder", "args": {"title": "y", "at_epoch_ms": int((now[0] + 10) * 1000)}})
        now[0] += 3 * 3600          # phone was off for hours
        self.assertEqual(c.fire_due_reminders(), 0)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(c.store.read("reminders.json", {}), {})

    def test_poll_reports_reminder_count_but_no_secrets(self):
        c = self.paired()
        c.poll_once()
        state = self.store.list_devices()[0]["state"]
        self.assertEqual(state["pending_reminders"], 0)
        self.assertEqual(state["app_version"], comp.VERSION)


class ConnectionTests(CompanionBase):
    def test_revoked_device_stops_the_loop_with_a_clear_message(self):
        c = self.paired()
        self.store.revoke_device(self.store.list_devices()[0]["id"])
        logs = []
        code = c.run(sleep=lambda s: None, max_cycles=3, log=logs.append)
        self.assertEqual(code, 3)
        self.assertIn("revoked", logs[0])

    def test_unknown_credential_after_a_server_reset_stops_and_asks_to_pair_again(self):
        c = self.paired()
        cfg = c.store.read("config.json", {})
        cfg["token"] = "dev_" + "x" * 40          # what a phone holds after the free host wiped its database
        c.store.write("config.json", cfg, private=True)
        logs = []
        self.assertEqual(c.run(sleep=lambda s: None, max_cycles=5, log=logs.append), 3)
        self.assertEqual(len(logs), 1, "must stop at once, not keep retrying with a stale credential")
        self.assertIn("Pair it again", logs[0])

    def test_unreachable_server_backs_off_and_does_not_crash(self):
        c = self.paired()
        c.config()  # paired
        cfg = c.store.read("config.json", {})
        cfg["server"] = "http://127.0.0.1:9"      # nothing listens here
        c.store.write("config.json", cfg, private=True)
        logs, sleeps = [], []
        self.assertEqual(c.run(sleep=sleeps.append, max_cycles=3, log=logs.append), 0)
        self.assertEqual(len(logs), 3)
        self.assertTrue(all("Could not reach the server" in m for m in logs))
        self.assertTrue(max(sleeps) <= 1.0)       # sleeps are 1 s ticks while waiting out the backoff

    def test_wrong_pairing_code_is_a_clean_error(self):
        self.serve()
        c = self.companion()
        with self.assertRaises(comp.CompanionError) as cm:
            c.pair(self.base, "AAAA-2222")
        self.assertIn("rejected", str(cm.exception))

    def test_not_paired_message(self):
        c = self.companion(sub="fresh")
        with self.assertRaises(comp.CompanionError) as cm:
            c.poll_once()
        self.assertIn("Not paired", str(cm.exception))


class EndToEndTests(CompanionBase):
    def test_agent_task_runs_phone_action_through_the_real_companion(self):
        """Task -> agent calls phone_action -> companion polls over HTTP -> fake Termux runs -> result reported ->
        the agent's tool result says completed (not 'queued')."""
        self.cfg.phone_wait_seconds = 10
        self.serve([tool("phone_action", {"action": "notify", "title": "Agent says hi", "body": "Test"}),
                    text("Notification shown."), VERIFY_OK])
        c = self.companion()
        c.pair(self.base, self.code())
        stop = threading.Event()

        def phone_loop():
            while not stop.is_set():
                try:
                    c.poll_once()
                except comp.CompanionError:
                    pass
                time.sleep(0.15)
        t = threading.Thread(target=phone_loop, daemon=True)
        t.start()
        self.addCleanup(t.join, 2)
        self.addCleanup(stop.set)
        tid = self.client.post("/api/tasks", json={"request": "notify me"}, headers=H).get_json()["id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            task = self.client.get(f"/api/tasks/{tid}", headers=H).get_json()["task"]
            if task["status"] in ("completed_verified", "completed_unverified", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(task["status"], "completed_verified", task)
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("status: completed", sent)
        self.assertIn("phone_reported", sent)
        self.assertEqual(self.runner.calls[0][:3], ["termux-notification", "--title", "Agent says hi"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
