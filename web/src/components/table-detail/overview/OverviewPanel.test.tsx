import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OperationLauncherProvider } from "../operations/OperationLauncher";
import { OverviewPanel } from "./OverviewPanel";

const mocks = vi.hoisted(() => ({
  getTable: vi.fn(),
  getTableSignals: vi.fn(),
  getTableAudit: vi.fn(),
  getTableHealthTrends: vi.fn(),
  getFastPathFindings: vi.fn(),
  getComplianceStatus: vi.fn(),
  getEffectivePreset: vi.fn(),
  getTableMaintenanceStatus: vi.fn(),
  getTableCost: vi.fn(),
  listPresets: vi.fn(),
  getEffectiveParams: vi.fn(),
  runTableOperations: vi.fn(),
  getTableChecklist: vi.fn(),
  getTableMaintenanceImpact: vi.fn()
}));

vi.mock("../../../api/client", () => ({
  getTable: mocks.getTable,
  getTableSignals: mocks.getTableSignals,
  getTableAudit: mocks.getTableAudit,
  getTableHealthTrends: mocks.getTableHealthTrends,
  getFastPathFindings: mocks.getFastPathFindings,
  getComplianceStatus: mocks.getComplianceStatus,
  getEffectivePreset: mocks.getEffectivePreset,
  getTableCost: mocks.getTableCost,
  listPresets: mocks.listPresets,
  getEffectiveParams: mocks.getEffectiveParams,
  runTableOperations: mocks.runTableOperations,
  getTableChecklist: mocks.getTableChecklist,
  getTableMaintenanceImpact: mocks.getTableMaintenanceImpact
}));

vi.mock("../../../api/maintenanceStatus", () => ({
  getTableMaintenanceStatus: mocks.getTableMaintenanceStatus
}));

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "operator"
  })
}));

vi.mock("../../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: true, hidden: false, reason: null })
}));

const api = { token: "test-token" };

const unscanned = () => ({
  id: "table-1",
  source_id: "source-1",
  schema_name: "analytics",
  table_name: "events_daily",
  first_seen_at: "2026-09-01T00:00:00Z",
  is_active: true,
  owner_email: "data-eng@example.com",
  stats: null
});

const scanned = (stats?: Record<string, unknown>) => ({
  ...unscanned(),
  stats: {
    scan_at: "2026-09-28T09:10:00Z",
    grade: "C",
    overall_score: 72,
    raw: { health_deductions: [] },
    ...stats
  }
});

const emptyWindow = (days: number | null) => ({
  days,
  since: null,
  ops: 0,
  ops_with_bytes: 0,
  ops_with_files: 0,
  bytes_freed: 0,
  files_in: 0,
  files_out: 0,
  by_operation: []
});

