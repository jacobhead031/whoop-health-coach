"""Append a day's calorie intake (and optional macros) to calories.csv.

Usage: python log_calories.py CALORIES [YYYY-MM-DD] [150p 60f 220c]
       (date defaults to today; macro tokens in any order)

Append-only on purpose: to correct a day, just log it again — readers take the
last entry per date.
"""

import sys
from datetime import date

from coach_email import MACRO, log_calories


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    try:
        calories = int(sys.argv[1])
    except ValueError:
        raise SystemExit(f"CALORIES must be a whole number, got {sys.argv[1]!r}")

    day, macros = date.today(), {}
    for arg in sys.argv[2:]:
        m = MACRO.fullmatch(arg)
        if m:
            macros[m.group(2).lower()] = int(m.group(1))
        else:
            day = date.fromisoformat(arg)

    log_calories(day.isoformat(), calories, macros.get("p"), macros.get("f"), macros.get("c"))
    macro_txt = " " + " ".join(f"{v}{k}" for k, v in macros.items()) if macros else ""
    print(f"Logged {calories} kcal{macro_txt} for {day}.")


if __name__ == "__main__":
    main()
