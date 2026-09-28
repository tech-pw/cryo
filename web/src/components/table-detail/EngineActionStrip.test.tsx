import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EngineActionStrip } from "./EngineActionStrip";

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: true, hidden: false, reason: null })
}));

vi.mock("../../pages/table-detail/TableDetailContext", () => ({
  useTableDetailContext: () => ({
    row: { id: "table-1" },
    effectivePreset: { data: null },
    effectiveDestination: null,
    assignPresetOptions: [],
    assign: { isPending: false, mutate: vi.fn() },
    unassign: { isPending: false, mutate: vi.fn() },
    presets: { isLoading: false },
    compatiblePresets: { isLoading: false },
    queuedMaintenanceRunId: null,
    run: { isPending: false, mutate: vi.fn() },
    handleRunAction: vi.fn(),
    setPreviewOpen: vi.fn(),
    manifestRewrite: { isPending: false, mutate: vi.fn() }
  })
}));

describe("EngineActionStrip", () => {
  it("disables Manifest without a preset for the same reason as Run", () => {
    render(<EngineActionStrip onOpenSettings={vi.fn()} />);
    const manifest = screen.getByRole("button", { name: "Manifest" });
    expect(manifest).toBeDisabled();
    expect(manifest).toHaveAttribute("title", "Assign a workflow before running maintenance");
    expect(screen.getByText("Assign a workflow before running maintenance")).toBeInTheDocument();
  });
});
