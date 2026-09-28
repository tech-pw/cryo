import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeAfter: null as { bytesUnreported?: boolean; before?: number | null; after?: number | null } | null,
  timeSeries: null as { points?: Array<{ value: number | null }>; events?: Array<{ at: string }> } | null
}));

vi.mock("../../../api/client", () => ({
  getTable: mocks.getTable,
  getTableSignals: mocks.getTableSignals,
  getTableAudit: mocks.getTableAudit,
  getTableHealthTrends: mocks.getTableHealthTrends,
  getFastPathFindings: mocks.getFastPathFindings,
  getComplianceStatus: mocks.getComplianceStatus,
  getEffectivePreset: mocks.getEffectivePreset
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

vi.mock("../../charts/BeforeAfter", () => ({
  BeforeAfter: (props: { bytesUnreported?: boolean; before?: number | null; after?: number | null }) => {
    mocks.beforeAfter = props;
    return <div data-testid="before-after" />;
  }
}));

vi.mock("../../charts/TimeSeries", () => ({
  TimeSeries: (props: { title: string; points?: Array<{ value: number | null }>; events?: Array<{ at: string }> }) => {
    mocks.timeSeries = props;
    return <div>{props.title}</div>;
  }
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

const renderPanel = (props?: { onScan?: () => void; onPreview?: () => void; onEdit?: () => void }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tables/table-1"]}>
        <Routes>
          <Route path="/tables/:tableId" element={<OverviewPanel {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("OverviewPanel", () => {
  beforeEach(() => {
    mocks.beforeAfter = null;
    mocks.timeSeries = null;
    mocks.getTable.mockReset().mockResolvedValue(unscanned());
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
  });

  it("hides sections 2-6 until the table has been scanned", async () => {
    const onScan = vi.fn();
    renderPanel({ onScan });

    expect(await screen.findByText("Needs a first scan")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scan now" }));
    expect(onScan).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "What Cryo will do next" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Last receipt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Most relevant finding" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Score over time" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Ownership & compliance" })).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mocks.getTable).toHaveBeenCalledWith(api, "table-1");
      expect(mocks.getTableMaintenanceStatus).not.toHaveBeenCalled();
      expect(mocks.getTableAudit).not.toHaveBeenCalled();
      expect(mocks.getFastPathFindings).not.toHaveBeenCalled();
      expect(mocks.getTableSignals).not.toHaveBeenCalled();
      expect(mocks.getTableHealthTrends).not.toHaveBeenCalled();
      expect(mocks.getComplianceStatus).not.toHaveBeenCalled();
      expect(mocks.getEffectivePreset).not.toHaveBeenCalled();
    });
  });

  it("renders deduction points from the persisted list", async () => {
    const onPreview = vi.fn();
    mocks.getTable.mockResolvedValue(
      scanned({
        raw: {
          health_deductions: [
            { label: "Snapshots", deduction: 8, severity: "warning", reason: "too many snapshots" },
            {
              label: "Small files",
              deduction: 20,
              severity: "critical",
              reason: "56% of files under target (critical)"
            }
          ]
        }
      })
    );
    mocks.getTableSignals.mockResolvedValue({
      table_id: "table-1",
      checked_at: "2026-09-28T09:10:00Z",
      any_triggered: true,
      signals: [{ triggered: true, signal_name: "query_latency", severity: "warning", reason: "p95 rose", metric_value: 1, threshold: 1 }]
    });
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
    mocks.getTableHealthTrends.mockResolvedValue({
      trend_points: [
        { timestamp: "2026-09-01T00:00:00Z", score: null, grade: null },
        { timestamp: "2026-09-28T09:10:00Z", score: 72, grade: "C" }
      ]
    });
    renderPanel({ onPreview });

    expect(await screen.findByText("−20 Small files: 56% of files under target (critical)")).toBeInTheDocument();
    expect(screen.getByText("−8 Snapshots: too many snapshots")).toBeInTheDocument();
    expect(screen.getByText("100 − 28 = 72")).toBeInTheDocument();
    expect(await screen.findByText("Diagnostic, not part of the score")).toBeInTheDocument();
    expect(screen.getByText("query_latency: p95 rose")).toBeInTheDocument();
    const list = screen.getByText("−20 Small files: 56% of files under target (critical)").closest("ul");
    expect(list?.textContent?.indexOf("−20")).toBeLessThan(list?.textContent?.indexOf("−8") ?? -1);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(onPreview).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "See the rest" })).toHaveAttribute(
      "href",
      "/tables/table-1?tab=files#files-properties"
    );

    await waitFor(() => {
      expect(mocks.getFastPathFindings).toHaveBeenCalledWith(api, "table-1");
      expect(mocks.timeSeries?.points?.some((point) => point.value === null)).toBe(true);
      expect(mocks.timeSeries?.points?.some((point) => point.value === 0)).toBe(false);
    });
  });

  it("says bytes were not reported when actual_bytes_freed is missing", async () => {
    mocks.getTable.mockResolvedValue(scanned());
    mocks.getTableAudit.mockResolvedValue([
      {
        id: 9,
        action: "optimize",
        ts: "2026-09-27T02:00:00Z",
        engine: "trino",
        duration_ms: 1200,
        actual_bytes_freed: null,
        bytes_freed: 999,
        input_files_count: 12,
        output_files_count: 3
      }
    ]);
    mocks.getTableHealthTrends.mockResolvedValue({
      trend_points: [{ timestamp: "2026-09-01T00:00:00Z", score: null, grade: null }]
    });
    renderPanel();

    expect(await screen.findByText("not reported")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("999")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open run" })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mocks.getTableAudit).toHaveBeenCalledWith(api, "table-1", { limit: 1 });
      expect(mocks.beforeAfter?.bytesUnreported).toBe(true);
      expect(mocks.beforeAfter?.before).toBe(12);
      expect(mocks.beforeAfter?.after).toBe(3);
      expect(mocks.timeSeries?.events).toEqual([
        expect.objectContaining({ at: "2026-09-27T02:00:00Z", kind: "maintenance" })
      ]);
    });
  });
});
