import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { runMaintenance, triggerScan } from "../api/client";
import { TableDetail } from "./TableDetail";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../components/TableAiInsights", () => ({
  TableAiInsights: () => <div>Mock AI insights</div>
}));

vi.mock("../components/TableCompactionAnalytics", () => ({
  TableCompactionAnalytics: () => null
}));

vi.mock("../components/PlanPreview", () => ({
  PlanPreview: () => null
}));

vi.mock("../api/client", () => ({
  getTable: vi.fn().mockResolvedValue({
    id: "table-1",
    source_id: "source-1",
    schema_name: "public",
    table_name: "events",
    first_seen_at: "2026-07-01T00:00:00Z",
    is_active: true,
    stats: {
      grade: "B",
      overall_score: 82,
      total_size_gb: 12,
      total_files: 100,
      small_files_count: 20,
      cost_precision: "metadata",
      cost_last_full_scan_at: "2026-06-01T00:00:00Z"
    }
  }),
  getTablePartitions: vi.fn().mockResolvedValue([]),
  getTableAudit: vi.fn().mockResolvedValue([]),
  getTableHistory: vi.fn().mockResolvedValue([]),
  getTableHealthTrends: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "events",
    current_grade: "B",
    current_score: 82,
    trend_points: []
  }),
  getEffectivePreset: vi.fn().mockResolvedValue({ preset_id: null, preset_name: null, provenance: "none" }),
  getTableCost: vi.fn().mockResolvedValue({
    table_id: "table-1",
    precision: "metadata",
    price_per_gb_month: 0.023,
    current_monthly_cost_usd: 10,
    projected_monthly_cost_usd: 8,
    projected_savings_usd_month: 2,
    query_request_savings_usd_month: 0,
    live_bytes: 1,
    dead_bytes: 0,
    metadata_bytes: 0,
    reclaimable_bytes: 0,
    by_operation: [],
    last_full_cost_scan_at: "2026-06-01T00:00:00Z"
  }),
  listPresets: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([
    {
      id: "dest-1",
      name: "trino",
      engine: "trino",
      config: {},
      enabled: true,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z"
    }
  ]),
  getComplianceStatus: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_ref: "iceberg.public.events",
    pending_deletion: null,
    abac_policy_count: 0,
    abac_enforcement_note: null
  }),
  getTableSignals: vi.fn().mockResolvedValue({
    table_id: "table-1",
    checked_at: "2026-07-01T00:00:00Z",
    signals: [],
    any_triggered: false
  }),
  assignTables: vi.fn(),
  unassignTables: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  protectSnapshot: vi.fn(),
  triggerScan: vi.fn()
}));

const renderTableDetail = (initialEntry = "/tables/table-1") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/tables/:tableId" element={<TableDetail />} />
          <Route path="/scans/:runId" element={<div>Scan run page</div>} />
          <Route path="/runs/:runId" element={<div>Maintenance run page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return view;
};

describe("TableDetail page", () => {
  beforeEach(() => {
    vi.mocked(triggerScan).mockResolvedValue({
      run_id: "scan-run-1",
      status: "accepted",
      scope: "tables",
      queued: true,
      message: "Scan queued"
    });
    vi.mocked(runMaintenance).mockResolvedValue({
      run_id: "maintenance-run-1",
      job_ids: ["job-1"],
      status: "queued"
    });
  });

  it("renders table detail and cost card", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();
    expect(await screen.findByText("Cost & Savings")).toBeInTheDocument();
    expect(await screen.findByText("Health Score")).toBeInTheDocument();
  });

  it("queues scan without redirecting until follow-up click", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Scan table" }));

    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(screen.queryByText("Scan run page")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open scan run" }));
    expect(await screen.findByText("Scan run page")).toBeInTheDocument();
  });

  it("queues maintenance run without redirecting until follow-up click", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run maintenance" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(runMaintenance).toHaveBeenCalled();
    });
    expect(screen.queryByText("Maintenance run page")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open maintenance run" }));
    expect(await screen.findByText("Maintenance run page")).toBeInTheDocument();
  });
});
