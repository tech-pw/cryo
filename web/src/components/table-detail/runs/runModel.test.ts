import { describe, expect, it } from "vitest";
import type { TableRunRead } from "../../../api/types";
import { buildRunColumns, changeColumns, gradeChange, gridRows, isManualTrigger, mergeSkipColumns, statChanges, toneFor } from "./runModel";

const run = (overrides: Partial<TableRunRead>): TableRunRead => ({
  job_id: "job-1",
  run_id: "run-1",
  status: "succeeded",
  mode: "deep",
  trigger: "cron",
  started_at: "2026-09-23T23:27:28Z",
  finished_at: "2026-09-23T23:38:24Z",
  duration_seconds: 656,
  steps: [],
  ...overrides
});

const skip = (id: string, at: string, reason: string | null, stage = "evaluated") => ({
  id,
  run_id: `nightly-${id}`,
  table_id: "t1",
  stage,
  reason,
  evaluated_at: at
});

describe("mergeSkipColumns", () => {
  it("collapses a streak of same-reason nights and breaks it at a run", () => {
    const runs = buildRunColumns(
      [
        run({ job_id: "a", run_id: "ra", started_at: "2026-10-01T23:00:00Z" }),
        run({ job_id: "b", run_id: "rb", started_at: "2026-10-05T23:00:00Z" })
      ],
      []
    );
    const merged = mergeSkipColumns(runs, [
      skip("1", "2026-10-02T23:00:00Z", "cooldown"),
      skip("2", "2026-10-03T23:00:00Z", "cooldown"),
      skip("3", "2026-10-04T23:00:00Z", "budget_cap", "eligible"),
      skip("4", "2026-10-06T23:00:00Z", "cooldown"),
      skip("5", "2026-10-07T23:00:00Z", null, "selected")
    ]);
    expect(merged.map((column) => (column.kind === "run" ? column.run.run_id : column.kind === "skip" ? `${column.reason}x${column.count}` : column.label))).toEqual([
      "ra",
      "cooldownx2",
      "budget_capx1",
      "rb",
      "cooldownx1"
    ]);
    const capped = merged[2];
    expect(capped.kind === "skip" && capped.hadWork).toBe(true);
  });

  it("drops nights older than the oldest shown run and keeps only the newest skip columns", () => {
    const runs = buildRunColumns([run({ job_id: "a", run_id: "ra", started_at: "2026-10-10T23:00:00Z" })], []);
    const evaluations = [
      skip("old", "2026-10-01T23:00:00Z", "no_work"),
      ...["a", "b", "c"].map((id, index) => skip(id, `2026-10-1${index + 1}T23:00:00Z`, index % 2 ? "cooldown" : "no_work"))
    ];
    const merged = mergeSkipColumns(runs, evaluations, 2);
    expect(merged.filter((column) => column.kind === "skip")).toHaveLength(2);
    expect(merged.some((column) => column.key === "skip-old")).toBe(false);
  });

  it("places a jobless config change on the timeline and never drops it for the skip cap", () => {
    const runs = buildRunColumns(
      [
        run({ job_id: "a", run_id: "ra", started_at: "2026-10-01T23:00:00Z" }),
        run({ job_id: "b", run_id: "rb", started_at: "2026-10-05T23:00:00Z" })
      ],
      []
    );
    const changes = changeColumns([
      { id: "c1", action: "table_config_change", status: "recorded", ts: "2026-10-03T12:00:00Z", detail: "cooldown_hours: 12 -> 20", actor: "aamir" },
      { id: "c2", action: "optimize", status: "succeeded", ts: "2026-10-04T12:00:00Z", detail: "CALL x", job_id: "b" }
    ] as never);
    expect(changes.map((column) => column.label)).toEqual(["Config change"]);
    const merged = mergeSkipColumns(
      runs,
      ["1", "2", "3", "4"].map((id, index) => skip(id, `2026-10-0${index + 6}T23:00:00Z`, `reason-${id}`)),
      1,
      changes
    );
    expect(merged.some((column) => column.kind === "change" && column.detail === "cooldown_hours: 12 -> 20")).toBe(true);
    expect(merged.filter((column) => column.kind === "skip")).toHaveLength(1);
    const changeIndex = merged.findIndex((column) => column.kind === "change");
    expect(changeIndex).toBeGreaterThan(merged.findIndex((column) => column.kind === "run" && column.run.run_id === "ra"));
    expect(changeIndex).toBeLessThan(merged.findIndex((column) => column.kind === "run" && column.run.run_id === "rb"));
  });
});

