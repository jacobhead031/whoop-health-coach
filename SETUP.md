# Setup — your own health coach

A personal health coach that runs on your Mac. It reads your WHOOP data, emails
you every night at 9 pm asking for your calories (reply with the number — e.g.
the total your Cal AI app shows), asks for your weight Sunday mornings, and
every Sunday at 8 pm emails you a coach-written PDF slide deck reviewing your
week with 3 concrete targets — and next week it grades you on them.

Everything runs locally and emails you from your own Gmail. ~20 minutes of
setup, most of it account signups.

## 1. Prerequisites

- A Mac (the scheduling uses launchd, macOS-only) that's usually on in the
  evenings — jobs fire on wake if it's asleep, but skip if it's powered off.
- **Google Chrome** — renders the weekly PDF. https://google.com/chrome
- **Claude Code** — writes the weekly deck. Install and log in:
  `npm install -g @anthropic-ai/claude-code`, then run `claude` once to sign in.
  (Without it you still get a plain-text weekly report email.)
- Python 3 (already on macOS) and git.

## 2. WHOOP developer app (free, gives access to *your* data)

1. Go to https://developer.whoop.com and sign in with your WHOOP account.
2. Create an app. Add this exact redirect URI: `http://localhost:8765/callback`
3. Enable scopes: `read:recovery`, `read:cycles`, `read:sleep`, `offline`
4. Copy the Client ID and Client Secret.

## 3. Gmail App Password (lets the scripts send/read your email)

1. Turn on 2-Step Verification on your Google account if it isn't already.
2. Go to https://myaccount.google.com/apppasswords and create one; copy the
   16-character password.

## 4. Install

```
git clone <REPO_URL> health-coach
cd health-coach
./install.sh
```

Then open `.env` in any editor and fill in:

- `WHOOP_CLIENT_ID` / `WHOOP_CLIENT_SECRET` — from step 2
- `GMAIL_ADDRESS` — your Gmail
- `GMAIL_APP_PASSWORD` — from step 3
- `COACH_TZ` — uncomment and set if you're not in Eastern time (e.g.
  `America/Vancouver`)

Connect WHOOP (one-time, opens your browser):

```
.venv/bin/python3 authorize.py
```

## 5. Smoke test

```
.venv/bin/python3 fetch_day.py     # should print yesterday's WHOOP data
./weekly_email.sh                  # should email you a deck within a few minutes
```

If both work, you're done. The schedule from here:

| When | What |
|------|------|
| Every day 9 pm | Email: "Calories for YYYY-MM-DD?" — reply with a number, e.g. `2340` (optionally macros: `2340, 150p 60f 220c`) |
| Sunday 9 am | Email: "Weight for YYYY-MM-DD?" — reply with your weight, same unit every week |
| Sunday 8 pm | Your weekly coaching deck PDF |

Replies are picked up by the next nightly run, so reply any time before 9 pm
the next day. Using Cal AI? Just reply with the daily total it shows you.

## Fixing things

- Logs are in `logs/` (`daily.log`, `weigh.log`, `weekly.log`).
- Logged a wrong number? Just reply again to that day's email, or run
  `.venv/bin/python3 log_calories.py 2200 2026-07-10` — the latest entry for a
  date wins.
- Re-running `./install.sh` is always safe.
