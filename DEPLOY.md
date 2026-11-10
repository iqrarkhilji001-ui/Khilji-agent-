# Deploying so you can open the agent from your Android phone (no computer of your own)

Researched 2026-10-28 from the providers' own docs. Prices and limits change; re-check before relying on them.
**Nothing here has been deployed or tested on a real host.** The build sandbox had no Docker daemon, no PyPI
access and no access to these services. Follow the steps and use the Self-check to see what really works.

> ## Important: Render's free tier forgets everything
> Render free web services have an **ephemeral filesystem**. The SQLite database (task history, saved memory)
> and workspace files are **erased on every restart, redeploy and sleep** (it sleeps after 15 idle minutes).
> Do not expect the agent to remember anything long-term on this host. Anything important must be copied out
> by you. Persistent storage on Render is a paid feature, which this project does not buy.

## Option A (recommended): Render free web service
From [render.com/docs/free](https://render.com/docs/free):
- Free web services need **no credit card**; HTTPS and a `*.onrender.com` address are included.
- Spins down after 15 minutes without traffic; waking takes about a minute. A task running at that moment is lost.
- Ephemeral filesystem (see the box above). 750 free instance hours per month.

Because the host is shared and public, this build ships with **code execution turned off**
(`AGENT_ENABLE_RUN_PYTHON=0`) and every request needs your secret token. The server **refuses to start** if
`AGENT_TOKEN` is missing (a generated token would change at each restart and lock you out).

### Steps
1. Create a free GitHub account and a **private** repository, then put the ZIP's contents in it. **Correction to
   earlier versions of this guide:** GitHub's web "Upload files" page cannot upload folders from a phone browser, and
   this project has several nested folders. Phone-only route: use Termux (`pkg install git unzip`, unzip the ZIP,
   `git init`, `git push`; exact commands are in ANDROID_SETUP_UR.md, part 1). The GitHub password prompt takes a
   personal access token that you type only into Termux. Or ask someone with a computer to drag the unzipped folder
   into the repository page. Do **not** upload any `.env` file or key. (Not tested from a phone.)
2. Create a free account at render.com. New > Web Service > connect that repository.
3. Settings: Runtime **Docker**, Instance type **Free**, Health check path `/api/health`.
4. Environment variables (type them into Render's form yourself; never send them in chat):
   - `ANTHROPIC_API_KEY` = your key from console.anthropic.com (billed to you by Anthropic; set a spend limit there)
   - `AGENT_TOKEN` = a long random password, at least 32 characters
   - `AGENT_ENABLE_RUN_PYTHON` = `0`
   - `AGENT_TRUST_PROXY` = `1` (already set in the Dockerfile; keep it)
   - (`AGENT_EPHEMERAL_STORAGE=1` and the data folder are already set in the Dockerfile.)
5. Deploy. If the deploy log says "AGENT_TOKEN is not set", add it and redeploy.
6. Open `https://<your-service>.onrender.com` in Chrome on your phone and paste the token once.
7. Go to the **About** tab:
   - The yellow cards list warnings (temporary storage, etc.).
   - Press **Test API key** (one tiny real call). A red message tells you exactly what is wrong (bad key, model not found, no credit).
   - Press **Test web search**. This is the first real test of DuckDuckGo from Render's servers. If it fails,
     search is unavailable there; the agent can still read pages you give it by URL.
8. Chrome menu > Add to Home screen.
9. Voice: tap the mic and allow the microphone. It needs HTTPS (Render provides it). Untested on a real phone.
10. Android companion (notifications/reminders): follow ANDROID_SETUP_UR.md. In the **Phone** tab create a pairing code and
    enter it with your server address in the companion.

### What happens when the free server sleeps or restarts
- Pages and voice stop working until it wakes (about a minute after the next visit).
- Everything stored locally is erased: task history, saved memory, **paired phones and queued phone commands**.
  Companions get a 401, stop, and tell you to pair again. Reminders already scheduled by the native Android app stay
  on the phone (system alarms); reminders held by the Termux companion stay in its own file too.
- Commands the agent queued while the phone was offline are lost, and the agent will have reported them as
  "queued", never "done".
- A phone that polls every 30 seconds keeps the service awake, which uses about 744 of the 750 free hours in a
  31-day month. If the hours run out, Render suspends the service until the next month.
- Reliable scheduled reminders therefore come from the phone itself (native app alarms), not from the free server.

(`render.yaml` is an optional "Blueprint" shortcut. I could not confirm whether Blueprints need a payment method,
so the manual route above is the safe one.)

## Persistent storage (optional, paid)
The free Render plan cannot keep data. To keep task history, memory and paired phones across restarts you need a host
with a persistent disk. On Render that means a paid instance type with a **Disk** attached (Render's dashboard shows
the current price; this project buys nothing for you):
1. Service > Disks > add a disk, mount path `/data`.
2. Set `AGENT_DATA_DIR=/data` and `AGENT_EPHEMERAL_STORAGE=0` (this hides the "temporary storage" warning; only do it
   once the disk really exists).
3. Keep exactly **one** instance: the app runs tasks in memory and uses a single SQLite file, so it cannot be scaled to
   several instances. Redeploys still interrupt a running task.
The SQLite file is `agent.db` inside `AGENT_DATA_DIR`; workspace files are in `workspace/` next to it. Back up by
downloading them, never commit them. **Untested on a real host.**

## Option B: PythonAnywhere free account
Their docs: 1 web app, 512 MiB disk, a 1-month expiry you must renew, outbound internet only to an allowlist that
**includes** `api.anthropic.com` and `duckduckgo.com` ([allowlist](https://www.pythonanywhere.com/whitelist/)). Disk is
persistent, so memory would survive. **Unverified:** whether background task threads keep running between requests
on the free tier (this app runs tasks in threads). Test a short task first.

## Option C: run it on the phone itself (Termux)
Not tested here. Install Termux from F-Droid, `pkg install python`, copy the project over,
`pip install -r requirements.txt && python run.py --check && python run.py`, then open `http://127.0.0.1:8080`.

## Environment variables (all of them)
| Name | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Your Anthropic key. Set it in the host's form, never in files or chat |
| `AGENT_TOKEN` | yes on hosts (the server refuses to start without it) | Login token, 32+ characters |
| `AGENT_ENABLE_RUN_PYTHON` | set `0` on hosts (the Dockerfile already does) | Disables the code-execution tool |
| `AGENT_EPHEMERAL_STORAGE` | set by the Dockerfile | Shows the temporary-storage warning |
| `ANTHROPIC_MODEL` | no (`claude-sonnet-5-5`) | Model ID override |
| `AGENT_PHONE_WAIT` | no (20) | Seconds the agent waits for a phone to confirm |
| `AGENT_DEVICE_TOKEN_DAYS` | no (90) | Lifetime of a paired phone's credential |
| `AGENT_NOTIFY_ON_COMPLETE` | no (0) | `1` = show a generic "task finished/failed" notification on paired phones (no task text is sent) |
| `AGENT_TRUST_PROXY` | set to `1` by the Dockerfile | Use the real client address behind Render's proxy for the login lockout and rate limits. Never set it when the app is directly reachable |
| `AGENT_ALLOW_RUN_PYTHON_IN_PRODUCTION` | no (0) | The production entry point ignores `AGENT_ENABLE_RUN_PYTHON=1` unless this is also `1`. Leave it off |
| `BRAVE_API_KEY` | no | Optional metered search API (Brave requires a card) |
| `AGENT_MAX_STEPS`, `AGENT_MAX_TOKENS`, `AGENT_HOST`, `AGENT_PORT`, `AGENT_DATA_DIR` | no | Tuning |

`/api/health` returns only `{"ok": true}` and needs no login; it reveals nothing about configuration.

## Security checklist for any public deployment
- Random token of 32+ characters; never reuse a password.
- Keep code execution off (`AGENT_ENABLE_RUN_PYTHON=0`). With it on, approved code runs as the same OS user as the
  server and could read the server's environment, including your API key.
- Set a monthly spend limit in your Anthropic console.
- If you suspect the token leaked, change `AGENT_TOKEN` in the host's dashboard and redeploy.
