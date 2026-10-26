import { describe, expect, it } from "vitest";

import type { PresetRead, RunRead } from "../../api/types";
import { runWorkflow } from "./useRunWorkflow";
import { NIGHTLY_WORKFLOW_NAME } from "./nightlyBatch";

const run = (over: Partial<RunRead>): RunRead => ({ id: "r1", kind: "maintenance", status: "succeeded", created_at: "2026-10-26T00:00:00Z", ...over });
const presets = [{ id: "p1", name: "nightly-standard" }] as PresetRead[];

describe("runWorkflow", () => {
  it("names a scheduled run by its preset, not the run kind", () => {
    expect(runWorkflow(run({ preset_id: "p1", summary: { target_label: "demo.orders +41 tables" } }), presets)).toEqual({
      name: "nightly-standard",
      link: "/runs/workflows/p1"
    });
  });

  it("falls back to 'Workflow' while presets load", () => {
    expect(runWorkflow(run({ preset_id: "p1" }), undefined).name).toBe("Workflow");
  });

  it("names scan and nightly batch runs", () => {
    expect(runWorkflow(run({ kind: "scan" }), presets)).toEqual({ name: "Scanner", link: "/runs/scanner" });
    expect(runWorkflow(run({ summary: { pipeline: "batch" } }), presets).name).toBe(NIGHTLY_WORKFLOW_NAME);
  });

  it("uses the target for a manual run with no preset", () => {
    expect(runWorkflow(run({ summary: { target_label: "demo.orders" } }), presets).name).toBe("demo.orders");
  });
});
