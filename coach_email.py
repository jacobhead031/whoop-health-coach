"""Email plumbing for the scheduled coach: send mail + turn replies into calorie logs.

Usage:
  python coach_email.py daily            # log calorie/weight replies, then send tonight's prompt
  python coach_email.py weight-prompt    # send the Sunday-morning weigh-in prompt
  python coach_email.py send SUBJECT [--attach FILE]   # send email, body from stdin (weekly coach uses this)
  python coach_email.py --selftest       # check the reply parsers

Sends from and to GMAIL_ADDRESS via Gmail SMTP using GMAIL_APP_PASSWORD (a Google
App Password, not the account password). Replies are read back over IMAP: the
daily prompt's subject is "Calories for YYYY-MM-DD?", so a reply's subject tells
us which day the number belongs to. A date already present in calories.csv is
skipped, so reruns are idempotent.

All dates use America/Toronto explicitly — the cloud agent that runs this is on UTC,
and 9 pm in Toronto is already tomorrow in UTC.
"""

import csv
import email
import email.policy
import imaplib
import mimetypes
import os
import re
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo(os.environ.get("COACH_TZ", "America/Toronto"))
ADDRESS = os.environ.get("GMAIL_ADDRESS") or sys.exit("GMAIL_ADDRESS missing from .env")


def app_password():
    # Google displays app passwords with spaces ("abcd efgh ..."); login wants them without.
    return os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
CALORIES_FILE = Path(__file__).resolve().parent / "calories.csv"
WEIGHT_FILE = Path(__file__).resolve().parent / "weight.csv"
CAL_FIELDS = ["date", "calories", "protein", "fat", "carbs"]
SUBJECT_DATE = re.compile(r"Calories for (\d{4}-\d{2}-\d{2})")
WEIGHT_SUBJECT_DATE = re.compile(r"Weight for (\d{4}-\d{2}-\d{2})")
MACRO = re.compile(r"(\d+)\s*([pfc])\b", re.IGNORECASE)


def send(subject, body, attachment=None):
    msg = EmailMessage()
    msg["From"] = msg["To"] = ADDRESS
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        path = Path(attachment)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/")
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(ADDRESS, app_password())
        s.send_message(msg)


def reply_top(text):
    """The reply's own text, stopping at the quoted original ("On ... wrote:" /
    "> ..."), which is full of date numbers we must not mistake for data."""
    top = []
    for line in text.splitlines():
        if line.startswith(">") or line.startswith("On "):
            break
        top.append(line)
    return " ".join(top)


def extract_calories(text):
    """(calories, protein, fat, carbs) from the reply; macros optional (None).
    Accepts e.g. "2342", "2,342", "2342, 150p 60f 220c" in any macro order."""
    top = reply_top(text)
    macros = {m.group(2).lower(): int(m.group(1)) for m in MACRO.finditer(top)}
    top_wo_macros = MACRO.sub("", top)
    for m in re.finditer(r"\d[\d,]*", top_wo_macros):
        n = int(m.group().replace(",", ""))
        if 200 <= n <= 20000:
            return n, macros.get("p"), macros.get("f"), macros.get("c")
    return None


def extract_weight(text):
    """First plausible body weight in the reply; lbs and kg both fit 30-500."""
    for m in re.finditer(r"\d+(?:\.\d+)?", reply_top(text)):
        w = float(m.group())
        if 30 <= w <= 500:
            return w
    return None


def logged_dates(path=CALORIES_FILE):
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {row["date"] for row in csv.DictReader(f)}


def migrate_calories_header():
    """One-time: widen the original date,calories header to include macros.
    Old 2-field rows stay as-is — DictReader just returns None for the rest."""
    if not CALORIES_FILE.exists():
        return
    lines = CALORIES_FILE.read_text().splitlines(keepends=True)
    if lines and lines[0].strip() == "date,calories":
        lines[0] = ",".join(CAL_FIELDS) + "\n"
        CALORIES_FILE.write_text("".join(lines))


def log_calories(day_iso, calories, protein=None, fat=None, carbs=None):
    migrate_calories_header()
    write_header = not CALORIES_FILE.exists() or CALORIES_FILE.stat().st_size == 0
    with CALORIES_FILE.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(CAL_FIELDS)
        writer.writerow([day_iso, calories, protein or "", fat or "", carbs or ""])


