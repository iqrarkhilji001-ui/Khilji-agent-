# Personal AI Agent (v0.3.2)

A self-hosted assistant you use from your phone's browser. Talk or type; it plans, uses tools in a restricted
workspace, checks its own claims against what the tools really did, and reports. Risky actions pause for your
Approve/Deny. A small Android companion can show notifications and reminders on your phone, but only for actions
you allow, over an authenticated outbound connection.

> **Honest status.** Nothing here has been run with a real Anthropic key, deployed to a real host, built as an APK,
> or tested on a physical Android phone. Every "tested" below means automated tests that use a scripted fake model,
> mocked speech/Termux, and a local server. See the status table before relying on anything.

Docs: [DEPLOY.md](DEPLOY.md) (hosting) · [SECURITY.md](SECURITY.md) (permissions, risks) ·
[ANDROID_SETUP_UR.md](ANDROID_SETUP_UR.md) (phone setup, simple Urdu) · [ANDROID_NEXT.md](ANDROID_NEXT.md) (roadmap)

## Setup (computer or Termux)
1. Python 3.10+ (developed on 3.13). `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (console.anthropic.com; usage is billed to you; set a spend limit).
   Never share this file or the key in chat or in a ZIP.
3. `python run.py --check` makes one tiny real API call and one real search and explains any failure.
4. `python run.py` prints an access token on first start (also saved in `data/.agent_token`). Open http://127.0.0.1:8080.

Model: `claude-sonnet-5-5` (override with `ANTHROPIC_MODEL`), checked against Anthropic's model docs on 2026-10-28.
Sonnet 5.5 rejects `temperature`/`top_p`/`top_k` and forced tool choice (none are sent) and returns thinking blocks
that must be passed back unchanged (they are).

## Free-trial AI usage limit
- The default daily AI-call runtime allowance is **600 seconds (10 minutes)** across all tasks, including verification calls.
- Usage is measured around actual model API calls, rounded up to whole seconds, and stored in SQLite by Pakistan local date (`Asia/Karachi`). It survives normal server restarts when the data directory is persistent and resets at midnight Pakistan time.
- Override with `AGENT_DAILY_AI_SECONDS` (60–86400 seconds). The default is 600. This is an AI runtime cap, not a 30-day subscription/billing control; configure the model provider separately and do not enable paid billing during the trial.
- A call already in progress can finish after the cap is reached; the next model call is blocked. Run a single app instance for reliable enforcement across tasks.

## Using it
- **Voice:** mic button (Chrome on Android, HTTPS or localhost). States: listening, working, speaking, error. You see
  the recognised text; replies are shown and spoken; Stop speaking / Retry / Cancel; language list (English, Urdu, Hindi);
  text always works. Browser speech recognition usually needs internet and varies by language.
- **Phone tab:** create a one-time pairing code, see paired phones and recent commands, disconnect a phone.
- **About tab:** warnings, "Test API key", "Test web search".

## Phone actions (strict allowlist)
`notify`, `schedule_reminder`, `cancel_reminder` run automatically; `open_url` (shows a notification you tap) needs
your approval of the exact URL. Nothing else exists: no SMS, calls, location, microphone, other apps or shell.
The agent says an action is done only when the phone reports success; otherwise it says "queued" or "failed".
Two companions speak the same protocol:
- `companion/termux/companion.py`: runs in Termux, no APK needed (reminders fire only while it runs).
- `android/`: native Kotlin app (system alarms, Keystore-encrypted credential). Built by the GitHub workflow in
  `.github/workflows/android.yml`.

## Status
Legend: **Tested** = automated tests ran and passed (mocks noted). **Untested** = code exists, never executed.

| Feature | Status |
|---|---|
| Agent loop: plan, tools, verify, report, step limit, cancellation, thinking blocks kept, parallel tool calls | Tested (fake model) |
| Task history, memory (view/delete), approvals enforced server-side | Tested |
| Workspace confinement (`..`, absolute, symlink), SSRF guard, secret redaction, run_python off on hosts | Tested |
| Login token auth, lockout on repeated failures, rate limits, CSP, strict static-file allowlist | Tested |
| Voice controller (state machine, duplicate-event guard, errors, speech chunking) | Tested: 19 Node tests with **fake** speech APIs |
| Voice UI in Chrome: mic granted/denied, recognition failure and timeout, spoken reply, interrupt, text fallback, double-tap | Tested in headless Chromium with **mocked** SpeechRecognition/speechSynthesis |
| Real microphone, permission prompt, speech engine, Urdu/Hindi recognition | **Untested** (needs a physical phone) |
| Device pairing (one-time code, hashed revocable credential, expiry, revoke), command queue (status transitions, expiry, idempotency, redelivery) | Tested (Flask test client + real HTTP) |
| Phone-action allowlist, argument validation, approval for open_url, "queued is not done" | Tested |
| Termux companion: pair, poll, run, report, idempotent redelivery, reminders, revoked/unknown credential, offline backoff | Tested over **real HTTP** against the real server; Termux:API commands are **mocked** |
| Termux companion with real Termux/Termux:API on a phone | **Untested** |
| Native Android app (`android/`) | **Source only. Never compiled** (no Android SDK or Kotlin compiler available). A static contract test checks endpoints, action names, JSON fields, manifest permissions and bracket balance against the server; it is not a compile |
| Task-finished phone notification (opt-in `AGENT_NOTIFY_ON_COMPLETE`) | Tested (server side, fake model); never shown on a real phone |
| Voice listen timeout (20 s watchdog) | Tested (fake timers, mocked speech API) |
| Model API errors: retry on 429/5xx, timeouts, bad JSON, secret redaction | Tested with **mocked** `requests`; no live API call |
| APK build via GitHub Actions (Gradle wrapper 8.9 + workflow) | **Untested; no APK exists.** The build sandbox cannot reach Google/Maven (HTTP 403), so Gradle could not download the Android plugin or SDK. Gradle did parse the build files and stopped only at the missing plugin download |
| `web_search` (DuckDuckGo default, Brave optional) | Parsing/wiring tested with **fixtures**; live service **untested** |
| Real Claude API call with a valid key | **Untested** (invalid key reached the real API: clean 401) |
| Docker image, gunicorn, Render deployment | **Untested** (`wsgi.py` import tested) |
| Spoken-reply TTS on a phone, push notifications, foreground service, always-listening | Not implemented |
| SMS, calls, contacts, location, other-app control, email/calendar | Not implemented, deliberately |

Test counts at packaging time: `python -m unittest discover -s tests` = **153 passed, 0 skipped** (the browser tests ran); `node --test tests/voice.test.js` = **22 passed**.
The browser tests need `pip install playwright` + Chromium and are skipped automatically without them.

## Files
`run.py` · `agent_app/` (config, preflight, llm, agent loop, tools, search, phone, db, ratelimit, redact, server, wsgi) ·
`static/` (index.html, app.js, voice.js) · `companion/termux/` · `android/` · `tests/` · `Dockerfile`, `render.yaml` ·
`.github/workflows/android.yml`. Agent files live in `data/workspace/`; the agent cannot touch anything outside it.

## Limits to know before you rely on it
- **Render free tier:** ephemeral disk. Sleep/restart wipes history, memory **and paired phones** (phones then show
  "pair again"). A polling phone keeps the service awake and burns the 750 free hours.
- **Background polling on Android** is at most every ~15 minutes while the app is closed (Android limit).
- Costs: hosting here is free-tier only; the Anthropic API is billed to you per use.
