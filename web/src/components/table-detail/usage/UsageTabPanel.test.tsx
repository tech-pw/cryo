import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../ui/tooltip";
import { UsageTabPanel } from "./UsageTabPanel";

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "viewer" })
}));

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>();
  return {
    ...actual,
    getQueryPerformanceDashboard: vi.fn().mockResolvedValue({
      queries: [],
      total: 0,
      p50_latency_ms: null,
      p95_latency_ms: null,
      p99_latency_ms: null,
      avg_bytes_scanned: null,
      failed_count: 0
    }),
    getTablePartitionAccess: vi.fn().mockResolvedValue({
      table_id: "table-1",
      since: "2026-09-01",
      points: [],
      partitions: []
    }),
    getTableLineage: vi.fn().mockResolvedValue({
      root_table_id: "table-1",
      root_table_name: "events",
      upstream: [],
      downstream: []
    }),
    getUnionable: vi.fn().mockResolvedValue({
      target_table_id: "table-1",
      results: [],
      generated_at: "2026-09-28T00:00:00Z"
    })
  };
});

describe("UsageTabPanel", () => {
  it("says no reads observed and never Unused", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CryoTooltipProvider>
          <MemoryRouter>
            <UsageTabPanel tableId="table-1" />
          </MemoryRouter>
        </CryoTooltipProvider>
      </QueryClientProvider>
    );

    expect(await screen.findByText("No query records in this window")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This does not mean the table is unused. Query-log coverage is not measured; check ingestion for this source."
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Unused$/)).not.toBeInTheDocument();
    expect(screen.getByText("Last 7 days")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("No query records in this window")).toBeInTheDocument();
    });
  });
});