describe("runModel", () => {
  it("maps statuses to tones without calling waiting work running", () => {
    expect(toneFor("partial")).toBe("attention");
    expect(toneFor("partial_failed")).toBe("attention");
    expect(toneFor("succeeded")).toBe("success");
    expect(toneFor("OK")).toBe("success");
    expect(toneFor("failed")).toBe("failed");
    expect(toneFor("running")).toBe("running");
    expect(toneFor("admitted")).toBe("queued");
    expect(toneFor("waiting")).toBe("queued");
    expect(toneFor("cancelled")).toBe("skipped");
    expect(toneFor(null)).toBe("none");
  });

  it("tells scheduled from manual triggers", () => {
    expect(isManualTrigger("cron")).toBe(false);
    expect(isManualTrigger("scheduler")).toBe(false);
    expect(isManualTrigger("schedule_backlog")).toBe(false);
    expect(isManualTrigger("web-table-operation")).toBe(true);
    expect(isManualTrigger(null)).toBe(true);
  });

  it("orders columns oldest first and fills cells from steps, then audit receipts", () => {
    const columns = buildRunColumns(
      [
        run({
          job_id: "new",
          run_id: "r-new",
          started_at: "2026-09-24T00:00:00Z",
          steps: [{ step_index: 0, operation_type: "optimize", status: "succeeded", started_at: "2026-09-24T00:00:00Z", finished_at: "2026-09-24T00:05:00Z" }]
        }),
        run({ job_id: "old", run_id: "r-old", status: "failed", started_at: "2026-09-09T00:00:00Z" })
      ],
      [
        { action: "remove_orphan_files", status: "FAILED", ts: "2026-09-09T00:10:00Z", detail: "CALL x", job_id: "old" } as never,
        { action: "optimize", status: "OK", ts: "2026-09-24T00:05:00Z", detail: "CALL rewrite_data_files", engine: "spark_emr", job_id: "new" } as never
      ]
    );
    expect(columns.map((column) => column.key)).toEqual(["old", "new"]);
    expect(columns[0].cells.get("remove_orphan_files")).toMatchObject({ tone: "failed", detail: "CALL x" });
    expect(columns[1].cells.get("optimize")).toMatchObject({ tone: "success", durationMs: 300000, detail: "CALL rewrite_data_files", engine: "spark_emr" });
    expect(columns[1].cells.has("expire_snapshots")).toBe(false);
  });

  it("shows only core rows unless a run used an extra operation", () => {
    const columns = buildRunColumns(
      [run({ steps: [{ step_index: 0, operation_type: "convert_deletes", status: "succeeded" }] })],
      []
    );
    expect(gridRows(columns)).toEqual(["convert_deletes", "optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]);
    expect(gridRows([])).toEqual(["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]);
  });

  it("reports before/after when both scans exist", () => {
    expect(statChanges(run({}))).toEqual([]);
    const changes = statChanges(
      run({
        before_stats: { scan_at: "a", total_files: 661, small_files_count: 186, overall_score: 48, total_size_gb: 32.9 },
        after_stats: { scan_at: "b", total_files: 484, small_files_count: 2, overall_score: 64, total_size_gb: 33.0 }
      })
    );
    expect(changes.find((change) => change.label === "Small files")).toMatchObject({ before: 186, after: 2, lowerIsBetter: true });
  });

  it("keeps before-stats when the run has no after-scan yet", () => {
    const changes = statChanges(
      run({
        status: "running",
        before_stats: { scan_at: "a", total_files: 1303, small_files_count: 1303, overall_score: 32, total_size_gb: 1.6 },
        after_stats: null
      })
    );
    expect(changes.find((change) => change.label === "Data files")).toMatchObject({ before: 1303, after: null });
    expect(changes.find((change) => change.label === "Health score")).toMatchObject({ before: 32, after: null });
    // Delete files weren't recorded in this snapshot, so the row is dropped rather than shown empty.
    expect(changes.find((change) => change.label === "Delete files")).toBeUndefined();
  });

  it("reports the grade before and after", () => {
    expect(gradeChange(run({}))).toBeNull();
    expect(gradeChange(run({ before_stats: { scan_at: "a", grade: "F" } }))).toEqual({ before: "F", after: null });
    expect(
      gradeChange(run({ before_stats: { scan_at: "a", grade: "F" }, after_stats: { scan_at: "b", grade: "B" } }))
    ).toEqual({ before: "F", after: "B" });
  });
});
