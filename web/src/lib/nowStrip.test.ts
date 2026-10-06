import { describe, expect, it } from "vitest";
import type { RunRead } from "../api/types";
import { formatGb } from "./format";
import {
  latestBatchRun,
  nightlyRunView,
  reclaimInputFromImpact,
  reclaimYesterdayDisplay,
  reclaimYesterdayLabel,
  vitalsScoreLabel
} from "./nowStrip";

describe("reclaimYesterdayLabel", () => {
  it("says Not measured for an unmeasured day and ignores a zero gigabyte figure", () => {
    expect(reclaimYesterdayLabel({ measurement_state: "unmeasured", gb_freed_yesterday: 0 })).toBe("Not measured");
    expect(reclaimYesterdayLabel({ measurement_state: "unmeasured", gb_freed_yesterday: null })).toBe("Not measured");
    expect(reclaimYesterdayLabel(null)).toBe("Not measured");
    expect(reclaimYesterdayLabel({ measurement_state: "unmeasured", gb_freed_yesterday: 0 })).not.toBe("0 GB");
  });

  it("keeps the old partial label for callers that still want one string", () => {
    expect(
      reclaimYesterdayLabel({
        measurement_state: "partial",
        gb_freed_yesterday: 0.2,
        measured: 2,
        succeeded: 9
      })
    ).toBe("Partial 2 of 9");
  });

  it("shows the partial byte subtotal and does not invent 0 of 0", () => {
    const fromPayload = reclaimInputFromImpact({
      measurement_state: "partial",
      gb_freed_yesterday: 0
    });
    expect(fromPayload.measured).toBeNull();
    const display = reclaimYesterdayDisplay(fromPayload);
    expect(display.hero).not.toBe("Partial");
    expect(display.caption).toBe("partial");
    expect(display.caption).not.toContain("0 of 0");
  });

  it("prints partial gigabytes as the hero and keeps partial as the caption", () => {
    const display = reclaimYesterdayDisplay(
      { measurement_state: "partial", gb_freed_yesterday: 1.5, measured: 2, succeeded: 9 }
    );
    expect(display.tone).toBe("number");
    expect(`${display.hero} ${display.unit}`).toBe(formatGb(1.5));
    expect(display.caption).toBe("partial · 2 of 9 measured");
  });

  it("prints gigabytes only when measurement is complete", () => {
    expect(reclaimYesterdayLabel({ measurement_state: "complete", gb_freed_yesterday: 1.5 })).toBe(formatGb(1.5));
    expect(reclaimYesterdayLabel({ measurement_state: "complete", gb_freed_yesterday: 1.5 })).toContain("GB");
    expect(reclaimYesterdayLabel({ measurement_state: "complete", gb_freed_yesterday: 0 })).toBe("0 GB");
    expect(reclaimYesterdayLabel({ measurement_state: "complete", gb_freed_yesterday: null })).toBe("Not measured");
  });

  it("does not show 0 GB when nothing succeeded", () => {
    expect(reclaimYesterdayLabel({ measurement_state: "no_actions", gb_freed_yesterday: 0 })).toBe("No actions");
    expect(reclaimYesterdayLabel({ measurement_state: "no_actions", gb_freed_yesterday: 0 })).not.toContain("GB");
  });
});

describe("vitalsScoreLabel", () => {
  it("does not turn a missing score into 0", () => {
    expect(vitalsScoreLabel(null)).toBe("Not measured");
    expect(vitalsScoreLabel(undefined)).toBe("Not measured");
    expect(vitalsScoreLabel(91.5)).toBe("91.5");
  });
});

describe("nightlyRunView", () => {
  const run = (overrides: Partial<RunRead> = {}): RunRead => ({
    id: "run-1",
    kind: "scheduled",
    status: "partial",
    created_at: "2026-09-27T18:00:00Z",
    finished_at: "2026-09-27T23:00:00Z",
    summary: { pipeline: "batch", selected: 10, succeeded: 7 },
    ...overrides
  });

  it("picks the latest batch run and does not invent 0/0", () => {
    const older = run({ id: "older", created_at: "2026-09-26T18:00:00Z" });
    const newer = run({ id: "newer", created_at: "2026-09-27T18:00:00Z" });
    const scan = run({
      id: "scan",
      created_at: "2026-09-28T01:00:00Z",
      summary: { pipeline: "scan", selected: 1, succeeded: 1 }
    });
    expect(latestBatchRun([older, scan, newer])?.id).toBe("newer");
    expect(nightlyRunView(null).succeededSelected).toBe("Not measured");
    expect(nightlyRunView(null).succeededSelected).not.toBe("0/0");
    const view = nightlyRunView(run({ summary: { pipeline: "batch" } }));
    expect(view.succeededSelected).toBe("Unknown/Unknown");
    expect(view.href).toBe("/runs/run-1");
    expect(view.status).toBe("partial");
  });
});
