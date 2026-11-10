"""Browser-level tests of the voice-first UI in headless Chromium (Playwright).

IMPORTANT: SpeechRecognition and speechSynthesis are MOCKED here (injected before the page loads). These tests
prove the page wiring (states, no duplicate submissions, error messages, text fallback, speaking replies).
They do NOT prove a real microphone, permission prompt or speech engine on an Android phone.
Skipped automatically if Playwright or Chromium is not installed."""
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from werkzeug.serving import make_server  # noqa: E402

from agent_app.config import Config  # noqa: E402
from agent_app.server import create_app  # noqa: E402
from test_agent import FakeLLM, text  # noqa: E402

TOKEN = "u" * 32
MOCK_SPEECH = """
(() => {
  window.__recCount = 0; window.__spoken = []; window.__cancelled = 0; window.__pending = [];
  window.__mode = window.__mode || 'grant';
  window.__transcript = window.__transcript || 'say hello';
  class MockRec {
    constructor() { window.__recCount++; }
    start() {
      const mode = window.__mode;
      setTimeout(() => {
        if (mode === 'none') return;      // the engine starts and then never answers (a hung recognizer)
        if (mode === 'deny') this.onerror && this.onerror({ error: 'not-allowed' });
        else if (mode === 'network') this.onerror && this.onerror({ error: 'network' });
        else if (mode === 'silence') this.onend && this.onend();
        else {
          const r1 = Object.assign([{ transcript: window.__transcript.slice(0, 4) }], { isFinal: false });
          this.onresult && this.onresult({ resultIndex: 0, results: [r1] });
          const r2 = Object.assign([{ transcript: window.__transcript }], { isFinal: true });
          this.onresult && this.onresult({ resultIndex: 0, results: [r2] });
          this.onresult && this.onresult({ resultIndex: 0, results: [r2] });   // engines sometimes repeat
          this.onend && this.onend();
        }
      }, 30);
    }
    stop() {} abort() { window.__aborted = (window.__aborted || 0) + 1; }
  }
  class MockUtterance { constructor(t) { this.text = t; } }
  const synth = {
    speak(u) { window.__spoken.push(u.text); if (window.__holdSpeech) window.__pending.push(u); else setTimeout(() => u.onend && u.onend(), 10); },
    cancel() { window.__cancelled++; const p = window.__pending; window.__pending = []; p.forEach(u => u.onerror && u.onerror({ error: 'interrupted' })); },
    getVoices() { return []; }
  };
  if (!window.__noVoice) {
    window.SpeechRecognition = MockRec; window.webkitSpeechRecognition = MockRec;
    window.SpeechSynthesisUtterance = MockUtterance;
    Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
  } else {
    window.SpeechRecognition = undefined; window.webkitSpeechRecognition = undefined;
    Object.defineProperty(window, 'speechSynthesis', { value: undefined, configurable: true });
  }
})();
"""


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class VoiceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.pw.stop()
            raise unittest.SkipTest(f"Chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def start_server(self, script):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config.from_env(env={"AGENT_DATA_DIR": tmp.name, "AGENT_TOKEN": TOKEN,
                                   "ANTHROPIC_API_KEY": "sk-ant-fake-ui-test-000"}, load_dotenv=False)
        cfg.auth_fail_delay = 0
        self.llm = FakeLLM(script)
        app = create_app(cfg, llm=self.llm)
        self.store = app.extensions["agent"]["store"]
        srv = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(self.store.close)
        self.base = f"http://127.0.0.1:{srv.server_port}/"

    def open(self, script, mode="grant", transcript="say hello", hold_speech=False, no_voice=False, bypass_csp=True):
        self.start_server(script)
        # Playwright's wait_for_function uses eval(), which the page's strict CSP rightly blocks; so most tests
        # bypass the CSP in the test browser. test_csp_is_enforced_and_the_app_works_under_it keeps it enforced.
        ctx = self.browser.new_context(viewport={"width": 390, "height": 844}, bypass_csp=bypass_csp)
        self.addCleanup(ctx.close)
        ctx.add_init_script(f"window.__mode={mode!r}; window.__transcript={transcript!r}; "
                            f"window.__holdSpeech={str(hold_speech).lower()}; window.__noVoice={str(no_voice).lower()};")
        ctx.add_init_script(MOCK_SPEECH)
        page = ctx.new_page()
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.goto(self.base)
        page.fill("#tok", TOKEN)
        page.click("#loginBtn")
        page.wait_for_selector("#app:not(.hidden)")
        return page

    def tasks(self):
        return self.store.list_tasks(50)

    def test_voice_round_trip_is_sent_once_and_spoken(self):
        page = self.open([text("Hello there, friend!")])
        page.click("#mic")
        page.wait_for_function("window.__spoken.length > 0", timeout=15000)
        self.assertEqual(page.evaluate("window.__spoken[0]"), "Hello there, friend!")
        self.assertEqual(len(self.tasks()), 1, "duplicate engine events must not create duplicate tasks")
        self.assertEqual(self.tasks()[0]["request"], "say hello")
        self.assertIn("Heard: say hello", page.inner_text("#heard"))
        self.assertIn("Hello there, friend!", page.inner_text("#current"))
        page.wait_for_function("document.getElementById('mic').dataset.state === 'idle'")
        self.assertEqual(self.errors, [])

    def test_double_tap_creates_one_recognition_and_one_task(self):
        page = self.open([text("ok")])
        page.dblclick("#mic")
        page.wait_for_function("window.__spoken.length > 0", timeout=15000)
        self.assertEqual(page.evaluate("window.__recCount"), 1)
        page.fill("#req", "typed twice")
        page.dblclick("#send")
        page.wait_for_timeout(500)
        self.assertEqual([t["request"] for t in self.tasks() if t["request"] == "typed twice"], ["typed twice"])

    def test_microphone_permission_denied(self):
        page = self.open([], mode="deny")
        page.click("#mic")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'error'")
        msg = page.inner_text("#voiceMsg")
        self.assertIn("permission was denied", msg)
        self.assertTrue(page.is_visible("#retry"))
        self.assertEqual(self.tasks(), [])
        page.evaluate("window.__mode = 'grant'")      # the user allows the mic, then presses Retry
        self.llm.script.append(text("Back again."))
        page.click("#retry")
        page.wait_for_function("window.__spoken.length > 0", timeout=15000)
        self.assertEqual(len(self.tasks()), 1)

    def test_recognition_failures_show_specific_messages(self):
        page = self.open([], mode="network")
        page.click("#mic")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'error'")
        self.assertIn("internet connection", page.inner_text("#voiceMsg"))
        page.evaluate("window.__mode = 'silence'")
        page.click("#retry")
        page.wait_for_function("document.getElementById('voiceMsg').textContent.includes('did not hear')")
        self.assertEqual(self.tasks(), [])

    def test_user_can_interrupt_speech(self):
        page = self.open([text("A long answer that is being read out loud.")], hold_speech=True)
        page.click("#mic")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'speaking'", timeout=15000)
        self.assertTrue(page.is_enabled("#stopSpeak"))
        page.click("#stopSpeak")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'idle'")
        self.assertGreaterEqual(page.evaluate("window.__cancelled"), 1)
        self.assertFalse(page.is_enabled("#stopSpeak"))
        self.assertEqual(self.errors, [])

    def test_text_only_fallback_without_speech_apis(self):
        page = self.open([text("Typed reply.")], no_voice=True)
        self.assertFalse(page.is_visible("#mic"))
        self.assertEqual(page.inner_text("#voiceState"), "Text mode")
        self.assertIn("not supported", page.inner_text("#voiceMsg"))
        page.fill("#req", "hello by typing")
        page.click("#send")
        page.wait_for_function("document.getElementById('current').textContent.includes('Typed reply.')", timeout=15000)
        self.assertEqual(self.errors, [])

    def test_spoken_replies_can_be_turned_off(self):
        page = self.open([text("Silent answer.")])
        page.uncheck("#speakReplies")
        page.click("#mic")
        page.wait_for_function("document.getElementById('current').textContent.includes('Silent answer.')", timeout=15000)
        # The result text is saved a moment before the task is marked final, so the UI returns to idle on its
        # next poll (every 2 s). Wait for that state instead of sleeping a fixed time.
        page.wait_for_function("document.getElementById('mic').dataset.state === 'idle'", timeout=10000)
        self.assertEqual(page.evaluate("window.__spoken.length"), 0)

    def test_cancel_button_stops_listening(self):
        # Regression: this test used to set mode 'none' AFTER open(), but the mock had no 'none' branch, so the mock
        # answered after 30 ms and the test only passed when its poll happened to land inside that window.
        page = self.open([], mode="none")             # recognition starts but never answers
        page.click("#mic")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'listening'")
        page.wait_for_timeout(400)                    # the mock really stays silent, so we are still listening
        self.assertEqual(page.evaluate("document.getElementById('mic').dataset.state"), "listening")
        page.click("#cancelBtn")
        page.wait_for_function("document.getElementById('mic').dataset.state === 'idle'")
        self.assertGreaterEqual(page.evaluate("window.__aborted"), 1, "cancel must abort the recognizer")
        self.assertEqual(self.tasks(), [], "cancelling while listening must not create a task")

    def test_csp_is_enforced_and_the_app_works_under_it(self):
        page = self.open([], bypass_csp=False)          # login and tab rendering already ran under the real CSP
        page.click("nav [data-tab=about]")
        page.wait_for_selector("#about .card")
        self.assertEqual([e for e in self.errors if "Content Security Policy" in e], [],
                         "the app itself must not trigger CSP violations")
        self.assertEqual(page.evaluate("document.querySelectorAll('script:not([src])').length"), 0)
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        injected = page.evaluate("""() => { const s = document.createElement('script');
            s.textContent = 'window.__injected = 1'; document.body.appendChild(s); return window.__injected === 1; }""")
        self.assertFalse(injected, "an injected inline script must be blocked by the CSP")

    def test_phone_tab_shows_a_pairing_code_and_paired_device(self):
        page = self.open([])
        page.click("nav [data-tab=phone]")
        page.click("#pairBtn")
        page.wait_for_selector("#pairOut .code")
        code = page.inner_text("#pairOut .code")
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        import requests
        r = requests.post(self.base + "api/companion/pair", json={"code": code, "name": "Test phone"}, timeout=5)
        self.assertEqual(r.status_code, 201)
        page.wait_for_function("document.getElementById('devices').textContent.includes('Test phone')", timeout=15000)
        page.once("dialog", lambda d: d.accept())
        page.click("#devices button.danger")
        page.wait_for_function("document.getElementById('devices').textContent.includes('revoked')", timeout=15000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
