import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QualityTabPanel } from "./QualityTabPanel";

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "operator" })
}));

vi.mock("../../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: true, hidden: false, reason: null })
}));

vi.mock("../../../pages/table-detail/TableDetailContext", () => ({
  useTableDetailContext: () => ({ tableId: "table-1" })
}));

vi.mock("../../TableAiInsights", () => ({
  TableAiInsights: () => <div>Experimental insights</div>
}));

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>();
  return {
    ...actual,
    getTableChecklist: vi.fn().mockResolvedValue({
      table_id: "table-1",
      checked_at: "2026-09-28T00:00:00Z",
      items: [],
      summary: {
        counts: { green: 2, yellow: 0, red: 0, unknown: 1 },
        weighted_score: 40
      }
    }),
    getSlaStatusDashboard: vi.fn().mockResolvedValue({
      slas: [
        {
          table_id: "table-1",
          table_name: "events",
          schema_name: "raw",
          freshness_hours: null,
          freshness_sla_hours: 24,
          freshness_breached: false,
          scan_staleness_hours: null,
          maintenance_days: null,
          maintenance_sla_days: null,
          maintenance_breached: false,
          p95_latency_ms: null,
          p95_latency_sla_ms: null,
          p95_latency_breached: false,
          has_preset: true
        }
      ],
      total_breached: 0,
      total_monitored: 1
    }),
    getDataQualityProfile: vi.fn().mockResolvedValue({
      table_id: "table-1",
      table_name: "events",
      profiled_at: "2026-09-28T00:00:00Z",
      column_metrics: [{ column_name: "should-not-render-null-rate", null_rate: 0.4 }],
      anomalies_detected: 1,
      schema_version_changed: false,
      new_columns: ["added_col"],
      dropped_columns: [],
      commit_signals: null
    }),
    listQualityConstraints: vi.fn().mockResolvedValue({ table_id: "table-1", constraints: [], columns: [] })
  };
});

describe("QualityTabPanel", () => {
  it("labels the checklist score and does not call unknown checks all green", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <QualityTabPanel tableId="table-1" />
      </QueryClientProvider>
    );

    expect(await screen.findByText(/Checklist score/)).toBeInTheDocument();
    expect(screen.getByText("2 checked, 1 unknown")).toBeInTheDocument();
    expect(screen.queryByText(/All green/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/health grade/i)).not.toBeInTheDocument();
    expect(screen.queryByText("should-not-render-null-rate")).not.toBeInTheDocument();
    expect(screen.getByText(/added_col/)).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    const experimental = screen.getByText("Experimental").closest("details");
    expect(experimental).not.toHaveAttribute("open");
  });
});
