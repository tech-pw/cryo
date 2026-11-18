import { describe, expect, it } from "vitest";
import type { FleetHealthTrendPoint } from "../../api/types";
import type { ImpactDay } from "../../lib/impactHistory";
import { linksThroughNode, shareOf, truncateLabel } from "../../components/charts/FlowSankey";
import { firstUnseen, lakeScore, milestones, nextBestAction, resolveBaseline, spanLabel } from "./briefing";
import { buildCalendar, isFailedNight, measuredShare, quartileThresholds, weekOverWeek, workStreak, workTotals } from "./calendarModel";
import { decadeTicks } from "./fixFirst";
import { cleanupProgress, storageTrendDelta } from "./storageStory";
import { storageFixture } from "./storageFixture";

const HOUR = 3_600_000;
const NOW = Date.parse("2026-09-28T04:30:00Z");
const TB = 1e12;

const day = (report_date: string, overrides: Partial<ImpactDay> = {}): ImpactDay => ({
  report_date,
  measurement_state: "complete",
  bytes_freed: TB,
  actions_succeeded: 10,
  actions_with_bytes_evidence: 10,
  jobs_succeeded: 10,
  jobs_failed: 0,
  tables_touched: 5,
  optimize_files_in: 100,
  optimize_files_out: 10,
  ...overrides
}) as ImpactDay;

describe("visit baseline", () => {
  it("uses a week for a first visit, a day for a recent one and caps old visits at 30 days", () => {
    expect(resolveBaseline(NOW, null, null)).toEqual({ since: NOW - 7 * 24 * HOUR, kind: "first" });
    expect(resolveBaseline(NOW, new Date(NOW - 10 * 60_000).toISOString(), null)).toEqual({ since: NOW - 24 * HOUR, kind: "recent" });
    expect(resolveBaseline(NOW, new Date(NOW - 90 * 24 * HOUR).toISOString(), null)).toEqual({ since: NOW - 30 * 24 * HOUR, kind: "visit" });
  });

  it("keeps the session baseline across reloads and ignores broken values", () => {
    const session = JSON.stringify({ since: NOW - 5 * HOUR, kind: "visit" });
    expect(resolveBaseline(NOW, null, session)).toEqual({ since: NOW - 5 * HOUR, kind: "visit" });
    expect(resolveBaseline(NOW, null, "{not json").kind).toBe("first");
  });
});

describe("next best action", () => {
  it("puts urgent work first and falls back to all clear", () => {
    expect(nextBestAction({ approvals: 2, failed: 3, hotAtRisk: 4 }).id).toBe("approvals");
    expect(nextBestAction({ failed: 1, runHref: "/runs/7" })).toMatchObject({ id: "failed", href: "/runs/7", title: "1 table failed last night" });
    expect(nextBestAction({ hotAtRisk: 0, needsCompaction: 0 }).id).toBe("clear");
  });
});

describe("milestones", () => {
  it("reports the biggest freed step once and skips what was already seen", () => {
    const all = milestones({ windowFreedBytes: 12 * TB, gradeF: 0, graded: 10 });
    expect(all.map((m) => m.id)).toEqual(["freed-10 TB", "zero-f"]);
    expect(firstUnseen(all, ["freed-10 TB"])?.id).toBe("zero-f");
    expect(firstUnseen(all, ["freed-10 TB", "zero-f"])).toBeNull();
  });
});

