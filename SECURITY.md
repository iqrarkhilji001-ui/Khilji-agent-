# Security and permissions

Plain statement of what this project protects, what it does not, and what each permission is for.
Written 2026-11-01. "Tested" means automated tests (mostly with mocks) plus local runs; none of this has been
audited by a third party, and nothing has been tested on a physical Android device.

## What the agent can and cannot do
| Capability | Default | Notes |
|---|---|---|
| Read/write files | Only inside `data/workspace/` | `..`, absolute paths and symlinks that leave the folder are rejected. Overwrite and delete need your approval. |
| Fetch web pages / search | Automatic | Results are labelled untrusted. Private and local addresses are blocked (SSRF guard). |
| Run Python | **Off on hosted deployments** (`AGENT_ENABLE_RUN_PYTHON=0`); always needs approval when on | Runs as the server's OS user. Do not enable on a shared or public host. |
| Phone: notification, reminder, cancel own reminder | Automatic | Phone must be paired. Only these actions exist. |
| Phone: show an https link | **Needs your approval**, shows the exact URL | Posts a notification; nothing opens until you tap it. |
| SMS, calls, contacts, location, microphone, screen, other apps, shell, accessibility | **Not implemented, not requested** | Not in the allowlist and not in the Android manifest. |

Approval applies to one specific tool call. A changed URL or changed arguments need a new approval.

## Authentication
- Browser/UI: one secret `AGENT_TOKEN` (32+ characters recommended) sent as a bearer header, compared in constant time.
- Failed logins from one client address are counted; after 10 in 5 minutes that address is blocked temporarily.
- Phones never use your login token. Pairing: you create a one-time code in the Phone tab (valid 10 minutes, one use). The phone exchanges it for its **own device credential**, stored on the server only as a hash, valid 90 days
  (`AGENT_DEVICE_TOKEN_DAYS`), revocable from the Phone tab or from the phone.
- A device credential works only on `/api/companion/*`; your login token does not work there and the reverse.
- Pairing a new device always requires you to be logged in and press the button; the assistant cannot pair a device.
- The credential is sent in an `Authorization` header, never in a URL; logs and stored step records redact tokens and API keys.
- No cookies are used, so cross-site request forgery does not apply; the page is served with a strict Content-Security-Policy (no inline scripts).

## The phone command channel
- The server never connects to the phone. The phone polls over HTTPS (outbound only), so no inbound port is exposed.
- States: approved -> running -> completed / failed, or cancelled / expired. Each command has a unique id, an expiry and an idempotency key; redelivered commands are executed once and the stored outcome is repeated.
- The agent is told a phone action succeeded only if the phone reported success. "Queued" is never reported as "done".
- "completed" means the Android API call returned without error, not that you saw or tapped anything. Android does not tell an app that a notification was read, and an alarm that was scheduled can still be delayed by battery optimisation.

## Android permissions requested by the native app (android/)
| Permission | Why | You control it |
|---|---|---|
| INTERNET | Talk to your server over HTTPS (cleartext traffic is disabled) | - |
| POST_NOTIFICATIONS (Android 13+) | Show notifications and reminders | Tap "Allow notifications" or use system settings |
| SCHEDULE_EXACT_ALARM (Android 12+) | Reminders at the exact minute. Without it reminders may be delayed | Tap "Allow exact reminders"; revocable in system settings |
| RECEIVE_BOOT_COMPLETED | Re-register reminders after the phone restarts | - |

Credential storage in the native app: AES-GCM key in the Android Keystore encrypts the token; backups are disabled (`allowBackup=false`). The Anthropic API key is never sent to or stored in the app.
The Termux companion stores its credential in a file readable only by the Termux user (mode 600); that is not hardware-backed.

## Android limits you should know
- Background work: while the app is closed, Android may run the poller no more often than every 15 minutes and may delay it more in Doze or battery saver. A foreground service would be faster but needs a permanent notification; it is not included.
- The app is not a "always listening" assistant. Voice input happens in the browser page while it is open; Chrome decides if the microphone works, usually needs internet and HTTPS.
- Disconnect: "Disconnect and forget this phone" tells the server, cancels this app's reminders and deletes the credential and its Keystore key, even if the server is unreachable.

## Hosting risks
- Behind a reverse proxy (Render) the app must trust the proxy's forwarded address (`AGENT_TRUST_PROXY=1`, set in the
  Dockerfile); otherwise the failed-login lockout would count every visitor as one client.
- The production entry point (`agent_app.wsgi`) forces `run_python` off even if `AGENT_ENABLE_RUN_PYTHON=1`, unless
  `AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION=1` is also set (not recommended).
- Optional task-finished phone notifications (`AGENT_NOTIFY_ON_COMPLETE=1`) contain only the task number and a status
  word, never the request or the result, because notifications can appear on a locked screen.
- Public hosting exposes the login page to the internet. Use a long random token, keep run_python off, and set a monthly spend limit in the Anthropic console.
- Render free tier: storage is ephemeral. After a sleep or restart the database (history, memory, **paired phones, pending commands**) is wiped and phones must pair again. The companions detect this (401), stop, and ask you to pair again rather than retrying.
- A phone that keeps polling keeps a free Render service awake and uses its monthly free hours; if the hours run out, Render suspends the service until next month.

## Known gaps
- No independent security review; mock-based tests do not prove behaviour on real hosts or devices.
- Rate limiting is in memory per process (resets on restart; fine for a single worker, not for several).
- Device credentials last 90 days with no automatic refresh; re-pair when expired.
- Debug APKs built by the workflow are signed with a debug key, suitable for sideloading only.
