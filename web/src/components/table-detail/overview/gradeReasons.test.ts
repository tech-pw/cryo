import { describe, expect, it } from "vitest";
import {
  diagnosticSignals,
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
