# Roadmap: Android phone actions

What exists today is described in README.md (status table), SECURITY.md and ANDROID_SETUP_UR.md. This file only
lists what is NOT done yet, in a sensible order.

## Next, in order
1. **Prove it on a real phone.** Run the Termux companion (ANDROID_SETUP_UR.md, route A) and report what happens.
   Until then nothing phone-related is "working", only "tested against mocks".
2. **Get the APK built.** Run the GitHub workflow; the first run will probably need small fixes because the Kotlin
   source was never compiled.
3. **Persistent storage.** Render's free disk is wiped on sleep, which also unpairs phones. Options: a free
   external Postgres/SQLite-compatible service, or a host with a persistent disk. Needs a decision on cost.
4. **Faster delivery to a closed app.** Push notifications (needs a Firebase project and account) or a foreground
   service (permanent notification, battery cost). Neither is built.
5. **Spoken replies and voice on the phone:** already in the web page; verify on a device, then consider a
   replaceable speech provider if browser recognition is poor for Urdu.

## Possible later actions (each needs its own explicit permission and approval design)
Open an app or URL directly, set a system alarm/timer through the clock app, read battery state, share text, copy to
clipboard. Sending SMS or placing calls would need dangerous runtime permissions and a Play-policy review; they are
intentionally not planned until the basics are proven.

## Never
Unrestricted shell, arbitrary intents, hidden recording or monitoring, accessibility-service control, reading
messages or files on the phone. These are outside this project's allowlist by design.
