"""Device pairing, command queue, phone-action allowlist, approvals, rate limiting, idempotency.
All tests use the Flask test client and a scripted fake model. They prove the server logic; they do NOT prove
anything about a real Android phone."""
import json
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_app import phone  # noqa: E402
from agent_app.db import hash_secret, iso_in  # noqa: E402
from agent_app.redact import redact  # noqa: E402
from agent_app.tools import ToolError  # noqa: E402
from test_agent import FAKE_KEY, TOKEN, VERIFY_OK, Base, text, tool  # noqa: E402


class DeviceBase(Base):
    def setUp(self):
        super().setUp()
        self.cfg.phone_wait_seconds = 0

    def pair(self, c, name="Pixel"):
        code = c.post("/api/devices/pairing", headers=self.H).get_json()["code"]
        r = c.post("/api/companion/pair", json={"code": code, "name": name})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        return body["device_id"], {"Authorization": f"Bearer {body['token']}"}, body["token"], code

    def poll(self, c, dh, state=None):
        r = c.post("/api/companion/poll", json={"state": state or {}}, headers=dh)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["commands"]

    def result(self, c, dh, cid, status="completed", detail="ok"):
        return c.post(f"/api/companion/commands/{cid}/result", json={"status": status, "detail": detail}, headers=dh)


class PairingTests(DeviceBase):
    def test_pairing_requires_user_auth_and_code_is_one_time(self):
        c = self.app([])
        self.assertEqual(c.post("/api/devices/pairing").status_code, 401)
        dev_id, dh, token, code = self.pair(c)
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        again = c.post("/api/companion/pair", json={"code": code, "name": "Other"})
        self.assertEqual(again.status_code, 401)
        self.assertTrue(token.startswith("dvc_"))

    def test_only_hashes_are_stored(self):
        c = self.app([])
        _, _, token, code = self.pair(c)
        db = self.ext["store"]._db
        dump = json.dumps([tuple(r) for r in db.execute("SELECT * FROM devices").fetchall()] +
                          [tuple(r) for r in db.execute("SELECT * FROM pairing_codes").fetchall()])
        self.assertNotIn(token, dump)
        self.assertNotIn(code.replace("-", ""), dump)
        self.assertIn(hash_secret(token), dump)

    def test_new_code_invalidates_the_previous_one(self):
        c = self.app([])
        first = c.post("/api/devices/pairing", headers=self.H).get_json()["code"]
        c.post("/api/devices/pairing", headers=self.H)
        self.assertEqual(c.post("/api/companion/pair", json={"code": first}).status_code, 401)

    def test_expired_code_rejected(self):
        c = self.app([])
        code = c.post("/api/devices/pairing", headers=self.H).get_json()["code"]
        db = self.ext["store"]._db
        db.execute("UPDATE pairing_codes SET expires_at = ?", (iso_in(-5),))
        db.commit()
        self.assertEqual(c.post("/api/companion/pair", json={"code": code}).status_code, 401)

    def test_code_format_is_forgiving_but_strict_on_length(self):
        c = self.app([])
        code = c.post("/api/devices/pairing", headers=self.H).get_json()["code"]
        self.assertEqual(c.post("/api/companion/pair", json={"code": code[:-1]}).status_code, 401)
        r = c.post("/api/companion/pair", json={"code": code.lower().replace("-", " "), "name": "x\x00y"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.ext["store"].list_devices()[0]["name"], "xy")


class DeviceAuthTests(DeviceBase):
    def test_token_types_are_not_interchangeable(self):
        c = self.app([])
        _, dh, _, _ = self.pair(c)
        self.assertEqual(c.get("/api/tasks", headers=dh).status_code, 401)       # device token on user API
        self.assertEqual(c.post("/api/companion/poll", json={}, headers=self.H).status_code, 401)  # user token on device API
        self.assertEqual(c.post("/api/companion/poll", json={}).status_code, 401)
        self.assertEqual(c.post("/api/companion/poll", json={}, headers={"Authorization": "Bearer dvc_nope"}).status_code, 401)

    def test_revoked_token_rejected_with_reason(self):
        c = self.app([])
        dev_id, dh, _, _ = self.pair(c)
        self.assertEqual(c.post(f"/api/devices/{dev_id}/revoke", headers=self.H).status_code, 200)
        r = c.post("/api/companion/poll", json={}, headers=dh)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (401, "revoked"))

    def test_expired_token_rejected_with_reason(self):
        c = self.app([])
        _, dh, _, _ = self.pair(c)
        db = self.ext["store"]._db
        db.execute("UPDATE devices SET expires_at = ?", (iso_in(-5),))
        db.commit()
        r = c.post("/api/companion/poll", json={}, headers=dh)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (401, "expired"))

    def test_device_can_unpair_itself(self):
        c = self.app([])
        dev_id, dh, _, _ = self.pair(c)
        self.assertEqual(c.post("/api/companion/unpair", headers=dh).status_code, 200)
        self.assertEqual(c.post("/api/companion/poll", json={}, headers=dh).status_code, 401)
        self.assertEqual(self.ext["store"].get_device(dev_id)["status"], "revoked")

    def test_state_is_sanitised_and_listed_without_token_hash(self):
        c = self.app([])
        _, dh, _, _ = self.pair(c)
        self.poll(c, dh, {"notifications_enabled": True, "pending_reminders": 2, "evil": "x", "app_version": "1.0\n"})
        dev = c.get("/api/devices", headers=self.H).get_json()["devices"][0]
        self.assertEqual(dev["state"], {"notifications_enabled": True, "pending_reminders": 2, "app_version": "1.0"})
        self.assertNotIn("token_hash", dev)
        self.assertTrue(dev["last_seen"])


