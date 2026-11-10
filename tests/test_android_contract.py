"""STATIC contract check between the Kotlin companion source and the Flask server.

This is NOT a compile and NOT a device test. It cannot prove the APK builds or runs. It only catches drift: an
endpoint path, action name, JSON field or manifest permission that no longer matches the server."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_app import phone  # noqa: E402
from agent_app.config import Config  # noqa: E402
from agent_app.server import create_app  # noqa: E402

KT = ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "personalagent" / "companion"
MANIFEST = ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml"


def kotlin_source():
    return {p.name: p.read_text() for p in KT.glob("*.kt")}


class AndroidContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = kotlin_source()
        cls.all = "\n".join(cls.src.values())
        cls.manifest = MANIFEST.read_text()
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cfg = Config.from_env(env={"AGENT_DATA_DIR": tmp.name, "AGENT_TOKEN": "t" * 40}, load_dotenv=False)
        app = create_app(cfg, inline=True)
        cls.addClassCleanup(app.extensions["agent"]["store"].close)
        cls.rules = [r.rule for r in app.url_map.iter_rules()]

    def test_sources_exist(self):
        for name in ("Api.kt", "Vault.kt", "Store.kt", "Actions.kt", "Poller.kt", "PollWorker.kt",
                     "Receivers.kt", "MainActivity.kt"):
            self.assertIn(name, self.src)

    def test_endpoints_used_by_the_app_exist_on_the_server(self):
        used = set(re.findall(r'"(/api/companion/[^"$]*)', self.all))
        self.assertTrue({"/api/companion/pair", "/api/companion/poll", "/api/companion/unpair"} <= used, used)
        for path in used:
            pattern = re.sub(r"<[^>]+>", "X", path) if "<" in path else path
            ok = any(re.fullmatch(re.sub(r"<[^>]+>", "[^/]+", rule), pattern.rstrip("/") + ("X" if path.endswith("/") else ""))
                     or rule.startswith(path) for rule in self.rules)
            self.assertTrue(ok, f"{path} is not served by the server")
        self.assertIn("/api/companion/commands/<int:command_id>/result", self.rules)
        self.assertIn("/api/companion/commands/${", self.all)

    def test_app_handles_every_allowlisted_action_and_nothing_else(self):
        handled = set(re.findall(r'^\s*"(\w+)" ->', self.src["Actions.kt"], re.M))
        self.assertEqual(handled, set(phone.ACTIONS))

    def test_json_field_names_match_the_server(self):
        for field in ("at_epoch_ms", "reminder_id", "title", "body", "url"):
            self.assertIn(f'"{field}"', self.src["Actions.kt"], field)
        server = (ROOT / "agent_app" / "server.py").read_text()
        for field in ("poll_seconds", "device_id", "token", "expires_at", "commands"):
            self.assertIn(field, server)
            self.assertIn(f'"{field}"', self.src["Poller.kt"], field)
        self.assertIn('"status"', self.src["Poller.kt"])
        self.assertIn('"detail"', self.src["Poller.kt"])

    def test_gradle_build_files_and_wrapper_are_present(self):
        a = ROOT / "android"
        for rel in ("gradlew", "gradlew.bat", "gradle/wrapper/gradle-wrapper.jar",
                    "gradle/wrapper/gradle-wrapper.properties", "build.gradle.kts", "settings.gradle.kts",
                    "gradle.properties", "app/build.gradle.kts"):
            self.assertTrue((a / rel).is_file(), rel)
        self.assertTrue((a / "gradlew").stat().st_mode & 0o111, "gradlew must be executable")
        props = (a / "gradle/wrapper/gradle-wrapper.properties").read_text()
        self.assertRegex(props, r"gradle-8\.(9|[1-9]\d)[\w.-]*-bin\.zip")  # AGP 8.5.2 needs Gradle 8.7+
        self.assertIn("android.useAndroidX=true", (a / "gradle.properties").read_text())
        wf = (ROOT / ".github/workflows/android.yml").read_text()
        self.assertIn("./gradlew", wf)
        self.assertIn("assembleDebug", wf)

    def test_manifest_has_only_the_expected_permissions(self):
        perms = set(re.findall(r'uses-permission android:name="([^"]+)"', self.manifest))
        self.assertEqual(perms, {"android.permission.INTERNET", "android.permission.POST_NOTIFICATIONS",
                                 "android.permission.RECEIVE_BOOT_COMPLETED", "android.permission.SCHEDULE_EXACT_ALARM"})
        for banned in ("SEND_SMS", "CALL_PHONE", "RECORD_AUDIO", "READ_CONTACTS", "ACCESSIBILITY",
                       "FOREGROUND_SERVICE", "READ_EXTERNAL_STORAGE", "MANAGE_EXTERNAL_STORAGE", "SYSTEM_ALERT_WINDOW"):
            self.assertNotIn(banned, self.manifest)
        self.assertIn('android:usesCleartextTraffic="false"', self.manifest)
        self.assertIn('android:allowBackup="false"', self.manifest)
        self.assertNotIn("<service", self.manifest)

    def test_app_never_contains_an_anthropic_key_and_is_https_only(self):
        self.assertNotRegex(self.all, r"sk-ant-")
        self.assertNotIn("ANTHROPIC", self.all)
        self.assertIn("https", self.src["Api.kt"].lower())
        self.assertNotRegex(self.all, r'"http://')

    def test_credential_goes_in_a_header_never_a_url(self):
        api = self.src["Api.kt"]
        self.assertIn("Authorization", api)
        self.assertNotRegex(self.all, r"\?token=|&token=|token=\$")

    def test_token_is_stored_through_the_keystore_vault(self):
        self.assertIn("AndroidKeyStore", self.src["Vault.kt"])
        self.assertIn("Vault", self.src["Store.kt"])

    def test_brackets_are_balanced_in_every_kotlin_file(self):
        for name, text in self.src.items():
            # One pass so that "https://..." inside a string is not mistaken for a comment.
            token = re.compile(r'"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\])\'|//[^\n]*|/\*.*?\*/', re.S)
            stripped = token.sub(lambda m: "" if m.group(0).startswith("/") else '""', text)
            for a, b in ("()", "{}", "[]"):
                self.assertEqual(stripped.count(a), stripped.count(b), f"{name}: {a}{b}")


if __name__ == "__main__":
    unittest.main()
