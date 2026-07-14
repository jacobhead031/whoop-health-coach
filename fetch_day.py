"""Pull one day of WHOOP data (strain, recovery, sleep) and print a summary.

Usage: python fetch_day.py [YYYY-MM-DD]   (defaults to yesterday)
"""

import sys
from datetime import date, datetime, time, timedelta, timezone

from whoop_client import get_collection


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def state_tag(record):
    st = record.get("score_state")
    return "" if st == "SCORED" else f"  [{st}]"


def main():
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today() - timedelta(days=1)
    # Local-midnight boundaries, so "a day" matches the calendar day as lived.
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time.min, tzinfo=local_tz)
    end = start + timedelta(days=1)

    cycles = get_collection("/cycle", iso(start), iso(end))
    recoveries = get_collection("/recovery", iso(start), iso(end))
    sleeps = get_collection("/activity/sleep", iso(start), iso(end))

    print(f"=== WHOOP {day} ===")
    if not (cycles or recoveries or sleeps):
        print("No records in this window.")
        return

    for c in cycles:
        s = c.get("score") or {}
        kcal = f"{s['kilojoule'] / 4.184:.0f} kcal burned" if "kilojoule" in s else "kcal n/a"
        print(
            f"Strain:   {s.get('strain', 'n/a'):>5}   "
            f"avg HR {s.get('average_heart_rate', 'n/a')} bpm   {kcal}{state_tag(c)}"
        )
    for r in recoveries:
        s = r.get("score") or {}
        print(
            f"Recovery: {s.get('recovery_score', 'n/a'):>4}%   "
            f"HRV {s.get('hrv_rmssd_milli', 'n/a')} ms   "
            f"RHR {s.get('resting_heart_rate', 'n/a')} bpm{state_tag(r)}"
        )
    for sl in sleeps:
        if sl.get("nap"):
            continue
        s = sl.get("score") or {}
        stages = s.get("stage_summary") or {}
        asleep_ms = sum(
            stages.get(k, 0)
            for k in (
                "total_light_sleep_time_milli",
                "total_slow_wave_sleep_time_milli",
                "total_rem_sleep_time_milli",
            )
        )
        print(
            f"Sleep:    {asleep_ms / 3_600_000:.1f} h asleep   "
            f"performance {s.get('sleep_performance_percentage', 'n/a')}%{state_tag(sl)}"
        )


if __name__ == "__main__":
    main()
