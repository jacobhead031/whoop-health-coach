"""Weekly readout: last 7 days of WHOOP data + logged calories, ending in 2-3 adjustments.

Usage: python weekly_coach.py

Day assignment (the cycle quirk): a WHOOP cycle is a physiological day running
sleep-onset to sleep-onset (verified against live data — starts cluster around
local bedtime), so cycles straddle calendar days and a naive per-day query
double-counts. Each record is assigned to exactly one calendar day:
  - cycle (strain, kcal burned) -> local date 12 h after it started, i.e. the
    day you woke up into. Raw start dates are unstable: falling asleep at 23:51
    vs 00:20 would land on different days.
  - sleep                       -> local date the sleep ENDED ("last night's sleep")
  - recovery                    -> the day of its cycle (it scores that cycle's night)
So for a given day D: sleep is the night into D, recovery is measured that
morning, and strain accumulates through D. Calories eaten on D pair with that.

Coaching logic: ADJUSTMENT_RULES near the bottom is a plain list of threshold
checks, evaluated top to bottom; the first 3 that fire are printed.
"""

import csv
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from whoop_client import get_collection

CALORIES_FILE = Path(__file__).resolve().parent / "calories.csv"
WEIGHT_FILE = Path(__file__).resolve().parent / "weight.csv"

