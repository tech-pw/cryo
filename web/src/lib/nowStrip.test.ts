import { describe, expect, it } from "vitest";
import type { RunRead } from "../api/types";
import { formatGb } from "./format";
import {
  latestBatchRun,
  nightlyRunView,
  reclaimInputFromImpact,
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

  it("says Partial N of M and does not print the partial subtotal as gigabytes", () => {
    const label = reclaimYesterdayLabel({
      measurement_state: "partial",
      gb_freed_yesterday: 0.2,
      measured: 2,
      succeeded: 9
    });
    expect(label).toBe("Partial 2 of 9");
    expect(label).not.toContain("GB");
    expect(label).not.toContain("0");
  });

  it("does not invent Partial 0 of 0 when the impact block has no action counts", () => {
    const fromPayload = reclaimInputFromImpact({
      measurement_state: "partial",
      gb_freed_yesterday: 0
    });
    expect(fromPayload.measured).toBeNull();
    expect(fromPayload.succeeded).toBeNull();
    const label = reclaimYesterdayLabel(fromPayload);
    expect(label).toBe("Partial");
    expect(label).not.toBe("Partial 0 of 0");
    expect(label).not.toContain("GB");
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
    expect(view.href).toBe("/operations/runs/run-1");
    expect(view.status).toBe("partial");
  });
});
