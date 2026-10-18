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

const openRunDialog = () => fireEvent.click(screen.getByRole("button", { name: /Run maintenance/ }));

describe("BulkAssignBar", () => {
  it("explains why Run is disabled until a workflow is chosen, then queues the chosen workflow", () => {
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

    openRunDialog();
    expect(screen.getByText("Run maintenance on all 42 matching tables")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue run" })).toBeDisabled();
    expect(screen.getByText("Choose the workflow to run.")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Run workflow" }), { target: { value: "preset-1" } });
    expect(screen.getByRole("button", { name: "Queue run" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Queue run" }));
    expect(onRun).toHaveBeenCalledWith({ destinationId: "dest-1", presetId: "preset-1", mode: "standard" });
  });

  it("assigns a workflow straight from the bar and pre-fills it for the next run", () => {
    const onAssign = vi.fn();
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1"], totalSelected: 1 }}
        presets={[{ ...preset, description: "Default local Trino maintenance profile" }]}
        destinations={[destination]}
        onClear={vi.fn()}
        onAssign={onAssign}
        onPreview={vi.fn()}
        onRun={vi.fn()}
      />
    );

    const presetSelect = screen.getByRole("combobox", { name: "Assign preset" });
    expect(
      Array.from((presetSelect as HTMLSelectElement).options).some((option) =>
        option.text.includes("trino-standard · Default local Trino maintenance profile")
      )
    ).toBe(true);
    fireEvent.change(presetSelect, { target: { value: "preset-1" } });
    expect(onAssign).toHaveBeenCalledWith("preset-1");

    openRunDialog();
    expect((screen.getByRole("combobox", { name: "Run workflow" }) as HTMLSelectElement).value).toBe("preset-1");
    expect(screen.getByText("trino-main · Trino")).toBeInTheDocument();
  });

  it("offers select-all-matching and warns about rows a run would likely skip", () => {
    const onSelectAllMatching = vi.fn();
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1", "table-2"], totalSelected: 2 }}
        presets={[preset]}
        destinations={[destination]}
        matchingTotal={120}
        onSelectAllMatching={onSelectAllMatching}
        likelySkipped={1}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("Select all 120 matching"));
    expect(onSelectAllMatching).toHaveBeenCalledTimes(1);
    openRunDialog();
    expect(screen.getByText(/1 of the selected tables is already running/)).toBeInTheDocument();
  });

  it("labels native engine destinations and blocks manifest-only runs on them", () => {
    const nativeDestination: DestinationRead = {
      ...destination,
      id: "dest-native",
      name: "native-datafusion",
      engine: "native",
      config: { engine_impl: "go" }
    };
    const onManifestRewrite = vi.fn();
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1"], totalSelected: 1 }}
        presets={[{ ...preset, destination_id: "dest-native" }]}
        destinations={[nativeDestination]}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={vi.fn()}
        onManifestRewrite={onManifestRewrite}
      />
    );

    openRunDialog();
    fireEvent.change(screen.getByRole("combobox", { name: "Run workflow" }), { target: { value: "preset-1" } });
    expect(screen.getByText("native-datafusion · Native (iceberg-go)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manifests only" }));
    expect(screen.getByRole("button", { name: "Queue run" })).toBeDisabled();
  });
});
