import { afterEach, describe, expect, it } from "vitest";
import type { FleetHealthTrendPoint, RunRead } from "../../api/types";
import { setDisplayTimeZone } from "../../lib/format";
import { dfDelta, dfSeries, formatAgo, nightOutcome, offPeakState } from "./homeSignals";

const NOW = Date.parse("2026-10-18T06:00:00Z");
const point = (day: string, df: number, extra: Partial<FleetHealthTrendPoint> = {}): FleetHealthTrendPoint =>
  ({ day, avg_health_score: 70, tables_at_grade_df: df, tables_counted: 100, ...extra });
const run = (overrides: Partial<RunRead>): RunRead =>
  ({ id: "r1", kind: "maintenance", status: "succeeded", created_at: "2026-10-18T01:00:00Z", finished_at: "2026-10-18T03:00:00Z", summary: {}, ...overrides }) as RunRead;

describe("home signals", () => {
  afterEach(() => {
    setDisplayTimeZone(undefined);
  });
  it("compares D/F against the latest complete day before today", () => {
    const trend = [point("2026-10-16", 9), point("2026-10-17", 8), point("2026-10-18", 4, { is_partial: true })];
    expect(dfDelta(trend, 6, 100, NOW)).toBe(-2);
    expect(dfDelta(trend, 8, 100, NOW)).toBeNull();
    expect(dfDelta(null, 6, 100, NOW)).toBeNull();
  });
  it("skips a prior day whose scan coverage was very different", () => {
    const trend = [point("2026-10-16", 9), point("2026-10-17", 1, { tables_counted: 20 })];
    expect(dfDelta(trend, 6, 100, NOW)).toBe(-3);
  });
  it("only draws a sparkline that says something", () => {
    expect(dfSeries([point("2026-10-17", 5), point("2026-10-16", 7)])).toEqual([7, 5]);
    expect(dfSeries([point("2026-10-16", 5), point("2026-10-17", 5)])).toEqual([]);
    expect(dfSeries([point("2026-10-16", 5)])).toEqual([]);
  });
  it("labels the off-peak window in the install's zone, including windows that cross midnight", () => {
    setDisplayTimeZone("Asia/Kolkata");
    expect(offPeakState({ start_hour_ist: 1, end_hour_ist: 7, enabled: true }, Date.parse("2026-10-17T21:00:00Z"))).toEqual({ label: "01:00–07:00 Kolkata", open: true });
    expect(offPeakState({ start_hour_ist: 22, end_hour_ist: 6, enabled: true }, Date.parse("2026-10-17T18:00:00Z"))?.open).toBe(true);
    expect(offPeakState({ start_hour_ist: 22, end_hour_ist: 6, enabled: true }, Date.parse("2026-10-18T06:00:00Z"))?.open).toBe(false);
    expect(offPeakState({ start_hour_ist: 1, end_hour_ist: 7, enabled: false })).toBeNull();
  });
  it("reads the off-peak window in the install's zone, not IST (#183)", () => {
    // 21:00 UTC is 02:30 in Kolkata (inside a 01:00-07:00 window) and 23:00 in Berlin
    // (outside it). The governor reads the window in the install's zone, so the UI must
    // agree with the governor rather than with IST.
    setDisplayTimeZone("Asia/Kolkata");
    expect(offPeakState({ start_hour_ist: 1, end_hour_ist: 7, enabled: true }, Date.parse("2026-10-17T21:00:00Z"))?.open).toBe(true);
    setDisplayTimeZone("Europe/Berlin");
    expect(offPeakState({ start_hour_ist: 1, end_hour_ist: 7, enabled: true }, Date.parse("2026-10-17T21:00:00Z"))?.open).toBe(false);
  });
  it("classifies each night by its worst outcome", () => {
    expect(nightOutcome(run({ finished_at: null, status: "running" }))).toBe("running");
    expect(nightOutcome(run({ summary: { failed: 1 } }))).toBe("failed");
    expect(nightOutcome(run({ summary: { partial: 2 } }))).toBe("partial");
    expect(nightOutcome(run({}))).toBe("succeeded");
  });
  it("formats ages compactly", () => {
    expect(formatAgo("2026-10-18T05:59:59Z", NOW)).toBe("just now");
    expect(formatAgo("2026-10-18T05:48:00Z", NOW)).toBe("12m ago");
    expect(formatAgo("2026-10-17T06:00:00Z", NOW)).toBe("24h ago");
    expect(formatAgo("2026-10-14T06:00:00Z", NOW)).toBe("4d ago");
    expect(formatAgo(null, NOW)).toBeNull();
  });
});
