import { describe, expect, it } from "vitest";
import {
  diagnosticSignals,
  gradeUnchangedAfterMaintenance,
  readDesignFindings,
  formatDeductionLine,
  formatScoreSum,
  gradeLetter,
  healthTrendPoints,
  integerScore,
  isTableScanned,
  markersFromAudit,
  readDeductions,
  receiptRunHref,
  scoreTrendAnswer,
  sortDeductions,
  type HealthDeduction
} from "./gradeReasons";

const smallFiles: HealthDeduction = {
  label: "Small files",
  deduction: 20,
  severity: "critical",
  reason: "56% of files under target (critical)"
};

const snapshots: HealthDeduction = {
  label: "Snapshots",
  deduction: 8,
  severity: "warning",
  reason: "too many snapshots"
};

describe("gradeReasons", () => {
  it("orders scores by time and preserves invalid scores as gaps", () => {
    const points = healthTrendPoints([
      { timestamp: "2026-09-03", score: 80 },
      { timestamp: "2026-09-02", score: 120 },
      { timestamp: "2026-09-01", score: 60 }
    ]);
    expect(points.map((point) => point.value)).toEqual([60, null, 80]);
    expect(scoreTrendAnswer(points)).toEqual({ kind: "up", points: 20, since: "2026-09-01" });
    expect(integerScore(-1)).toBeNull();
    expect(integerScore(101)).toBeNull();
  });
  it("sorts deductions by points and formats the minus line", () => {
    expect(sortDeductions([snapshots, smallFiles])).toEqual([smallFiles, snapshots]);
    expect(formatDeductionLine(smallFiles)).toBe("−20 Small files: 56% of files under target (critical)");
  });

  it("shows the sum from 100 and labels a persisted score that differs", () => {
    expect(formatScoreSum([smallFiles, snapshots], 72)).toBe("100 − 28 = 72");
    expect(formatScoreSum([smallFiles], 71)).toBe("100 − 20 = 80 (computed); persisted score 71");
    expect(formatScoreSum([smallFiles], null)).toBe("100 − 20 = 80 (computed); persisted score not recorded");
    expect(formatScoreSum([smallFiles], null)).not.toContain("persisted score 0");
  });

  it("never turns an unknown score into 0", () => {
    expect(integerScore(null)).toBeNull();
    expect(integerScore(undefined)).toBeNull();
    expect(integerScore(Number.NaN)).toBeNull();
    expect(integerScore(0)).toBe(0);
    expect(gradeLetter(null, null)).toBeNull();
    expect(gradeLetter(undefined, integerScore(null))).toBeNull();
    expect(gradeLetter(null, 0)).toBe("F");
  });

  it("treats a missing scan as not scanned and keeps null trend scores null", () => {
    expect(isTableScanned(null)).toBe(false);
    expect(isTableScanned({})).toBe(false);
    expect(isTableScanned({ scan_at: "2026-09-28T09:10:00Z" })).toBe(true);
    const points = healthTrendPoints([
      { timestamp: "2026-09-01T00:00:00Z", score: null, grade: null },
      { timestamp: "2026-09-02T00:00:00Z", score: 80, grade: "B" }
    ]);
    expect(points[0]?.value).toBeNull();
    expect(points.some((point) => point.value === 0)).toBe(false);
    expect(scoreTrendAnswer(points)).toEqual({ kind: "single" });
  });

  it("keeps triggered signals that are not in the deduction list", () => {
    const signals = diagnosticSignals(
      [
        { triggered: true, signal_name: "query_latency", severity: "warning", reason: "p95 rose" },
        { triggered: true, signal_name: "Small files", severity: "critical", reason: "already scored" },
        { triggered: false, signal_name: "cost_anomaly", severity: "info", reason: "quiet" }
      ],
      [smallFiles]
    );
    expect(signals.map((signal) => signal.signal_name)).toEqual(["query_latency"]);
  });

  it("does not invent a maintenance marker or a run link without an id", () => {
    expect(markersFromAudit([{ action: "optimize" }, { action: "optimize", ts: "not-a-date" }])).toEqual([]);
    expect(markersFromAudit([{ id: 4, action: "optimize", ts: "2026-09-27T02:00:00Z" }])).toEqual([
      { id: "4", at: "2026-09-27T02:00:00Z", kind: "maintenance", label: "optimize" }
    ]);
    expect(receiptRunHref({ detail: "job_id=job-1 at 2026-09-27T02:00:00Z" })).toBeNull();
    expect(receiptRunHref({ job_id: "job-1" })).toBe("/runs/jobs/job-1");
    expect(receiptRunHref({ run_id: "run-1", job_id: "job-1" })).toBe("/runs/run-1");
  });

  it("reads only a real deduction array", () => {
    expect(readDeductions(null)).toBeNull();
    expect(readDeductions({})).toBeNull();
    expect(readDeductions({ health_deductions: [] })).toEqual([]);
    expect(readDeductions({ health_deductions: [smallFiles, { label: "bad" }] })).toEqual([smallFiles]);
  });
});

describe("#264 design findings and unchanged grade", () => {
  it("reads structural findings and drops malformed rows", () => {
    const raw = {
      health_design_findings: [
        { label: "Partition volume skew", severity: "critical", reason: "Skew ratio of 94.0x", recommendation: "Bucket it" },
        { label: "" },
        null,
        { label: "Partition count complexity" }
      ]
    };
    expect(readDesignFindings(raw)).toEqual([
      { label: "Partition volume skew", severity: "critical", reason: "Skew ratio of 94.0x", recommendation: "Bucket it" },
      { label: "Partition count complexity", severity: "warning", reason: "", recommendation: "" }
    ]);
    expect(readDesignFindings({})).toEqual([]);
    expect(readDesignFindings(null)).toEqual([]);
  });

  const points = [
    { at: "2026-10-20T00:00:00Z", grade: "F" },
    { at: "2026-10-22T00:00:00Z", grade: "F" },
    { at: "2026-10-24T00:00:00Z", grade: "F" }
  ];

  it("flags a grade that didn't move across the last successful run", () => {
    expect(gradeUnchangedAfterMaintenance(points, [{ ts: "2026-10-23T00:00:00Z", status: "OK" }])).toBe(true);
  });

  it("ignores failed runs and runs with no scan after them", () => {
    expect(gradeUnchangedAfterMaintenance(points, [{ ts: "2026-10-23T00:00:00Z", status: "FAILED" }])).toBe(false);
    expect(gradeUnchangedAfterMaintenance(points, [{ ts: "2026-10-25T00:00:00Z", status: "OK" }])).toBe(false);
    expect(gradeUnchangedAfterMaintenance(points, [])).toBe(false);
  });

  it("is false when the grade improved after the run", () => {
    const improved = [...points.slice(0, 2), { at: "2026-10-24T00:00:00Z", grade: "C" }];
    expect(gradeUnchangedAfterMaintenance(improved, [{ created_at: "2026-10-23T00:00:00Z", status: "succeeded" }])).toBe(false);
  });
});
