"""Opt-in 'task finished' phone notification (server side, mocked model; no real phone involved)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agent import VERIFY_OK, text  # noqa: E402
from test_devices import DeviceBase  # noqa: E402


class NotifyOnCompleteTests(DeviceBase):
    def commands(self):
        return self.ext["store"].list_commands(50)

    def test_off_by_default_queues_nothing(self):
        c = self.app([text("Done."), VERIFY_OK])
        self.pair(c)
        self.run_task(c)
        self.assertEqual(self.commands(), [])

    def test_enabled_queues_one_generic_notification_without_task_text(self):
        self.cfg.notify_on_complete = True
        c = self.app([text("The result is SECRETANSWER."), VERIFY_OK])
        _, dh, _, _ = self.pair(c)
        tid = self.run_task(c, "very private request text")
        cmds = self.commands()
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["action"], "notify")
        blob = str(cmds[0])
        self.assertNotIn("private", blob)
        self.assertNotIn("SECRETANSWER", blob)
        self.assertIn(f"#{tid}", blob)
        polled = self.poll(c, dh)
        self.assertEqual(len(polled), 1)

    def test_no_paired_device_is_fine(self):
        self.cfg.notify_on_complete = True
        c = self.app([text("Done."), VERIFY_OK])
        tid = self.run_task(c)
        self.assertTrue(self.task(c, tid)["task"]["status"].startswith("completed"))
        self.assertEqual(self.commands(), [])

    def test_failed_task_notifies_and_repeat_call_is_idempotent(self):
        from agent_app import phone
        self.cfg.notify_on_complete = True
        c = self.app([])
        self.pair(c)
        store = self.ext["store"]
        tid = store.create_task("x")
        self.assertEqual(phone.notify_task_finished(self.cfg, store, tid, "failed"), 1)
        self.assertEqual(phone.notify_task_finished(self.cfg, store, tid, "failed"), 0)
        self.assertEqual(phone.notify_task_finished(self.cfg, store, tid, "cancelled"), 0)
        self.assertEqual(len(self.commands()), 1)

    def test_notification_error_never_breaks_the_task(self):
        from agent_app import phone
        self.cfg.notify_on_complete = True
        c = self.app([text("Done."), VERIFY_OK])
        self.pair(c)
        original = phone.notify_task_finished

        def boom(*a, **k):
            raise RuntimeError("db down")
        import agent_app.agent as agent_mod
        agent_mod.notify_task_finished = boom
        self.addCleanup(setattr, agent_mod, "notify_task_finished", original)
        tid = self.run_task(c)
        self.assertTrue(self.task(c, tid)["task"]["status"].startswith("completed"))


if __name__ == "__main__":
    unittest.main()
