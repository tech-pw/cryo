import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MaintenanceStatusRead } from "../../api/maintenanceStatus";
import { MaintenanceTab } from "./MaintenanceTab";

const { getTableMaintenanceStatus, getTableRuns, listPresets, listDestinations } = vi.hoisted(() => ({
  getTableMaintenanceStatus: vi.fn(),
  getTableRuns: vi.fn().mockResolvedValue([]),
  getTableAudit: vi.fn().mockResolvedValue([]),
  getEffectiveParams: vi.fn().mockResolvedValue({
    table_id: "table-1",
    preset_params: {},
    param_overrides: {},
    effective_params: {}
  }),
  getEffectivePreset: vi.fn().mockResolvedValue({ table_id: "table-1", provenance: "none" }),
  listPresets: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([]),
  listCompatiblePresets: vi.fn().mockResolvedValue([]),
  assignTables: vi.fn(),
  unassignTables: vi.fn(),
  patchTableParamOverrides: vi.fn(),
  getAutoCompCandidates: vi.fn().mockResolvedValue({ items: [], skipped: [], compare: {} }),
  getCompactionCandidates: vi.fn().mockResolvedValue({ candidates: [] }),
  getTableSignals: vi.fn().mockResolvedValue({
    signals: [],
    any_triggered: false,
    checked_at: "2026-09-28T12:00:00Z"
  }),
  getTable: vi.fn().mockResolvedValue(null),
  updateTableBenefitScores: vi.fn()
}));

vi.mock("../../api/maintenanceStatus", () => ({
  getTableMaintenanceStatus
}));

vi.mock("../../api/client", () => ({
  getTableRuns,
  getTableCost: vi.fn().mockResolvedValue({ table_id: "table-1", orphan_bytes: null }),
  getTableAudit: vi.fn().mockResolvedValue([]),
  getEffectiveParams: vi.fn().mockResolvedValue({
    table_id: "table-1",
    preset_params: {},
    param_overrides: {},
    effective_params: {}
  }),
  getEffectivePreset: vi.fn().mockResolvedValue({ table_id: "table-1", provenance: "none" }),
  listPresets,
  listDestinations,
  listCompatiblePresets: vi.fn().mockResolvedValue([]),
  assignTables: vi.fn(),
  unassignTables: vi.fn(),
  patchTableParamOverrides: vi.fn(),
  getAutoCompCandidates: vi.fn().mockResolvedValue({ items: [], skipped: [], compare: {} }),
  getCompactionCandidates: vi.fn().mockResolvedValue({ candidates: [] }),
  getTableSignals: vi.fn().mockResolvedValue({
    signals: [],
    any_triggered: false,
    checked_at: "2026-09-28T12:00:00Z"
  }),
  getTable: vi.fn().mockResolvedValue(null),
  updateTableBenefitScores: vi.fn()
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer",
    subject: "viewer"
  })
}));

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: false, reason: "viewer", hidden: false })
}));

const base = (): MaintenanceStatusRead => ({
  table_id: "table-1",
  eligible: true,
  evaluated_at: "2026-09-28T12:00:00Z",
  eligibility_basis: "Nightly batch selection.",
  blocking_reasons: [],
  thresholds: {
    cooldown_hours: 72,
    max_stats_age_days: 3,
    min_delete_file_pct: 1
  },
  last_actions: [],
  last_actions_note: "No grouped audit rows.",
  next_nightly_at: null,
  next_nightly_note: "Not derivable.",
  omitted: [
    {
      code: "over_budget_last_run",
      where: "fit_budget",
      why: "Over budget is not a per-table predicate."
    }
  ]
});

