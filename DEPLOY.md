# Run Personal AI Agent on your Android phone (free local setup)

**Recommended for this project right now: Termux on Android.** No Render account, payment card, paid hosting, or public server is required. Your phone runs the server locally.

This setup has not yet been verified on your physical phone. Follow the checks below and treat their actual output as the test result.

## Before you start

- Android phone with internet access.
- Termux installed from the official F-Droid listing: https://f-droid.org/packages/com.termux/
- A Gemini API key created privately in Google AI Studio: https://aistudio.google.com/app/apikey
- Do not paste your API key or agent access token into ChatGPT, GitHub issues, screenshots, or messages.

## Install and download

Open Termux and run:

```bash
pkg update -y && pkg upgrade -y
pkg install -y git python nano
cd ~
git clone --branch gemini-free-tier https://github.com/iqrarkhilji001-ui/Khilji-agent-.git personal-agent
cd ~/personal-agent
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configure the private API key

```bash
cd ~/personal-agent
cp .env.example .env
nano .env
```

Set the value on the `GEMINI_API_KEY=` line to your own key. Keep the other defaults initially. Save with Ctrl+O, Enter, then exit with Ctrl+X.

The `.env` file is excluded by `.gitignore`; never commit it or share its contents.

## Run the checks

```bash
python -m unittest discover -s tests -v
python run.py --check
```

The first command runs local automated tests using fake model responses; it does not prove that Gemini works. The second command makes a small real Gemini API request and checks web search. It may use a small amount of API quota. Do not share the key if a check fails; share only the error text after checking it contains no secrets.

## Start the agent

```bash
python run.py
```

Keep Termux open and visit this address in Chrome on the same phone:

```
http://127.0.0.1:8080
```

If the app asks for an access token, the first startup prints it in Termux and saves it in the local data folder. Keep it private. The server listens on localhost only by default; this is not a public website and cannot be accessed from outside your phone.

Stop the server with Ctrl+C in Termux. Start it again with:

```bash
cd ~/personal-agent
python run.py
```

## Important limits

- The app's trial starts on its first server initialization and lasts 30 days; its timestamp is stored in the local SQLite database.
- The app limits model-call runtime to 10 minutes per Pakistan-time day, shared across tasks and verification. It resets at midnight Asia/Karachi.
- These are app-level limits. Gemini API availability, quotas, and free-tier eligibility are controlled by Google and can change. This app does not enable paid billing automatically.
- Keep the phone powered and Termux running while you use the agent. Android battery management may stop background processes; allow Termux background activity in Android settings.
- Clearing Termux app data or deleting the project data folder can erase local history, saved memory, pairing records, and the locally stored trial timestamp.
- This local setup does not expose the server to the internet. Do not change `AGENT_HOST` to `0.0.0.0` or forward port 8080 unless you have deliberately configured secure remote access.

## If something fails

- `ModuleNotFoundError`: run `pip install -r requirements.txt` again.
- `GEMINI_API_KEY is not set`: check the private `.env` file and spelling of the variable.
- `Address already in use`: stop the earlier server with Ctrl+C before restarting.
- Git clone error: check the branch name and internet connection.
- Android may restrict Termux background execution; reopen Termux and restart the server.

**Not currently recommended:** Render or other public hosting. The previous Render attempt required a billing card, so this project does not depend on it. No cloud deployment or real-phone test is claimed by this guide.
