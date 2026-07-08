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
  it("disables Run until a preset is explicitly selected, then runs with the chosen preset", async () => {
    const onRun = vi.fn();
    const onAssign = vi.fn();
    const filter = leaf("grade", "in", ["D", "F"]);
    render(
      <BulkAssignBar
        selection={{ mode: "filter", filter, totalSelected: 42 }}
        presets={[preset]}
        destinations={[destination]}
        onClear={vi.fn()}
        onAssign={onAssign}
        onPreview={vi.fn()}
        onRun={onRun}
      />
    );

    expect(screen.getByText("Filter selection")).toBeInTheDocument();
    expect(screen.getByText("All 42 matching tables selected")).toBeInTheDocument();
    expect(
      screen.getByText("Actions now apply to every table matching the current filters, across all pages.")
    ).toBeInTheDocument();

    const runButton = screen.getByText("Run now").closest("button")!;
    expect(runButton.disabled).toBe(true);

    // Simulate explicit preset selection via the antd Select.
    const presetSelect = screen.getByRole("combobox", { name: "Assign preset" });
    fireEvent.mouseDown(presetSelect);
    fireEvent.click(await screen.findByText("trino-standard · standard maintenance preset"));

    expect(onAssign).toHaveBeenCalledWith("preset-1");

    const enabledRunButton = screen.getByText("Run now").closest("button")!;
    expect(enabledRunButton.disabled).toBe(false);
    fireEvent.click(screen.getByText("Run now"));
    expect(onRun).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(onRun).toHaveBeenCalledWith({ destinationId: "dest-1", presetId: "preset-1", mode: "standard" });
  });

  it("labels preset and engine options with useful context", async () => {
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

    const presetSelect = screen.getByRole("combobox", { name: "Assign preset" });
    fireEvent.mouseDown(presetSelect);
    fireEvent.click(await screen.findByText("trino-standard · Default local Trino maintenance profile"));
    expect(screen.getAllByText("trino-standard · Default local Trino maintenance profile").length).toBeGreaterThan(0);
    expect(screen.getByText("trino-main · Trino")).toBeInTheDocument();
  });

  it("labels native engine destinations clearly", async () => {
    const nativeDestination: DestinationRead = {
      ...destination,
      id: "dest-native",
      name: "native-datafusion",
      engine: "native",
      config: { engine_impl: "go" }
    };
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1"], totalSelected: 1 }}
        presets={[{ ...preset, destination_id: "dest-native" }]}
        destinations={[nativeDestination]}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={vi.fn()}
      />
    );

    const presetSelect = screen.getByRole("combobox", { name: "Assign preset" });
    fireEvent.mouseDown(presetSelect);
    fireEvent.click(await screen.findByText("trino-standard · standard maintenance preset"));

    expect(screen.getByText("native-datafusion · Native (iceberg-go)")).toBeInTheDocument();
  });

  it("disables manifest rewrite for native destinations", async () => {
    const nativeDestination: DestinationRead = {
      ...destination,
      id: "dest-native",
      name: "native-datafusion",
      engine: "native",
      config: { engine_impl: "go" }
    };
    render(
      <BulkAssignBar
        selection={{ mode: "ids", ids: ["table-1"], totalSelected: 1 }}
        presets={[{ ...preset, destination_id: "dest-native" }]}
        destinations={[nativeDestination]}
        onClear={vi.fn()}
        onAssign={vi.fn()}
        onPreview={vi.fn()}
        onRun={vi.fn()}
        onManifestRewrite={vi.fn()}
      />
    );

    const presetSelect = screen.getByRole("combobox", { name: "Assign preset" });
    fireEvent.mouseDown(presetSelect);
    fireEvent.click(await screen.findByText("trino-standard · standard maintenance preset"));

    expect(screen.getByText("Manifest rewrite").closest("button")?.disabled).toBe(true);
  });
});
