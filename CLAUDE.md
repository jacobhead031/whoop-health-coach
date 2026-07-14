# Personal Health Coach

A personal health coach agent. It reads WHOOP data (recovery, sleep, strain) via
the WHOOP v2 API, collects daily calories and weekly weight by email reply, and
every Sunday emails a coach-written PDF slide deck with 3 concrete targets —
which the next week's deck grades HIT/PARTIAL/MISS. Human setup steps live in
SETUP.md. Build incrementally — small working pieces over big-bang features.

## Tech

- Python 3, flat module layout. Deps in `requirements.txt` (requests, python-dotenv).
- Config in `.env` (see `.env.example`); WHOOP tokens cached in `tokens.json`.
  Both are gitignored — never commit or print them.
- Always run project code with `.venv/bin/python3` — system python3 lacks requests.

## WHOOP API v2 (verified against their OpenAPI spec, 2026-07)

- Base: `https://api.prod.whoop.com/developer/v2`
- Endpoints used: `/recovery`, `/activity/sleep`, `/cycle`. Collection endpoints
  paginate via `limit` / `nextToken` and filter with ISO-8601 `start` / `end`.
- OAuth 2.0 authorization-code flow:
  - Auth URL: `https://api.prod.whoop.com/oauth/oauth2/auth`
  - Token URL: `https://api.prod.whoop.com/oauth/oauth2/token`
  - Scopes: `read:recovery read:cycles read:sleep offline` (`offline` → refresh token)
- WHOOP quirks:
  - The refresh request must include `scope=offline` or no new refresh token is returned.
  - Refresh tokens are single-use — every refresh returns a new one; persist it immediately.
  - `state` param is required and must be at least 8 characters.
  - Records carry a `score_state` (`SCORED` / `PENDING_SCORE` / `UNSCORABLE`); `score`
    may be absent, so always guard before reading score fields.
- A WHOOP "cycle" is a physiological day running **sleep-onset to sleep-onset**,
  so it won't align with calendar-day boundaries. `weekly_coach.py` assigns each
  cycle to the local date 12 h after its start (the day you woke up into); a
  sleep goes to the date it ended; a recovery goes to its cycle's day. An
  in-progress cycle (no `end`) lands on today and falls outside the
  last-7-days window.

## Running

```
./install.sh           # venv + deps + the three launchd jobs (see SETUP.md)
# fill in .env, then:
python authorize.py    # one-time browser OAuth flow → tokens.json
python fetch_day.py [YYYY-MM-DD]   # defaults to yesterday
python log_calories.py 2200 [YYYY-MM-DD]   # log a day's intake (defaults to today)
python weekly_coach.py             # last-7-days readout + 2–3 adjustments
```

## Calories & coaching

- `calories.csv` (`date,calories,protein,fat,carbs`; macro columns optional/empty)
  is append-only; to correct a day, just log it again — readers take the last
  entry per date. `weight.csv` (`date,weight`) works the same, one row per Sunday.
  Both are created on first use and gitignored (personal data).
- `recs-history.md`: one `## YYYY-MM-DD` block per weekly deck with that week's
  Top 3 targets; `weekly_email.sh` appends the newest and feeds the previous one
  back so the deck grades adherence.
- Coaching logic lives in `ADJUSTMENT_RULES` in `weekly_coach.py`: plain threshold
  checks (tunables at the top of that file), evaluated top to bottom; the first 3
  that fire are printed. Keep rules transparent — no black-box scoring.

## Email automation (local launchd jobs)

- `coach_email.py` sends/reads Gmail over SMTP/IMAP using `GMAIL_ADDRESS` +
  `GMAIL_APP_PASSWORD` from `.env`. `daily` logs calorie AND weight replies then
  sends the night's prompt ("Calories for YYYY-MM-DD?" — reply with a number,
  optionally "2342, 150p 60f 220c"); `weight-prompt` sends the Sunday weigh-in;
  `send SUBJECT` emails stdin (`--attach FILE` supported). Dates use `COACH_TZ`
  (default America/Toronto).
- `install.sh` writes three launchd agents to `~/Library/LaunchAgents`
  (logs in `logs/`): daily 9 pm (`coach_email.py daily`), Sunday 9 am
  (`coach_email.py weight-prompt`), Sunday 8 pm (`weekly_email.sh`).
  Mac asleep at fire time → job runs at next wake; powered off → skipped.
- `weekly_email.sh`: `weekly_coach.py` readout → `claude -p` writes the deck's
  HTML structure only (coach persona + the slide spec live in the prompt in that
  script; all styling comes from `brand/apex.css`) → headless Chrome prints
  weekly-deck.pdf → emailed as an attachment, falling back to a plain-text
  readout email if Chrome or claude is unavailable.
- APEX Coaching brand lives in `brand/`: `apex.css` is the single design system
  (palette, slide geometry, components, fonts base64-embedded because headless
  Chrome refuses file:// font loads). `apex-logo.svg` is the mark;
  `APEX-philosophy.md` is the design philosophy.
