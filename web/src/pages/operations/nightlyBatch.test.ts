import { describe, expect, it } from "vitest";
import type { RunRead } from "../../api/types";
import {
  isBatchRun,
  latestNightlyRunPath,
  NIGHTLY_SCHEDULE_LABEL,
  nightlyBatchRuns,
  NO_PRESETS_COPY
} from "./nightlyBatch";

const run = (overrides: Partial<RunRead> = {}): RunRead => ({
  id: "run-1",
  kind: "scheduled",
  status: "succeeded",
  created_at: "2026-09-27T02:00:00Z",
  ...overrides
});

describe("isBatchRun", () => {
  it("matches only summary.pipeline === batch", () => {
    expect(isBatchRun(run({ summary: { pipeline: "batch" } }))).toBe(true);
    expect(isBatchRun(run({ summary: { pipeline: "batch", succeeded: 3 } }))).toBe(true);
    expect(isBatchRun(run({ summary: { pipeline: "other" } }))).toBe(false);
    expect(isBatchRun(run({ summary: { pipeline: "Batch" } }))).toBe(false);
    expect(isBatchRun(run({ summary: null }))).toBe(false);
    expect(isBatchRun(run())).toBe(false);
  });
});

describe("nightlyBatchRuns", () => {
  it("keeps batch runs newest first and drops the rest", () => {
    const older = run({
      id: "older",
      created_at: "2026-09-26T02:00:00Z",
      summary: { pipeline: "batch" }
    });
    const newer = run({
      id: "newer",
      created_at: "2026-09-27T02:00:00Z",
      summary: { pipeline: "batch" }
    });
    const other = run({
      id: "other",
      created_at: "2026-09-28T02:00:00Z",
      summary: { pipeline: "scan" }
    });

    expect(nightlyBatchRuns([older, other, newer]).map((item) => item.id)).toEqual(["newer", "older"]);
  });
});

describe("latestNightlyRunPath", () => {
  it("links to the newest batch run", () => {
    const older = run({
      id: "older",
      created_at: "2026-09-26T02:00:00Z",
      summary: { pipeline: "batch" }
    });
    const newer = run({
      id: "newer",
      created_at: "2026-09-27T02:00:00Z",
      summary: { pipeline: "batch" }
    });

    expect(latestNightlyRunPath([older, newer])).toBe("/runs/newer");
  });

  it("returns null when no batch run exists", () => {
    expect(latestNightlyRunPath([run()])).toBeNull();
    expect(latestNightlyRunPath([])).toBeNull();
  });
});

describe("nightly labels", () => {
  it("uses the schedule label and zero-preset copy from the task", () => {
    expect(NIGHTLY_SCHEDULE_LABEL).toBe("Nightly (CronJob)");
    expect(NO_PRESETS_COPY).toBe("Maintenance is driven by the nightly batch.");
    expect(NO_PRESETS_COPY.toLowerCase()).not.toContain("nothing configured");
  });
});
