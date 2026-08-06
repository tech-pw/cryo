#!/usr/bin/env python3
"""Month-over-month cost comparison from daily-cost-log.csv.

Compares the current month's day-to-date average daily cost against the same
day-range in the previous month (not full-month totals -- an incomplete month
compared to a complete one is not a fair comparison), and reports a Welch's
t-test so "the number moved" and "the number moved more than day-to-day noise
would explain" don't get conflated. See README.md in this directory for the
full methodology and how this log gets populated.

Usage:
    python3 compare_mom.py trino-db
    python3 compare_mom.py cryo
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

LOG_PATH = Path(__file__).parent / "daily-cost-log.csv"

# Below this many days of current-month data, don't claim significance --
# the t-test is mathematically valid but underpowered; report the trend
# as "early signal" instead. See README.md for how this threshold was chosen.
MIN_DAYS_FOR_SIGNIFICANCE = 7


def welch_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    """Returns (t_statistic, degrees_of_freedom). No scipy dependency."""
    m1, m2 = statistics.mean(a), statistics.mean(b)
    s1, s2 = statistics.stdev(a), statistics.stdev(b)
    n1, n2 = len(a), len(b)
    se = math.sqrt(s1**2 / n1 + s2**2 / n2)
    t = (m1 - m2) / se if se > 0 else float("inf")
    df = (s1**2 / n1 + s2**2 / n2) ** 2 / (
        (s1**2 / n1) ** 2 / (n1 - 1) + (s2**2 / n2) ** 2 / (n2 - 1)
    )
    return t, df


def load_series(namespace: str) -> dict[date, float]:
    if not LOG_PATH.exists():
        raise SystemExit(f"No log file at {LOG_PATH} yet -- nothing to compare.")
    by_date: dict[date, float] = {}
    with open(LOG_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row["namespace"] != namespace:
                continue
            # Skip aggregate/non-daily rows (e.g. "2026-08-06_30d" markers).
            try:
                d = datetime.strptime(row["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            by_date[d] = float(row["daily_cost_usd"])
    return by_date


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <namespace>")
    namespace = sys.argv[1]
    series = load_series(namespace)
    if not series:
        raise SystemExit(f"No rows found for namespace={namespace!r} in {LOG_PATH}")

    today = max(series)
    cur_month = today.month
    cur_year = today.year
    cur_days = sorted(d for d in series if d.year == cur_year and d.month == cur_month)
    if not cur_days:
        raise SystemExit("No data for the current month yet.")
    day_of_month_range = {d.day for d in cur_days}
    max_day = max(day_of_month_range)

    # Same day-range in the previous month.
    prev_month = cur_month - 1 or 12
    prev_year = cur_year if cur_month > 1 else cur_year - 1
    prev_days = sorted(
        d
        for d in series
        if d.year == prev_year and d.month == prev_month and d.day <= max_day
    )

    cur_costs = [series[d] for d in cur_days]
    prev_costs = [series[d] for d in prev_days]

    print(f"Namespace: {namespace}")
    print(f"Current month ({cur_year}-{cur_month:02d}), days 1-{max_day}: n={len(cur_costs)}")
    print(f"  mean=${statistics.mean(cur_costs):.2f}  total=${sum(cur_costs):.2f}")
    if len(prev_costs) < 2:
        print("Not enough prior-month same-range data to compare yet.")
        return
    print(f"Prior month ({prev_year}-{prev_month:02d}), days 1-{max_day}: n={len(prev_costs)}")
    print(f"  mean=${statistics.mean(prev_costs):.2f}  total=${sum(prev_costs):.2f}")

    pct = (statistics.mean(cur_costs) - statistics.mean(prev_costs)) / statistics.mean(prev_costs) * 100
    print(f"\nChange: {pct:+.1f}%")

    if len(cur_costs) >= 2 and len(prev_costs) >= 2:
        t, df = welch_t_test(prev_costs, cur_costs)
        print(f"Welch's t = {t:.2f}, df = {df:.1f}")
        if len(cur_costs) < MIN_DAYS_FOR_SIGNIFICANCE:
            print(
                f"Only {len(cur_costs)} day(s) of current-month data "
                f"(want >= {MIN_DAYS_FOR_SIGNIFICANCE}) -- treat as an early trend, "
                "not a confirmed result, regardless of what the t-stat says."
            )
        else:
            # |t| > ~2 is roughly the 95% threshold for df >= 5; not exact without
            # a real t-distribution CDF, but close enough for a go/no-go read.
            verdict = "likely significant" if abs(t) > 2.2 else "not significant"
            print(f"Verdict: {verdict} (rough threshold, not an exact p-value)")


if __name__ == "__main__":
    main()