const renderTab = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tables/table-1?tab=maintenance"]}>
        <Routes>
          <Route path="/tables/:tableId" element={<MaintenanceTab />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("MaintenanceTab", () => {
  beforeEach(() => {
    getTableMaintenanceStatus.mockReset();
    getTableRuns.mockReset();
    getTableRuns.mockResolvedValue([]);
    listPresets.mockClear();
    listDestinations.mockClear();
  });

  it("shows a clear eligible state and does not invent last-action rows", async () => {
    getTableMaintenanceStatus.mockResolvedValue(base());
    renderTab();

    expect(await screen.findByRole("heading", { name: "Eligible for nightly selection" })).toBeInTheDocument();
    expect(screen.getByText("No blocking reasons.")).toBeInTheDocument();
    expect(screen.getByText("Next nightly not recorded")).toBeInTheDocument();
    expect(screen.queryByText("Last actions are not recorded")).not.toBeInTheDocument();
    expect(screen.queryByText("optimize")).not.toBeInTheDocument();
    expect(screen.queryByText("expire_snapshots")).not.toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();

    expect(screen.queryByText("History not recorded yet")).not.toBeInTheDocument();
    expect(screen.queryByText("engine default")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(getTableMaintenanceStatus).toHaveBeenCalledWith({ token: "test-token" }, "table-1");
    });
    expect(listPresets).not.toHaveBeenCalled();
    expect(listDestinations).not.toHaveBeenCalled();
  });

  it("shows waiting work as waiting in the run grid, never as running", async () => {
    getTableMaintenanceStatus.mockResolvedValue(base());
    getTableRuns.mockResolvedValue([
      {
        job_id: "job-9",
        run_id: "run-9",
        status: "admitted",
        trigger: "web-table-operation",
        mode: "light",
        started_at: "2026-09-30T10:00:00Z",
        steps: []
      }
    ]);
    renderTab();

    expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(await screen.findByText("1 in progress")).toBeInTheDocument();
    expect(document.querySelector(".td-grid-bar")?.getAttribute("data-tone")).toBe("queued");
    const runGrid = document.querySelector<HTMLElement>(".td-grid");
    expect(runGrid).toHaveClass("td-grid");
    expect(runGrid?.style.gridTemplateColumns).toBe("minmax(112px, max-content) repeat(1, 20px)");
    expect(runGrid?.style.gridTemplateColumns).not.toContain("1fr");
    expect(document.querySelector(".td-grid-bar span")?.getAttribute("style")).toContain("height:");
    expect(document.querySelector(".td-run-status")?.textContent).toContain("Waiting");
    expect(screen.queryByText("Running", { selector: ".td-run-status" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open full run/ })).toHaveAttribute("href", "/runs/run-9");
  });

  it("shows each blocking reason with its values and keeps unknown bytes unknown", async () => {
    getTableMaintenanceStatus.mockResolvedValue({
      ...base(),
      eligible: false,
      blocking_reasons: [
        {
          code: "inactive",
          summary: "Table is inactive (is_active=false)",
          predicate: "load_candidates",
          blocks_nightly_selection: true,
          until: null,
          values: { is_active: false }
        },
        {
          code: "stats_stale",
          summary: "Stats are older than 3 days",
          predicate: "load_candidates",
          blocks_nightly_selection: true,
          until: null,
          values: { max_stats_age_days: 3, scan_at: null, cutoff: "2026-09-25T12:00:00+00:00" }
        }
      ],
      last_actions: [
        {
          id: 9,
          ts: "2026-09-27T02:00:00Z",
          action: "optimize",
          status: "succeeded",
          engine: "trino",
          duration_ms: 1200,
          actual_bytes_freed: null,
          input_files_count: 12,
          output_files_count: 3
        }
      ]
    });
    renderTab();

    expect(await screen.findByRole("heading", { name: "Not eligible for nightly selection" })).toBeInTheDocument();
    expect(screen.getByText("Table is inactive (is_active=false)")).toBeInTheDocument();
    expect(screen.getByText("Stats are older than 3 days")).toBeInTheDocument();
    expect(screen.getByText("is_active").closest("div")?.textContent).toContain("false");
    const scanAt = screen.getByText("scan_at").closest("div");
    expect(scanAt?.textContent).toContain("Unknown");
    expect(scanAt?.textContent).not.toMatch(/0 GB|\b0\b/);
    expect(screen.getByText("max_stats_age_days").closest("div")?.textContent).toContain("3");
    expect(screen.getByText("cutoff").closest("div")?.textContent).not.toContain("2026-09-25T12:00:00");
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.getByText("2 gates must clear first.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("expire_snapshots")).not.toBeInTheDocument();
  });
});