const renderPanel = (props?: { onScan?: () => void; onPreview?: () => void; onEdit?: () => void }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tables/table-1"]}>
        <Routes>
          <Route
            path="/tables/:tableId"
            element={
              <OperationLauncherProvider tableId="table-1" tableName="events_daily">
                <OverviewPanel {...props} />
              </OperationLauncherProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("OverviewPanel", () => {
  beforeEach(() => {
    mocks.getTable.mockReset().mockResolvedValue(unscanned());
    mocks.getTableMaintenanceImpact.mockReset().mockResolvedValue({
      table_id: "table-1",
      recent: emptyWindow(30),
      lifetime: emptyWindow(null)
    });
    mocks.getTableChecklist.mockReset().mockResolvedValue({
      table_id: "table-1",
      checked_at: "2026-09-28T09:10:00Z",
      items: [
        { key: "distribution_mode", label: "write.distribution-mode set", weight_pct: 5, tier: 2, status: "yellow", reason: "write.distribution-mode='none'", metric_value: null, threshold: null },
        { key: "sort_order", label: "Sort order matches filter columns", weight_pct: 6, tier: 2, status: "green", reason: "Sort order configured", metric_value: null, threshold: null }
      ],
      summary: { counts: { green: 1, yellow: 1, red: 0, unknown: 0 }, weighted_score: 49 }
    });
    mocks.getTableSignals.mockReset().mockResolvedValue({ table_id: "table-1", checked_at: "", signals: [], any_triggered: false });
    mocks.getTableAudit.mockReset().mockResolvedValue([]);
    mocks.getTableHealthTrends.mockReset().mockResolvedValue({ trend_points: [] });
    mocks.getFastPathFindings.mockReset().mockResolvedValue([]);
    mocks.getComplianceStatus.mockReset().mockResolvedValue({
      table_id: "table-1",
      table_ref: "iceberg.analytics.events_daily",
      pending_deletion: null,
      abac_policy_count: 1,
      abac_enforcement_note: null
    });
    mocks.getEffectivePreset.mockReset().mockResolvedValue({
      table_id: "table-1",
      preset_id: "preset-1",
      preset_name: "nightly-standard",
      provenance: "assignment"
    });
    mocks.getTableMaintenanceStatus.mockReset().mockResolvedValue({
      table_id: "table-1",
      eligible: false,
      evaluated_at: "2026-09-28T12:00:00Z",
      eligibility_basis: "Nightly batch.",
      blocking_reasons: [
        {
          code: "cooldown",
          summary: "Cooldown until 2026-09-29T02:00:00+00:00",
          predicate: "load_candidates",
          blocks_nightly_selection: true,
          until: "2026-09-29T02:00:00Z",
          values: {}
        }
      ],
      thresholds: { cooldown_hours: 72, max_stats_age_days: 3, min_delete_file_pct: 1 },
      last_actions: [],
      last_actions_note: "",
      next_nightly_at: null,
      next_nightly_note: "not recorded",
      omitted: []
    });
    mocks.getTableCost.mockReset().mockResolvedValue({ table_id: "table-1", orphan_bytes: null });
    mocks.listPresets.mockReset().mockResolvedValue([]);
    mocks.getEffectiveParams.mockReset().mockResolvedValue({
      table_id: "table-1",
      preset_params: {},
      param_overrides: {},
      effective_params: { retention_days: 5 }
    });
    mocks.runTableOperations.mockReset().mockResolvedValue({ run_id: "run-7", job_ids: ["job-1"], status: "queued" });
  });

  it("hides sections 2-6 until the table has been scanned", async () => {
    const onScan = vi.fn();
    renderPanel({ onScan });

    expect(await screen.findByText("Needs a first scan")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scan now" }));
    expect(onScan).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "Run maintenance" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Health over time" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "History & activity" })).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mocks.getTable).toHaveBeenCalledWith(api, "table-1");
      expect(mocks.getTableMaintenanceStatus).not.toHaveBeenCalled();
      expect(mocks.getTableAudit).not.toHaveBeenCalled();
      expect(mocks.getFastPathFindings).not.toHaveBeenCalled();
      expect(mocks.getTableSignals).not.toHaveBeenCalled();
      expect(mocks.getTableHealthTrends).not.toHaveBeenCalled();
      expect(mocks.getComplianceStatus).not.toHaveBeenCalled();
    });
  });

  it("does not claim health checks passed when the breakdown is missing", async () => {
    mocks.getTable.mockResolvedValue(scanned({ raw: {} }));
    mocks.getTableMaintenanceStatus.mockRejectedValue(new Error("Unavailable"));
    renderPanel();
    expect(await screen.findByText("Score explanation unavailable")).toBeInTheDocument();
    expect(await screen.findByText("Selection status unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/Every health check passed/)).not.toBeInTheDocument();
    expect(screen.queryByText("Not currently eligible")).not.toBeInTheDocument();
  });

  it("ranks deductions and pairs each with the action that fixes it", async () => {
    mocks.getTable.mockResolvedValue(
      scanned({
        raw: {
          health_deductions: [
            { label: "Snapshot retention buildup", deduction: 8, severity: "warning", reason: "123 snapshots" },
            { label: "Small-file fragmentation", deduction: 20, severity: "critical", reason: "56% small files" },
            { label: "High velocity streaming", deduction: 8, severity: "warning", reason: "24 commits/day" }
          ]
        }
      })
    );
    mocks.getFastPathFindings.mockResolvedValue([
      {
        table_id: "table-1",
        table_name: "events_daily",
        finding_type: "missing_target_file_size",
        reason: "No target file size",
        severity: "warning",
        detected_at: "2026-09-28T09:00:00Z"
      }
    ]);
    renderPanel();

    expect(await screen.findByText("Small-file fragmentation")).toBeInTheDocument();
    expect(screen.getByText("−36 points", { exact: false })).toBeInTheDocument();
    const order = [...document.querySelectorAll(".td-deductions li b")].map((node) => node.textContent);
    expect(order[0]).toBe("Small-file fragmentation");
    expect(screen.getByText("critical")).toBeInTheDocument();
    expect(screen.getByText(/Write pattern/)).toBeInTheDocument();
    expect(screen.queryByText("No target file size")).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Change in Config/ })).toHaveAttribute(
      "href",
      "/tables/table-1?tab=config#config-properties"
    );

    const fix = document.querySelectorAll(".td-deductions li")[1].querySelector("button");
    expect(fix?.textContent).toBe("Expire snapshots");
  });

  it("lists structural design findings unscored and explains an unchanged grade (#264)", async () => {
    mocks.getTable.mockResolvedValue(
      scanned({
        grade: "F",
        raw: {
          health_deductions: [],
          health_design_findings: [
            {
              label: "Partition volume skew",
              severity: "critical",
              reason: "Skew ratio of 94.0x (above 50x)",
              recommendation: "Choose a partition column with a more even distribution."
            }
          ]
        }
      })
    );
    mocks.getTableHealthTrends.mockResolvedValue({
      trend_points: [
        { timestamp: "2026-09-26T09:00:00Z", score: 40, grade: "F" },
        { timestamp: "2026-09-28T09:00:00Z", score: 40, grade: "F" }
      ]
    });
    mocks.getTableAudit.mockResolvedValue([{ id: 1, ts: "2026-09-27T09:00:00Z", action: "optimize", status: "OK" }]);
    renderPanel();

    expect(await screen.findByRole("heading", { name: "Design findings" })).toBeInTheDocument();
    expect(screen.getByText("Partition volume skew")).toBeInTheDocument();
    expect(screen.getByText(/more even distribution/)).toBeInTheDocument();
    expect(document.querySelectorAll(".td-deductions li")).toHaveLength(0);
    expect(await screen.findByText(/remaining issues are structural/)).toBeInTheDocument();
  });

  it("asks for an explicit acknowledgement before a destructive operation runs", async () => {
    mocks.getTable.mockResolvedValue(
      scanned({
        total_snapshots: 123,
        raw: { health_deductions: [{ label: "Snapshot retention buildup", deduction: 8, severity: "warning", reason: "123 snapshots" }] }
      })
    );
    renderPanel();

    await screen.findByText("Snapshot retention buildup");
    fireEvent.click(document.querySelector(".td-deductions li button") as HTMLButtonElement);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(await screen.findByText(/Keeps snapshots from the last 5 days/)).toBeInTheDocument();
    const run = screen.getByRole("button", { name: "Queue Expire snapshots" });
    expect(run).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /files are deleted/ }));
    expect(run).toBeEnabled();
    fireEvent.click(run);

    await waitFor(() => {
      expect(mocks.runTableOperations).toHaveBeenCalledWith(api, "table-1", { operations: ["expire_snapshots"] });
    });
  });

  it("shows reported before/after file counts and never infers freed bytes", async () => {
    mocks.getTable.mockResolvedValue(scanned());
    mocks.getTableAudit.mockResolvedValue([
      {
        id: 9,
        action: "optimize",
        status: "OK",
        ts: "2026-09-27T02:00:00Z",
        engine: "trino",
        duration_ms: 1200,
        actual_bytes_freed: null,
        bytes_freed: 999,
        input_files_count: 12,
        output_files_count: 3
      }
    ]);
    renderPanel();

    expect(await screen.findByText("12 → 3 files")).toBeInTheDocument();
    expect(screen.queryByText(/freed/)).not.toBeInTheDocument();
    expect(screen.queryByText("999")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.getTableAudit).toHaveBeenCalledWith(api, "table-1", { limit: 40 });
    });
  });

  it("totals only what engines reported and counts silent operations as unmeasured", async () => {
    mocks.getTable.mockResolvedValue(scanned());
    mocks.getTableAudit.mockResolvedValue([]);
    const optimize = { operation: "optimize", ops: 2, ops_with_bytes: 0, ops_with_files: 2, bytes_freed: 0, files_in: 400, files_out: 20 };
    const expire = { operation: "expire_snapshots", ops: 1, ops_with_bytes: 1, ops_with_files: 0, bytes_freed: 3 * 1024 ** 3, files_in: 0, files_out: 0 };
    const orphans = { operation: "remove_orphan_files", ops: 1, ops_with_bytes: 0, ops_with_files: 0, bytes_freed: 0, files_in: 0, files_out: 0 };
    mocks.getTableMaintenanceImpact.mockResolvedValue({
      table_id: "table-1",
      recent: { ...emptyWindow(30), ops: 2, ops_with_files: 2, files_in: 400, files_out: 20, by_operation: [optimize] },
      lifetime: {
        ...emptyWindow(null),
        since: "2026-08-01T00:00:00Z",
        ops: 4,
        ops_with_bytes: 1,
        ops_with_files: 2,
        bytes_freed: 3 * 1024 ** 3,
        files_in: 400,
        files_out: 20,
        by_operation: [optimize, expire, orphans]
      }
    });
    renderPanel();

    expect(await screen.findByText("What maintenance achieved")).toBeInTheDocument();
    expect(screen.getByText(/2 of 4 reported their effect/)).toBeInTheDocument();
    expect(screen.getByText("400 → 20 files")).toBeInTheDocument();
    expect(screen.queryByText("Orphan files")).not.toBeInTheDocument();
  });
});
