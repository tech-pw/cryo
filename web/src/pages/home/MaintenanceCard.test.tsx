import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { RunRead } from "../../api/types";
import { MaintenanceCard, maintenanceProgress } from "./MaintenanceCard";

const run: RunRead = { id: "run-1", kind: "maintenance", status: "partial", created_at: "2026-09-28T00:00:00Z", finished_at: "2026-09-28T01:00:00Z", summary: { selected: 10, succeeded: 3, partial: 2, failed: 1, skipped: 1, nothing_to_do: 2, planning_failed: 1, locked: 1, over_budget: 5 } };
describe("Nightly outcomes", () => {
  it("counts partial work and planning outcomes without double-counting locks or over-budget tables", () => {
    expect(maintenanceProgress(run).complete).toBe(10);
    render(<MemoryRouter><MaintenanceCard run={run} loading={false} error={false} /></MemoryRouter>);
    expect(screen.getByText("Tables succeeded").parentElement).toHaveTextContent("3 / 10 selected");
    expect(screen.getByRole("img", { name: /10 of 10 selected tables have outcomes/ })).toBeInTheDocument();
    expect(screen.getByText("Partial").closest("div")).toHaveTextContent("2");
    expect(screen.getByText(/2 needed no work/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inspect run" })).toHaveAttribute("href", "/runs/run-1");
  });
  it.each([{ selected: 0 }, { selected: 10, succeeded: 2 }, { ...run.summary, succeeded: 20 }, { ...run.summary, partial: -1 }])("does not draw a misleading progress bar for incomplete or invalid counters: %j", (summary) => {
    expect(maintenanceProgress({ ...run, summary }).percent).toBeNull();
  });
});
