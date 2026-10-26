import { describe, expect, it } from "vitest";
import { countOperations, pipelineStepsFromJobs, type PipelineJob } from "./runPipeline";

const FOUR = ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"];
const planJob = (status: string, operations: string[] = FOUR): PipelineJob => ({ status, plan: { operations } });

describe("run pipeline", () => {
  it("counts every operation across tables and does not stop at a failed job", () => {
    const jobs = [
      ...Array.from({ length: 488 }, () => planJob("succeeded")),
      planJob("failed"),
      planJob("failed")
    ];
    const steps = pipelineStepsFromJobs(jobs);
    expect(steps.map((step) => step.key)).toEqual(FOUR);
    expect(steps.every((step) => step.status === "failed")).toBe(true);
    expect(steps[0].detail).toBe("488 ✓ · 2 ✗");
    expect(steps[3].detail).toBe("488 ✓ · 2 ✗");
  });

  it("prefers executed steps over the plan and attributes a failure to its operation", () => {
    const failedCompaction: PipelineJob = {
      status: "failed",
      plan: { operations: FOUR },
      steps: [{ step_index: 0, operation_type: "optimize", status: "failed" }]
    };
    const ok: PipelineJob = {
      status: "succeeded",
      plan: { operations: FOUR },
      steps: FOUR.map((op, index) => ({ step_index: index, operation_type: op, status: "succeeded" }))
    };
    const counts = countOperations([ok, ok, failedCompaction]);
    expect(counts.find((item) => item.op === "optimize")).toMatchObject({ succeeded: 2, failed: 1 });
    // The failed job never reached expire, so it is not counted as an expire failure.
    expect(counts.find((item) => item.op === "expire_snapshots")).toMatchObject({ succeeded: 2, failed: 0, skipped: 1 });
  });

  it("derives the operation list from the jobs, in canonical order", () => {
    const steps = pipelineStepsFromJobs([
      planJob("succeeded", ["expire_snapshots", "optimize", "compute_statistics"]),
      planJob("succeeded", ["rewrite_position_deletes", "optimize"])
    ]);
    expect(steps.map((step) => step.key)).toEqual(["rewrite_position_deletes", "optimize", "compute_statistics", "expire_snapshots"]);
    expect(steps[0].label).toBe("rewrite position deletes");
  });

  it("reports running, waiting and skipped operations", () => {
    const steps = pipelineStepsFromJobs([planJob("running", ["optimize"]), planJob("queued", ["optimize"]), planJob("queued", ["expire_snapshots"]), planJob("skipped", ["remove_orphan_files"])]);
    expect(steps.find((step) => step.key === "optimize")).toMatchObject({ status: "running", detail: "1 running · 1 waiting" });
    expect(steps.find((step) => step.key === "expire_snapshots")).toMatchObject({ status: "pending", statusText: "Waiting" });
    expect(steps.find((step) => step.key === "remove_orphan_files")).toMatchObject({ status: "skipped" });
  });

  it("keeps single-table runs free of counts", () => {
    const [step] = pipelineStepsFromJobs([planJob("succeeded", ["optimize"])]);
    expect(step).toMatchObject({ status: "succeeded" });
    expect(step.detail).toBeUndefined();
  });

  it("returns nothing when no job names an operation", () => {
    expect(pipelineStepsFromJobs([{ status: "succeeded", plan: null }])).toEqual([]);
  });
});