class RateLimitTests(DeviceBase):
    def test_failed_logins_lock_the_client_then_recover(self):
        c = self.app([])
        clock = [1000.0]
        self.ext["limiter"]._clock = lambda: clock[0]
        for _ in range(10):
            self.assertEqual(c.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        r = c.get("/api/tasks", headers=self.H)            # even the right token is blocked during the lockout
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        self.assertEqual(c.get("/api/health").status_code, 200)   # health stays reachable
        clock[0] += 301
        self.assertEqual(c.get("/api/tasks", headers=self.H).status_code, 200)

    def test_wrong_pairing_codes_count_as_failures(self):
        c = self.app([])
        codes = [c.post("/api/companion/pair", json={"code": "ABCD-EFGH"}).status_code for _ in range(7)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5:], [429, 429])

    def test_pairing_code_creation_and_selfcheck_are_limited(self):
        c = self.app([])
        statuses = [c.post("/api/devices/pairing", headers=self.H).status_code for _ in range(6)]
        self.assertEqual(statuses, [201] * 5 + [429])
        s = [c.post("/api/selfcheck", json={"what": "x"}, headers=self.H).status_code for _ in range(7)]
        self.assertEqual(s[-1], 429)

    def test_revoked_device_polling_does_not_lock_the_ip(self):
        c = self.app([])
        dev_id, dh, _, _ = self.pair(c)
        c.post(f"/api/devices/{dev_id}/revoke", headers=self.H)
        for _ in range(15):
            c.post("/api/companion/poll", json={}, headers=dh)
        self.assertEqual(c.get("/api/tasks", headers=self.H).status_code, 200)

    def test_task_creation_is_limited(self):
        c = self.app([text("ok")] * 31)
        codes = [c.post("/api/tasks", json={"request": f"hi {i}"}, headers=self.H).status_code for i in range(31)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(codes.count(202), 30)


class ValidationTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def build(self, action, **kw):
        return phone.build_command(action, kw, self.NOW)

    def test_allowlist(self):
        for bad in ("shell", "send_sms", "call", "", None, "NOTIFY"):
            with self.assertRaises(ToolError):
                self.build(bad)
        self.assertEqual(set(phone.ACTIONS), {"notify", "schedule_reminder", "cancel_reminder", "open_url"})

    def test_notify_fields(self):
        args, _ = self.build("notify", title=" Hi\x00 ", body="b")
        self.assertEqual(args, {"title": "Hi", "body": "b"})
        for kw in ({}, {"title": ""}, {"title": "x" * 81}, {"title": "t", "body": "b" * 501}, {"title": 5}):
            with self.assertRaises(ToolError, msg=kw):
                self.build("notify", **kw)

    def test_reminder_time_rules(self):
        args, ttl = self.build("schedule_reminder", title="t", in_seconds=3600)
        self.assertEqual(args["at_epoch_ms"], int((self.NOW + 3600) * 1000))
        self.assertAlmostEqual(ttl, 3600, delta=1)
        args, _ = self.build("schedule_reminder", title="t", at="2027-02-01T09:00:00+05:00")
        expected = datetime(2027, 2, 1, 4, 0, 0, tzinfo=timezone.utc)   # 09:00 in UTC+05:00 is 04:00 UTC
        self.assertEqual(args["at_epoch_ms"], int(expected.timestamp() * 1000))
        for kw in ({"in_seconds": 1}, {"in_seconds": 400 * 86400}, {"in_seconds": "5"}, {"in_seconds": True},
                   {"at": "2027-02-01T09:00:00"}, {"at": "soon"}, {"at": "2001-01-01T00:00:00+00:00"},
                   {"at": "2027-02-01T09:00:00+05:00", "in_seconds": 60}, {}):
            with self.assertRaises(ToolError, msg=kw):
                self.build("schedule_reminder", title="t", **kw)

    def test_open_url_rules(self):
        ok, _ = self.build("open_url", url="https://example.com/a?b=1")
        self.assertEqual(ok["url"], "https://example.com/a?b=1")
        for url in ("http://example.com", "javascript:alert(1)", "intent://x#Intent;end", "file:///etc/passwd",
                    "https://user:pw@example.com", "https://127.0.0.1/", "https://[::1]/", "https://localhost/",
                    "https://example.com:8443/", "https://exa mple.com", "https://example.com/" + "a" * 1000, ""):
            with self.assertRaises(ToolError, msg=url):
                self.build("open_url", url=url)

    def test_cancel_reminder_needs_integer_id(self):
        self.assertEqual(self.build("cancel_reminder", reminder_id=3)[0], {"reminder_id": 3})
        for rid in (0, -1, "3", None, True):
            with self.assertRaises(ToolError):
                self.build("cancel_reminder", reminder_id=rid)


class CommandFlowTests(DeviceBase):
    def run_agent(self, c, script, req="do it"):
        r = c.post("/api/tasks", json={"request": req}, headers=self.H)
        self.assertEqual(r.status_code, 202)
        return r.get_json()["id"]

    def test_no_device_is_reported_as_an_error(self):
        c = self.app([tool("phone_action", {"action": "notify", "title": "Hi"}), text("No phone paired."), VERIFY_OK])
        tid = self.run_agent(c, None)
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("No paired Android device", sent)
        self.assertIn('"is_error": true', sent)

    def test_queued_command_is_not_reported_as_done(self):
        c = self.app([tool("phone_action", {"action": "notify", "title": "Hi", "body": "there"}),
                      text("It is queued."), VERIFY_OK])
        dev_id, dh, _, _ = self.pair(c)
        self.run_agent(c, None)
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("NOT confirmed", sent)
        self.assertIn("status: approved", sent)
        cmds = self.poll(c, dh)
        self.assertEqual([(x["action"], x["args"]) for x in cmds], [("notify", {"title": "Hi", "body": "there"})])
        self.assertEqual(self.ext["store"].get_command(cmds[0]["id"])["status"], "running")

    def test_phone_confirmation_makes_the_tool_result_completed(self):
        self.cfg.phone_wait_seconds = 5
        c = self.app([tool("phone_action", {"action": "notify", "title": "Hi"}), text("Shown."), VERIFY_OK])
        _, dh, _, _ = self.pair(c)
        phone_client = self.flask_app.test_client()

        def phone_side():
            for _ in range(40):
                cmds = phone_client.post("/api/companion/poll", json={}, headers=dh).get_json()["commands"]
                if cmds:
                    phone_client.post(f"/api/companion/commands/{cmds[0]['id']}/result",
                                      json={"status": "completed", "detail": "notification shown"}, headers=dh)
                    return
                time.sleep(0.1)
        t = threading.Thread(target=phone_side)
        t.start()
        self.run_agent(c, None)
        t.join()
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("status: completed", sent)
        self.assertIn("notification shown", sent)

    def test_phone_failure_is_an_error_result(self):
        self.cfg.phone_wait_seconds = 5
        c = self.app([tool("phone_action", {"action": "notify", "title": "Hi"}), text("It failed."), VERIFY_OK])
        _, dh, _, _ = self.pair(c)
        pc = self.flask_app.test_client()

        def phone_side():
            for _ in range(40):
                cmds = pc.post("/api/companion/poll", json={}, headers=dh).get_json()["commands"]
                if cmds:
                    pc.post(f"/api/companion/commands/{cmds[0]['id']}/result",
                            json={"status": "failed", "detail": "notification permission denied"}, headers=dh)
                    return
                time.sleep(0.1)
        t = threading.Thread(target=phone_side)
        t.start()
        self.run_agent(c, None)
        t.join()
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("did NOT succeed", sent)
        self.assertIn("notification permission denied", sent)
        self.assertIn('"is_error": true', sent)

    def test_reminder_then_cancel(self):
        c = self.app([tool("phone_action", {"action": "schedule_reminder", "title": "Tea", "in_seconds": 600}, uid="a"),
                      tool("phone_action", {"action": "cancel_reminder", "reminder_id": 1}, uid="b"),
                      text("done"), VERIFY_OK])
        _, dh, _, _ = self.pair(c)
        self.run_agent(c, None)
        cmds = self.poll(c, dh)
        self.assertEqual([x["action"] for x in cmds], ["schedule_reminder", "cancel_reminder"])
        self.assertEqual(cmds[1]["args"], {"reminder_id": 1})
        self.assertGreater(cmds[0]["args"]["at_epoch_ms"], time.time() * 1000)

    def test_cancel_reminder_rejects_foreign_or_wrong_command(self):
        c = self.app([tool("phone_action", {"action": "notify", "title": "x"}, uid="a"),
                      tool("phone_action", {"action": "cancel_reminder", "reminder_id": 1}, uid="b"),
                      text("done"), VERIFY_OK])
        self.pair(c)
        self.run_agent(c, None)
        self.assertIn("does not match a reminder", json.dumps(self.llm.calls[2][1]))

    def test_open_url_requires_approval_and_shows_exact_url(self):
        url = "https://example.com/page"
        c = self.app([tool("phone_action", {"action": "open_url", "url": url}), text("Link sent."), VERIFY_OK])
        dev_id, dh, _, _ = self.pair(c)
        tid = self.run_agent(c, None)
        d = c.get(f"/api/tasks/{tid}", headers=self.H).get_json()
        self.assertEqual(d["task"]["status"], "waiting_approval")
        self.assertIn(url, d["approvals"][0]["description"])
        self.assertEqual(self.poll(c, dh), [])                     # nothing queued before approval
        c.post(f"/api/approvals/{d['approvals'][0]['id']}", json={"decision": "approve"}, headers=self.H)
        cmds = self.poll(c, dh)
        self.assertEqual([(x["action"], x["args"]) for x in cmds], [("open_url", {"url": url})])

    def test_denied_open_url_never_reaches_the_phone(self):
        c = self.app([tool("phone_action", {"action": "open_url", "url": "https://example.com/"}),
                      text("Not sent."), VERIFY_OK])
        _, dh, _, _ = self.pair(c)
        tid = self.run_agent(c, None)
        appr = c.get(f"/api/tasks/{tid}", headers=self.H).get_json()["approvals"][0]
        c.post(f"/api/approvals/{appr['id']}", json={"decision": "deny"}, headers=self.H)
        self.assertEqual(self.poll(c, dh), [])
        self.assertEqual(self.ext["store"].list_commands(), [])

    def test_safe_actions_do_not_need_approval(self):
        c = self.app([tool("phone_action", {"action": "notify", "title": "x"}), text("ok"), VERIFY_OK])
        self.pair(c)
        tid = self.run_agent(c, None)
        self.assertNotEqual(c.get(f"/api/tasks/{tid}", headers=self.H).get_json()["task"]["status"], "waiting_approval")

    def test_phone_status_tool(self):
        c = self.app([tool("phone_status", {}), text("ok"), VERIFY_OK])
        _, dh, _, _ = self.pair(c, "My Pixel")
        self.poll(c, dh, {"notifications_enabled": False})
        self.run_agent(c, None)
        sent = json.dumps(self.llm.calls[1][1])
        self.assertIn("My Pixel", sent)
        self.assertIn("notifications allowed: False", sent)

    def test_several_devices_require_device_id(self):
        c = self.app([tool("phone_action", {"action": "notify", "title": "x"}), text("ok"), VERIFY_OK])
        self.pair(c, "A")
        self.pair(c, "B")
        self.run_agent(c, None)
        self.assertIn("pass device_id", json.dumps(self.llm.calls[1][1]))


class IdempotencyAndLifecycleTests(DeviceBase):
    def test_same_tool_call_is_queued_once(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        first, created1 = store.create_command(dev_id, 1, "notify", {"title": "x", "body": ""}, 600, "key-1")
        second, created2 = store.create_command(dev_id, 1, "notify", {"title": "x", "body": ""}, 600, "key-1")
        self.assertEqual((created1, created2, first["id"] == second["id"]), (True, False, True))
        self.assertEqual(len(store.list_commands()), 1)

    def test_replayed_agent_call_reports_duplicate(self):
        from agent_app.tools import ToolContext
        c = self.app([])
        self.pair(c)
        store = self.ext["store"]
        ctx = ToolContext(self.cfg, store, 7, tool_use_id="tu_9")
        out1 = phone.phone_action({"action": "notify", "title": "x"}, ctx)
        out2 = phone.phone_action({"action": "notify", "title": "x"}, ctx)
        self.assertNotIn("no duplicate was created", out1)
        self.assertIn("no duplicate was created", out2)
        self.assertEqual(len(store.list_commands()), 1)

    def test_delivery_is_not_repeated_until_the_redelivery_window(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        store.create_command(dev_id, None, "notify", {"title": "x", "body": ""}, 600, "k")
        self.assertEqual(len(self.poll(c, dh)), 1)
        self.assertEqual(self.poll(c, dh), [])                       # immediate retry: nothing new
        store._db.execute("UPDATE commands SET delivered_at = ?", (iso_in(-120),))
        store._db.commit()
        again = self.poll(c, dh)                                      # phone never answered: re-delivered
        self.assertEqual(len(again), 1)
        self.assertEqual(store.get_command(again[0]["id"])["attempts"], 2)

    def test_duplicate_and_conflicting_results(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        cmd, _ = store.create_command(dev_id, None, "notify", {"title": "x", "body": ""}, 600, "k")
        self.assertEqual(self.result(c, dh, cmd["id"]).status_code, 409)       # not delivered yet
        self.poll(c, dh)
        r1 = self.result(c, dh, cmd["id"])
        r2 = self.result(c, dh, cmd["id"])                                      # network retry
        self.assertEqual((r1.status_code, r1.get_json()["duplicate"]), (200, False))
        self.assertEqual((r2.status_code, r2.get_json()["duplicate"]), (200, True))
        self.assertEqual(self.result(c, dh, cmd["id"], "failed", "x").status_code, 409)   # contradicts final state
        self.assertEqual(self.result(c, dh, 999).status_code, 404)

    def test_device_cannot_touch_another_devices_commands(self):
        c = self.app([])
        store = self.ext["store"]
        d1, h1, _, _ = self.pair(c, "one")
        d2, h2, _, _ = self.pair(c, "two")
        cmd, _ = store.create_command(d1, None, "notify", {"title": "x", "body": ""}, 600, "k")
        self.assertEqual(self.poll(c, h2), [])
        self.poll(c, h1)
        self.assertEqual(self.result(c, h2, cmd["id"]).status_code, 404)

    def test_bad_result_payloads(self):
        c = self.app([])
        _, dh, _, _ = self.pair(c)
        for body in ({"status": "ok"}, {"status": "completed", "detail": 5}, {}):
            r = c.post("/api/companion/commands/1/result", json=body, headers=dh)
            self.assertEqual(r.status_code, 400, body)

    def test_expired_commands_are_not_delivered(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        cmd, _ = store.create_command(dev_id, None, "notify", {"title": "x", "body": ""}, -1, "k")
        self.assertEqual(self.poll(c, dh), [])
        self.assertEqual(store.get_command(cmd["id"])["status"], "expired")

    def test_expired_command_is_an_error_for_the_agent(self):
        from agent_app.tools import ToolContext
        c = self.app([])
        dev_id, _, _, _ = self.pair(c)
        store = self.ext["store"]
        self.cfg.phone_wait_seconds = 0
        store._db.execute("UPDATE devices SET state = state")
        orig = store.create_command
        store.create_command = lambda *a, **k: orig(a[0], a[1], a[2], a[3], -1, a[5])
        with self.assertRaises(ToolError) as cm:
            phone.phone_action({"action": "notify", "title": "x"}, ToolContext(self.cfg, store, 1, tool_use_id="t"))
        self.assertIn("did NOT succeed", str(cm.exception))

    def test_running_command_with_no_result_eventually_fails(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        cmd, _ = store.create_command(dev_id, None, "notify", {"title": "x", "body": ""}, 600, "k")
        self.poll(c, dh)
        store._db.execute("UPDATE commands SET expires_at = ?", (iso_in(-1),))
        store._db.commit()
        row = store.get_command(cmd["id"])
        self.assertEqual((row["status"], row["result"]), ("failed", "The phone never reported a result."))

    def test_user_cancel_only_before_delivery(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        a, _ = store.create_command(dev_id, None, "notify", {"title": "a", "body": ""}, 600, "a")
        self.assertEqual(c.post(f"/api/commands/{a['id']}/cancel", headers=self.H).status_code, 200)
        self.assertEqual(self.poll(c, dh), [])
        b, _ = store.create_command(dev_id, None, "notify", {"title": "b", "body": ""}, 600, "b")
        self.poll(c, dh)
        self.assertEqual(c.post(f"/api/commands/{b['id']}/cancel", headers=self.H).status_code, 409)
        self.assertEqual(c.post("/api/commands/999/cancel", headers=self.H).status_code, 404)

    def test_revoking_a_device_cancels_its_open_commands(self):
        c = self.app([])
        store = self.ext["store"]
        dev_id, dh, _, _ = self.pair(c)
        cmd, _ = store.create_command(dev_id, None, "notify", {"title": "a", "body": ""}, 600, "a")
        c.post(f"/api/devices/{dev_id}/revoke", headers=self.H)
        self.assertEqual(store.get_command(cmd["id"])["status"], "cancelled")
        self.assertEqual(c.post("/api/devices/999/revoke", headers=self.H).status_code, 404)


class SecretAndRequestTests(DeviceBase):
    def test_device_tokens_are_redacted(self):
        c = self.app([])
        _, _, token, _ = self.pair(c)
        self.assertNotIn(token, redact(f"leaked {token} here"))

    def test_client_request_id_makes_submission_idempotent(self):
        c = self.app([text("hello")])
        r1 = c.post("/api/tasks", json={"request": "hi", "client_request_id": "abc-123"}, headers=self.H)
        r2 = c.post("/api/tasks", json={"request": "hi", "client_request_id": "abc-123"}, headers=self.H)
        self.assertEqual((r1.status_code, r2.status_code), (202, 200))
        self.assertEqual(r1.get_json()["id"], r2.get_json()["id"])
        self.assertTrue(r2.get_json()["duplicate"])
        self.assertEqual(len(c.get("/api/tasks", headers=self.H).get_json()["tasks"]), 1)

    def test_static_files_are_allowlisted(self):
        c = self.app([])
        self.assertEqual(c.get("/static/voice.js").status_code, 200)
        self.assertEqual(c.get("/static/app.js").status_code, 200)
        for name in ("config.py", "..%2Fagent_app%2Fconfig.py", "index.html", ".env"):
            self.assertEqual(c.get(f"/static/{name}").status_code, 404, name)

    def test_csp_blocks_inline_scripts(self):
        c = self.app([])
        csp = c.get("/").headers["Content-Security-Policy"]
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertNotIn("unsafe-eval", csp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