describe("work calendar", () => {
  it("lays out Monday-first weeks ending this week, with future days marked", () => {
    const weeks = buildCalendar([day("2026-09-27")], "2026-09-28", 2);
    expect(weeks).toHaveLength(2);
    expect(weeks[1].start).toBe("2026-09-28");
    expect(weeks[1].cells[0].state).toBe("missing");
    expect(weeks[1].cells[1].state).toBe("future");
    expect(weeks[0].cells[6]).toMatchObject({ date: "2026-09-27", state: "measured", level: 4 });
  });

  it("splits quartiles over positive values only", () => {
    expect(quartileThresholds([0, 0])).toEqual([Infinity, Infinity, Infinity]);
    expect(quartileThresholds([1, 2, 3, 4])).toEqual([2, 3, 4]);
  });

  it("counts streaks through quiet days and resets on failures", () => {
    const streak = workStreak([
      day("2026-09-20"),
      day("2026-09-21"),
      day("2026-09-22", { measurement_state: "no_actions", actions_succeeded: 0 }),
      day("2026-09-23"),
      day("2026-09-24", { jobs_failed: 1 }),
      day("2026-09-25")
    ]);
    expect(streak).toEqual({ current: 1, best: 3, brokeRecently: false });
  });

  it("keeps the streak through a handful of failures at fleet scale", () => {
    const night = (date: string, failed: number) => day(date, { jobs_succeeded: 2_000 - failed, jobs_failed: failed });
    expect(isFailedNight(night("2026-09-20", 5))).toBe(false);
    expect(isFailedNight(night("2026-09-20", 20))).toBe(true);
    expect(workStreak([night("2026-09-20", 3), night("2026-09-21", 12), night("2026-09-22", 0)])).toEqual({ current: 3, best: 3, brokeRecently: false });
    expect(workStreak([night("2026-09-20", 3), night("2026-09-21", 40)])).toEqual({ current: 0, best: 1, brokeRecently: true });
  });

  it("breaks the streak when a small fleet fails one job in ten", () => {
    expect(workStreak([day("2026-09-20", { jobs_succeeded: 9, jobs_failed: 1 })]).brokeRecently).toBe(true);
  });

  it("totals measured bytes and compares the last two weeks", () => {
    const days = [day("2026-09-27", { bytes_freed: 3 * TB }), day("2026-09-18", { bytes_freed: 2 * TB }), day("2026-09-26", { measurement_state: "unmeasured", actions_with_bytes_evidence: 0 })];
    expect(workTotals(days)).toMatchObject({ bytes: 5 * TB, measuredDays: 2, activeDays: 3, partial: true, filesCompacted: 270 });
    expect(weekOverWeek(days.slice(0, 2), "2026-09-28")).toEqual({ kind: "change", pct: 50 });
    expect(weekOverWeek(days, "2026-09-28")).toMatchObject({ kind: "coverage_changed", recentShare: 0.5, priorShare: 1 });
    expect(weekOverWeek([day("2026-09-27")], "2026-09-28")).toBeNull();
  });

  it("does not compare weeks that measured very different shares of actions", () => {
    const week = (start: number, measured: number, bytes: number) =>
      Array.from({ length: 7 }, (_, i) => day(`2026-09-${String(start + i).padStart(2, "0")}`, { measurement_state: "partial", actions_succeeded: 1_000, actions_with_bytes_evidence: measured, bytes_freed: bytes }));
    const jump = [...week(14, 5, TB), ...week(21, 300, 60 * TB)];
    expect(measuredShare(week(14, 5, TB))).toBeCloseTo(0.005);
    expect(weekOverWeek(jump, "2026-09-27")).toEqual({ kind: "coverage_changed", recentShare: 0.3, priorShare: 0.005 });
    const steady = [...week(14, 280, 10 * TB), ...week(21, 300, 12 * TB)];
    expect(weekOverWeek(steady, "2026-09-27")).toEqual({ kind: "change", pct: 20 });
  });
});

describe("lake score", () => {
  const point = (date: string, score: number) => ({ day: date, avg_health_score: score }) as FleetHealthTrendPoint;

  it("hides the weekly change until a point at least 7 days old exists", () => {
    const short = lakeScore([point("2026-09-25", 60), point("2026-09-28", 70)]);
    expect(short).toMatchObject({ score: 70, weekDelta: null, deltaDays: null });
  });

  it("compares against the newest point a week or more back and reports the real span", () => {
    expect(lakeScore([point("2026-09-20", 50), point("2026-09-21", 55), point("2026-09-25", 60), point("2026-09-28", 62)])).toMatchObject({ weekDelta: 7, deltaDays: 7 });
    expect(lakeScore([point("2026-09-18", 50), point("2026-09-26", 58), point("2026-09-28", 60)])).toMatchObject({ weekDelta: 10, deltaDays: 10 });
    expect(lakeScore([point("2026-09-21", 60), point("2026-09-28", 60)])).toMatchObject({ weekDelta: 0, deltaDays: 7 });
    expect(spanLabel(7)).toBe("this week");
    expect(spanLabel(10)).toBe("in 10 days");
  });
});

describe("fix-first axes", () => {
  it("adds 2x and 5x ticks when the data spans less than a few decades", () => {
    expect(decadeTicks(1, 4)).toEqual([10, 100, 1000, 10000]);
    expect(decadeTicks(1.2, 1.8)).toEqual([10, 20, 50, 100, 200, 500]);
  });
});

describe("flow sankey helpers", () => {
  it("lights every path through a node", () => {
    const links = [{ source: 0, target: 1 }, { source: 1, target: 2 }, { source: 1, target: 3 }, { source: 0, target: 4 }];
    expect([...linksThroughNode(links, 2)].sort()).toEqual([0, 1]);
    expect([...linksThroughNode(links, 1)].sort()).toEqual([0, 1, 2]);
  });

  it("truncates labels and formats shares", () => {
    expect(truncateLabel("Compacted overnight", 10)).toBe("Compacted…");
    expect(truncateLabel("Short", 10)).toBe("Short");
    expect(shareOf(1, 1000)).toBe("<1%");
    expect(shareOf(0, 0)).toBe("0%");
    expect(shareOf(1, 3)).toBe("33%");
  });
});

describe("storage story", () => {
  it("compares reclaimed bytes with what is still freeable", () => {
    const b = storageFixture().breakdown;
    expect(cleanupProgress(41 * TB, b)).toEqual({ reclaimed: 41 * TB, remaining: 23 * TB, pct: 64 });
    expect(cleanupProgress(null, { ...b, dead_bytes: 0, orphan_bytes: 0, compaction_savings_bytes: 0 }).pct).toBeNull();
  });

  it("measures the trend change between the first and last recorded day", () => {
    expect(storageTrendDelta(storageFixture().trend)).toMatchObject({ days: 59 });
    expect(storageTrendDelta([])).toBeNull();
  });
});
