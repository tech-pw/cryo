import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BulkAssignBar } from "./BulkAssignBar";
import type { DestinationRead, PresetRead } from "../api/types";
import { leaf } from "../lib/predicate";

const preset: PresetRead = {
  id: "preset-1",
  name: "trino-standard",
  destination_id: "dest-1",
  mode: "standard",
  operations: ["optimize"],
  params: {},
  priority: 100,
  enabled: true,
  guardrails: {},
  created_at: "",
  updated_at: ""
};

const destination: DestinationRead = {
  id: "dest-1",
  name: "trino-main",
  engine: "trino",
  config: {},
  enabled: true,
  created_at: "",
  updated_at: ""
};

describe("BulkAssignBar", () => {
  it("runs with the filter selection when all matching is active", () => {
    const onRun = vi.fn();
    const filter = leaf("grade", "in", ["D", "F"]);
    render(
      <BulkAssignBar
        selection={{ mode: "filter", filter, totalSelected: 42 }}
        presets={[preset]}
        destinations={[destination]}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={onRun}
      />
    );

    expect(screen.getByText("Filter selection")).toBeInTheDocument();
    expect(screen.getByText("All 42 matching tables selected")).toBeInTheDocument();
    expect(
      screen.getByText("Actions now apply to every table matching the current filters, across all pages.")
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Run now"));
    expect(onRun).toHaveBeenCalledWith({ destinationId: "dest-1", presetId: "preset-1", mode: "standard" });
  });

  it("labels preset and engine options with useful context", () => {
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1"], totalSelected: 1 }}
        presets={[{ ...preset, description: "Default local Trino maintenance profile" }]}
        destinations={[destination]}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={vi.fn()}
      />
    );

    expect(screen.getByText("trino-standard · Default local Trino maintenance profile")).toBeInTheDocument();
    expect(screen.getByText("trino-main · trino")).toBeInTheDocument();
  });
});
