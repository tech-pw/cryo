import { describe, expect, it } from "vitest";
import {
  RUNNABLE_OPERATIONS,
  lastRunByOperation,
  operationEvidence,
  pointsByOperation,
  readDeductionList,
  remedyForDeduction
} from "./operationCatalog";

describe("operationCatalog", () => {
  it("keeps the canonical compact-then-clean-up order", () => {
    expect(RUNNABLE_OPERATIONS).toEqual(["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]);
  });

  it("maps scanner deduction labels to the operation that fixes them", () => {
    expect(remedyForDeduction("Small-file fragmentation", "t")).toEqual({ kind: "operation", op: "optimize" });
    expect(remedyForDeduction("Snapshot retention buildup", "t")).toEqual({ kind: "operation", op: "expire_snapshots" });
    expect(remedyForDeduction("Metadata ratio overhead", "t")).toEqual({ kind: "operation", op: "optimize_manifests" });
    expect(remedyForDeduction("Partition volume skew", "t")).toEqual({ kind: "operation", op: "optimize" });
    expect(remedyForDeduction("Suboptimal properties", "t")).toMatchObject({ kind: "settings" });
    expect(remedyForDeduction("High velocity streaming", "t")).toMatchObject({ kind: "none" });
  });

  it("sums recoverable points per operation and ignores malformed deductions", () => {
    const deductions = readDeductionList({
      health_deductions: [
        { label: "Small-file fragmentation", deduction: 8, severity: "warning", reason: "" },
        { label: "Partition volume skew", deduction: 8, severity: "warning", reason: "" },
        { label: "Snapshot retention buildup", deduction: 20, severity: "critical", reason: "" },
        { label: "broken", deduction: "8" },
        null
      ]
    });
    expect(deductions.map((item) => item.deduction)).toEqual([20, 8, 8]);
    const points = pointsByOperation(deductions, "t");
    expect(points.get("optimize")).toBe(16);
    expect(points.get("expire_snapshots")).toBe(20);
    expect(points.has("remove_orphan_files")).toBe(false);
  });

  it("shows unknown evidence as a dash, never as zero", () => {
    expect(operationEvidence("optimize", {}, null).value).toBe("—");
    expect(operationEvidence("remove_orphan_files", {}, null).value).toBe("—");
    expect(operationEvidence("remove_orphan_files", {}, 0).value).toBe("0");
    expect(operationEvidence("optimize", { small_files_count: 147, total_files: 629 }, null).detail).toContain("23% of 629 files");
  });

  it("keeps the latest audit entry per operation and tells nightly from manual", () => {
    const runs = lastRunByOperation([
      { action: "optimize", ts: "2026-09-20T00:00:00Z", status: "OK", actor: "system:scheduler" },
      { action: "optimize", ts: "2026-09-24T00:00:00Z", status: "FAILED", actor: "alice" },
      { action: "expire_snapshots", ts: "2026-09-23T00:00:00Z", status: "OK", actor: "system:scheduler" }
    ]);
    expect(runs.get("optimize")).toMatchObject({ status: "FAILED", scheduled: false });
    expect(runs.get("expire_snapshots")).toMatchObject({ scheduled: true });
  });
});
