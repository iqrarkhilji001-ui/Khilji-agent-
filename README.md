# Personal AI Agent — Gemini Free Trial

Phone-friendly self-hosted assistant with voice UI, tools, verification, and approval gates.

## Trial rules
- First server initialization starts a persistent 30-day trial record in SQLite. It survives restarts only if the data directory persists.
- AI calls stop at expiry. The app never starts a paid plan or enables billing automatically.
- Daily AI runtime is capped at 600 seconds (10 minutes) across tasks and verification; it resets at midnight Asia/Karachi. One in-flight request may finish after the cap. Run one app instance.
- Google controls Gemini Free Tier availability, quotas, and policy. Use a Google AI Studio project that remains on Free Tier. Do not enable billing or attach a paid billing account. Google may change free-tier limits.

## Android setup
Follow [ANDROID_SETUP_UR.md](ANDROID_SETUP_UR.md) for the no-card Termux setup. The phone runs the local server; it is not a public website. Keep the phone and Termux running while using the agent.

## Basic setup
1. Install Python 3.10+ and run `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`.
3. Set `GEMINI_API_KEY` privately from Google AI Studio. Never paste it into chat or commit it.
4. Keep `GEMINI_MODEL=gemini-2.5-flash` unless that model is available to your Free Tier project.
5. Run `python run.py --check` (uses a small amount of API quota), then `python run.py`.
6. Open `http://127.0.0.1:8080`.

## Environment
- `GEMINI_API_KEY`: required, private.
- `GEMINI_MODEL`: default `gemini-2.5-flash`.
- `AGENT_TRIAL_DAYS`: default 30 (1–90).
- `AGENT_DAILY_AI_SECONDS`: default 600.
- `AGENT_DATA_DIR`: must be persistent to preserve trial and usage history.

## Honest status
Gemini adapter and trial enforcement are in the `gemini-free-tier` branch. They have not been run against a real Gemini key or on a physical Android phone. GitHub Actions runs automated tests after branch commits; a green CI result is required before calling the code verified. The trial starts on first server initialization, not on commit date.