# Tunable thresholds — the whole personality of the coach lives here.
HARD_DAY_STRAIN = 14.0     # a strain at/above this counts as a hard training day
SHORT_SLEEP_H = 7.0        # a night under this counts as short sleep
LOW_RECOVERY = 50.0        # a morning recovery below this counts as "red"
FUEL_GAP_KCAL = 300        # eaten-vs-burned gap per day worth acting on
TREND_SLEEP_H = 0.4        # half-week sleep change smaller than this is "steady"
TREND_RECOVERY = 6.0       # half-week recovery change smaller than this is "steady"


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_date(ts, shift_hours=0):
    """Local calendar date of an ISO-8601 timestamp like 2026-07-07T04:12:00.000Z."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return (dt + timedelta(hours=shift_hours)).date()


def mean(xs):
    return sum(xs) / len(xs)


def read_calories(day_set):
    """({date: calories}, {date: protein_g}) for the requested days; the last
    entry per date wins. Protein is absent for rows logged without macros."""
    eaten, protein = {}, {}
    if not CALORIES_FILE.exists():
        return eaten, protein
    with CALORIES_FILE.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["date"])
            except (ValueError, TypeError):
                continue
            if d in day_set:
                eaten[d] = int(row["calories"])
                if row.get("protein"):
                    protein[d] = int(row["protein"])
                else:
                    protein.pop(d, None)  # re-log without macros wipes the old value
    return eaten, protein


def read_weight(last_n=4):
    """Most recent weigh-ins as [(date, weight)], oldest first; last entry per date wins."""
    if not WEIGHT_FILE.exists():
        return []
    entries = {}
    with WEIGHT_FILE.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                entries[date.fromisoformat(row["date"])] = float(row["weight"])
            except (ValueError, TypeError):
                continue
    return sorted(entries.items())[-last_n:]


def fetch_week(days):
    """Per-day dicts for the given days: sleep hours, recovery %, strain, kcal burned."""
    day_set = set(days)
    local_tz = datetime.now().astimezone().tzinfo
    # One day of lead-in so the sleep that *ends* on days[0] (it started the
    # evening before) and its cycle are inside the query window.
    start = datetime.combine(days[0] - timedelta(days=1), time.min, tzinfo=local_tz)
    end = datetime.now(local_tz)

    cycles = get_collection("/cycle", iso(start), iso(end))
    recoveries = get_collection("/recovery", iso(start), iso(end))
    sleeps = get_collection("/activity/sleep", iso(start), iso(end))

    strain, burned, cycle_day = {}, {}, {}
    for c in cycles:
        # Cycles start at sleep onset, right around midnight — shift 12 h so the
        # cycle lands on the day it was lived (the day you woke up into).
        d = local_date(c["start"], shift_hours=12)
        cycle_day[c["id"]] = d  # kept for all fetched cycles so recoveries can be placed
        if d not in day_set:
            continue
        s = c.get("score") or {}  # score is absent unless score_state == SCORED
        if "strain" in s:
            strain[d] = s["strain"]
        if "kilojoule" in s:
            burned[d] = s["kilojoule"] / 4.184

    recovery = {}
    for r in recoveries:
        d = cycle_day.get(r.get("cycle_id"))
        if d not in day_set:
            continue
        s = r.get("score") or {}
        if "recovery_score" in s:
            recovery[d] = s["recovery_score"]

    sleep_h = {}
    for sl in sleeps:
        if sl.get("nap"):
            continue
        d = local_date(sl["end"])
        if d not in day_set:
            continue
        stages = (sl.get("score") or {}).get("stage_summary") or {}
        asleep_ms = sum(
            stages.get(k, 0)
            for k in (
                "total_light_sleep_time_milli",
                "total_slow_wave_sleep_time_milli",
                "total_rem_sleep_time_milli",
            )
        )
        if asleep_ms:  # if a night somehow yields two sleep records, keep the longer
            sleep_h[d] = max(sleep_h.get(d, 0), asleep_ms / 3_600_000)

    eaten, protein = read_calories(day_set)
    return {
        "days": days,
        "sleep": sleep_h,
        "recovery": recovery,
        "strain": strain,
        "burned": burned,
        "eaten": eaten,
        "protein": protein,
        "weight": read_weight(),
    }


def half_week_trend(values, days, flat_band):
    """Compare first-half vs second-half averages. Returns (label, first, second) or None."""
    half = len(days) // 2
    first = [values[d] for d in days[:half] if d in values]
    second = [values[d] for d in days[half:] if d in values]
    if not first or not second:
        return None
    a, b = mean(first), mean(second)
    label = "improving" if b - a > flat_band else "declining" if a - b > flat_band else "steady"
    return label, a, b


def fuel_gaps(wk, only_hard=False):
    """Per-day (eaten - burned) for days where both are known; optionally hard days only."""
    return [
        wk["eaten"][d] - wk["burned"][d]
        for d in wk["days"]
        if d in wk["eaten"] and d in wk["burned"]
        and (not only_hard or wk["strain"].get(d, 0) >= HARD_DAY_STRAIN)
    ]


# --- Coaching rules ---------------------------------------------------------
# Each rule looks at the week and returns a message string, or None if it
# doesn't apply. Ordered by priority; the first 3 that fire are printed.


def rule_recovery_drops_after_short_sleep(wk):
    short = [wk["recovery"][d] for d in wk["days"]
             if d in wk["recovery"] and wk["sleep"].get(d, 99) < SHORT_SLEEP_H]
    rested = [wk["recovery"][d] for d in wk["days"]
              if d in wk["recovery"] and wk["sleep"].get(d, 0) >= SHORT_SLEEP_H]
    if len(short) >= 2 and len(rested) >= 2 and mean(rested) - mean(short) >= 8:
        return (
            f"Protect sleep: you recovered {mean(short):.0f}% after nights under "
            f"{SHORT_SLEEP_H:.0f} h vs {mean(rested):.0f}% after longer nights. "
            f"Set a bedtime that gives you 7.5 h+, especially before planned hard days."
        )


def rule_underfueled_hard_days(wk):
    gaps = fuel_gaps(wk, only_hard=True)
    if gaps and mean(gaps) <= -FUEL_GAP_KCAL:
        add = round(-mean(gaps) / 100) * 100
        return (
            f"Fuel your training: on hard days (strain >= {HARD_DAY_STRAIN:.0f}) you ate "
            f"{-mean(gaps):.0f} kcal less than you burned. Add ~{add} kcal on those days, "
            f"ideally around the workout."
        )


def rule_recovery_trending_down(wk):
    t = half_week_trend(wk["recovery"], wk["days"], TREND_RECOVERY)
    if t and t[0] == "declining":
        return (
            f"Back off early next week: recovery slid from {t[1]:.0f}% to {t[2]:.0f}% "
            f"across the week. Keep strain under 10 until you wake up above 60% again."
        )


def rule_chronic_short_sleep(wk):
    hours = [wk["sleep"][d] for d in wk["days"] if d in wk["sleep"]]
    if len(hours) >= 4 and mean(hours) < SHORT_SLEEP_H:
        shift = round((7.5 - mean(hours)) * 60 / 15) * 15
        return (
            f"You averaged {mean(hours):.1f} h of sleep. Move bedtime ~{shift} min earlier "
            f"to get to 7.5 h; keep it for a week before judging."
        )


def rule_hard_day_then_red_recovery(wk):
    crashes = [
        d for d in wk["days"][:-1]
        if wk["strain"].get(d, 0) >= HARD_DAY_STRAIN
        and wk["recovery"].get(d + timedelta(days=1), 100) < LOW_RECOVERY
    ]
    if crashes:
        when = ", ".join(d.strftime("%a") for d in crashes)
        return (
            f"Hard days knocked you into the red the next morning ({when}). "
            f"Follow strain >= {HARD_DAY_STRAIN:.0f} days with a deliberate easy day next week."
        )


def rule_big_weekly_deficit(wk):
    gaps = fuel_gaps(wk)
    if len(gaps) >= 4 and mean(gaps) <= -500:
        return (
            f"You ran a {-mean(gaps):.0f} kcal/day average deficit. Unless cutting is the "
            f"goal, raise daily intake by ~300 kcal and re-check next week."
        )


def rule_sparse_calorie_logs(wk):
    logged = len(wk["eaten"])
    if logged < 5:
        return (
            f"Log calories every day ({logged}/7 days logged this week) — the fuel-vs-strain "
            f"analysis needs the full week: python log_calories.py 2200"
        )


def rule_erratic_sleep_schedule(wk):
    hours = [wk["sleep"][d] for d in wk["days"] if d in wk["sleep"]]
    if len(hours) >= 4 and max(hours) - min(hours) >= 2:
        return (
            f"Your sleep swung from {min(hours):.1f} h to {max(hours):.1f} h. Aim for a "
            f"consistent window next week — variability costs recovery even when the average is fine."
        )


def rule_keep_it_up(wk):
    rec = [wk["recovery"][d] for d in wk["days"] if d in wk["recovery"]]
    hours = [wk["sleep"][d] for d in wk["days"] if d in wk["sleep"]]
    if rec and hours:
        return (
            f"No red flags — repeat the formula: ~{mean(hours):.1f} h sleep and this week's "
            f"training load kept you at {mean(rec):.0f}% average recovery."
        )


ADJUSTMENT_RULES = [
    rule_recovery_drops_after_short_sleep,
    rule_underfueled_hard_days,
    rule_recovery_trending_down,
    rule_chronic_short_sleep,
    rule_hard_day_then_red_recovery,
    rule_big_weekly_deficit,
    rule_sparse_calorie_logs,
    rule_erratic_sleep_schedule,
    rule_keep_it_up,  # catch-all so the readout always ends with something actionable
]


# --- Readout ----------------------------------------------------------------


def fmt(value, spec, width):
    return (spec.format(value) if value is not None else "--").rjust(width)


def print_readout(wk):
    days = wk["days"]
    print(f"=== Weekly readout: {days[0]} -> {days[-1]} ===\n")

    print("Date        Sleep  Recov  Strain   Eaten  Burned")
    for d in days:
        print(
            d.strftime("%a %m-%d")
            + fmt(wk["sleep"].get(d), "{:.1f}h", 9)
            + fmt(wk["recovery"].get(d), "{:.0f}%", 7)
            + fmt(wk["strain"].get(d), "{:.1f}", 8)
            + fmt(wk["eaten"].get(d), "{:d}", 8)
            + fmt(wk["burned"].get(d), "{:.0f}", 8)
        )
    print()

    hours = [wk["sleep"][d] for d in days if d in wk["sleep"]]
    if hours:
        t = half_week_trend(wk["sleep"], days, TREND_SLEEP_H)
        trend_txt = f", {t[0]} ({t[1]:.1f} h -> {t[2]:.1f} h)" if t else ""
        print(f"Sleep:    avg {mean(hours):.1f} h{trend_txt}")

    rec = [wk["recovery"][d] for d in days if d in wk["recovery"]]
    if rec:
        t = half_week_trend(wk["recovery"], days, TREND_RECOVERY)
        trend_txt = f", {t[0]} ({t[1]:.0f}% -> {t[2]:.0f}%)" if t else ""
        print(f"Recovery: avg {mean(rec):.0f}%{trend_txt}")

    strains = [wk["strain"][d] for d in days if d in wk["strain"]]
    if strains:
        hard = sum(1 for s in strains if s >= HARD_DAY_STRAIN)
        print(f"Strain:   avg {mean(strains):.1f}  ({hard} hard day{'s' if hard != 1 else ''} >= {HARD_DAY_STRAIN:.0f})")

    prot = [wk["protein"][d] for d in days if d in wk["protein"]]
    if prot:
        line = f"Protein:  avg {mean(prot):.0f} g/day ({len(prot)}/7 days logged)"
        if wk["weight"]:
            w = wk["weight"][-1][1]
            kg = w / 2.205 if w > 120 else w  # ponytail: >120 assumed lbs, else kg
            line += f", {mean(prot) / kg:.1f} g/kg"
        print(line)

    if wk["weight"]:
        trail = " -> ".join(f"{w:g}" for _, w in wk["weight"])
        print(f"Weight:   {wk['weight'][-1][1]:g} (last {len(wk['weight'])}: {trail})")

    gaps = fuel_gaps(wk)
    if gaps:
        word = "deficit" if mean(gaps) < 0 else "surplus"
        line = f"Fuel:     avg {abs(mean(gaps)):.0f} kcal/day {word} ({len(gaps)} days with both logged)"
        hard_gaps = fuel_gaps(wk, only_hard=True)
        if hard_gaps:
            hard_word = "deficit" if mean(hard_gaps) < 0 else "surplus"
            line += f"; hard days averaged a {abs(mean(hard_gaps)):.0f} kcal {hard_word}"
        print(line)
    else:
        print("Fuel:     no days with both calories logged and WHOOP burn data")

    print("\n--- Adjustments for next week ---")
    fired = [msg for rule in ADJUSTMENT_RULES if (msg := rule(wk))][:3]
    for i, msg in enumerate(fired, 1):
        print(f"{i}. {msg}")


def main():
    today = date.today()
    days = [today - timedelta(days=n) for n in range(7, 0, -1)]  # last 7 full days
    wk = fetch_week(days)
    if not (wk["sleep"] or wk["recovery"] or wk["strain"]):
        raise SystemExit("No WHOOP data found for the last 7 days.")
    print_readout(wk)


if __name__ == "__main__":
    main()
