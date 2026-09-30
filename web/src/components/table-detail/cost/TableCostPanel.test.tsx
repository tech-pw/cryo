import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { TableCostPanel } from "./TableCostPanel";

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "viewer" })
}));

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>();
  return {
    ...actual,
    getTableCost: vi.fn().mockResolvedValue({
      table_id: "table-1",
      precision: "heuristic",
      price_per_gb_month: 0.023,
      current_monthly_cost_usd: 12,
      projected_monthly_cost_usd: 8,
      projected_savings_usd_month: 4,
      query_request_savings_usd_month: 0,
      live_bytes: 1000,
      dead_bytes: 200,
      metadata_bytes: 50,
      orphan_bytes: null,
      reclaimable_bytes: 200,
      by_operation: [{ operation: "optimize", savings_usd_month: 4, reclaimable_bytes: 200 }],
      request_cost_usd_month: null,
      get_requests_30d: null,
      put_requests_30d: null,
      list_requests_30d: null
    }),
    getTable: vi.fn().mockResolvedValue({
      id: "table-1",
      stats: { raw: {} }
    }),
    getTableS3Tiering: vi.fn().mockResolvedValue({
      table_id: "table-1",
      table_name: "events",
      current_size_gb: 1,
      avg_file_size_kb: 10,
      days_since_last_access: 90,
      current_monthly_cost_usd: 5,
      intelligent_tiering_monthly_cost_usd: 3,
      estimated_annual_savings_usd: 24,
      recommendation: "Move to Intelligent-Tiering",
      reason: "Objects have not been read recently"
    }),
    getCompactionCandidates: vi.fn().mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-09-28T00:00:00Z",
      candidates: [
        {
          partition_path: "dt=do-not-rank/",
          partition_size_gb: 1,
          small_files: 4,
          query_freq_per_day: 1,
          p95_latency_ms: 10,
          score: 0.4,
          priority: "HIGH",
          savings_estimate_usd: 9,
          reasons: ["small files"]
        }
      ]
    })
  };
});

describe("TableCostPanel", () => {
  it("labels dollars and does not rank compaction candidates", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <TableCostPanel tableId="table-1" />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Estimated storage / month")).toBeInTheDocument();
    expect(screen.getByText("$12")).toBeInTheDocument();
    expect(screen.getByText("S3 request cost · 30 days")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(await screen.findByText("Delete-file bytes")).toBeInTheDocument();
    expect(screen.getAllByText(/Modelled/).length).toBeGreaterThan(0);
    expect(await screen.findByText("Move to Intelligent-Tiering")).toBeInTheDocument();
    expect(screen.getByText("Objects have not been read recently")).toBeInTheDocument();
    expect(screen.queryByText("dt=do-not-rank/")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open compaction ranking" })).toHaveAttribute(
      "href",
      "/tables/table-1?tab=maintenance#maintenance-compaction"
    );
  });
});