def log_weight(day_iso, weight):
    write_header = not WEIGHT_FILE.exists() or WEIGHT_FILE.stat().st_size == 0
    with WEIGHT_FILE.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "weight"])
        writer.writerow([day_iso, weight])


def plain_text(msg):
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part else ""


def handle_calorie_reply(day_iso, body):
    parsed = extract_calories(body)
    if parsed is None:
        print(f"Reply for {day_iso}: no calorie number found, skipped.")
        return False
    cal, p, fat, c = parsed
    log_calories(day_iso, cal, p, fat, c)
    macro_txt = f" ({p}p {fat}f {c}c)" if p or fat or c else ""
    print(f"Logged {cal} kcal{macro_txt} for {day_iso} from your reply.")
    return True


def handle_weight_reply(day_iso, body):
    weight = extract_weight(body)
    if weight is None:
        print(f"Weight reply for {day_iso}: no number found, skipped.")
        return False
    log_weight(day_iso, weight)
    print(f"Logged weight {weight:g} for {day_iso} from your reply.")
    return True


def log_replies():
    """Scan the inbox for replies to recent prompts and log any new days."""
    prompts = [
        ("Calories for", SUBJECT_DATE, logged_dates(), handle_calorie_reply),
        ("Weight for", WEIGHT_SUBJECT_DATE, logged_dates(WEIGHT_FILE), handle_weight_reply),
    ]
    since = (datetime.now(TZ) - timedelta(days=7)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(ADDRESS, app_password())
    imap.select("INBOX", readonly=True)
    for prefix, subject_re, done, handle in prompts:
        _, data = imap.search(None, f'(SUBJECT "{prefix}" SINCE {since})')
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)
            subject = msg["Subject"] or ""
            if not subject.lower().startswith("re:"):
                continue  # the prompt itself, not a reply
            m = subject_re.search(subject)
            if not m or m.group(1) in done:
                continue
            if handle(m.group(1), plain_text(msg)):
                done.add(m.group(1))
    imap.logout()


def send_daily_prompt():
    today = datetime.now(TZ).date().isoformat()
    send(
        f"Calories for {today}?",
        "How many calories did you eat today? Reply with just the number — "
        "optionally add macros like: 2342, 150p 60f 220c",
    )
    print(f"Sent calorie prompt for {today}.")


def send_weight_prompt():
    today = datetime.now(TZ).date().isoformat()
    send(
        f"Weight for {today}?",
        "Sunday weigh-in: reply with just the number (same unit every week).",
    )
    print(f"Sent weight prompt for {today}.")


def selftest():
    assert extract_calories("2200") == (2200, None, None, None)
    assert extract_calories("2,200\n\nOn Sun, Jul 12, 2026 at 9:00 PM Coach wrote:\n> reply with") == (2200, None, None, None)
    assert extract_calories("around 1850 I think\n> Calories for 2026-07-12?") == (1850, None, None, None)
    assert extract_calories("On Sun, Jul 12, 2026\n> quoted") is None  # wrapped quote header only
    assert extract_calories("12") is None  # too small to be a day's calories
    assert extract_calories("2342, 150p 60f 220c") == (2342, 150, 60, 220)
    assert extract_calories("2342 220C 150P") == (2342, 150, None, 220)  # any order/case, fat missing
    assert extract_calories("150p 60f 220c") is None  # macros but no calorie total
    assert extract_weight("183.2") == 183.2
    assert extract_weight("83 kg this morning") == 83
    assert extract_weight("1050") is None  # implausible
    assert extract_weight("On Sun\n> Weight for 2026-07-19?") is None
    assert SUBJECT_DATE.search("Re: Calories for 2026-07-12?").group(1) == "2026-07-12"
    assert WEIGHT_SUBJECT_DATE.search("Re: Weight for 2026-07-19?").group(1) == "2026-07-19"
    print("selftest ok")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "--selftest":
        selftest()
    elif cmd == "daily":
        log_replies()
        send_daily_prompt()
    elif cmd == "weight-prompt":
        send_weight_prompt()
    elif cmd == "send":
        args = sys.argv[2:]
        attachment = None
        if "--attach" in args:
            i = args.index("--attach")
            attachment = args[i + 1]
            del args[i : i + 2]
        send(args[0], sys.stdin.read(), attachment)
        print(f"Sent: {args[0]}" + (f" (+ {attachment})" if attachment else ""))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
