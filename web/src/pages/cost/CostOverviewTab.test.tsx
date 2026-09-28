import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { CostOverviewTab } from "./CostOverviewTab";

vi.mock("../../lib/auth", () => ({
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

vi.mock("../../api/client", () => ({
  savingsOpportunityFilter: {},
  getCostDashboard: vi.fn(),
  listTables: vi.fn(),
  listPresets: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([]),
  getCostModelEvaluate: vi.fn().mockResolvedValue(null),
  getSchemaCost: vi.fn().mockResolvedValue([]),
  assignTables: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  triggerCostScan: vi.fn(),
  triggerScan: vi.fn()
}));

import { getCostDashboard, listTables } from "../../api/client";

const renderTab = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CryoTooltipProvider>
        <MemoryRouter>
          <CostOverviewTab />
        </MemoryRouter>
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

describe("CostOverviewTab", () => {
  it("shows Dashboard unavailable instead of a 500-table fleet total, and does not zero-fill saved-to-date", async () => {
    vi.mocked(listTables).mockClear();
    vi.mocked(getCostDashboard).mockRejectedValue(new Error("cost dashboard down"));
    renderTab();

    expect(await screen.findByText("Dashboard unavailable", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Total monthly S3 cost")).not.toBeInTheDocument();
    expect(listTables).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ limit: 500 }));
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
  });

  it("labels a missing saved-to-date figure instead of $0", async () => {
    vi.mocked(getCostDashboard).mockResolvedValue({
      totals: {
        current_monthly_cost_usd: 80,
        projected_monthly_cost_usd: 50,
        projected_savings_usd_month: 30,
        current_storage_bytes: 1000,
        reclaimable_bytes: 100
      },
      savings_by_operation: [],
      cost_by_schema: [],
      top_opportunities: [],
      trend: [
        {
          scan_at: "2026-06-01T00:00:00Z",
          projected_savings_usd_month: 20,
          realized_usd_month: 10
        },
        {
          scan_at: "2026-07-01T00:00:00Z",
          projected_savings_usd_month: null,
          realized_usd_month: null
        }
      ]
    });
    renderTab();

    const saved = (await screen.findByText("Saved to date")).closest(".cryo-metric-tile");
    expect(saved).toHaveTextContent("Cost data unavailable");
    expect(saved).not.toHaveTextContent("$0");
    expect(await screen.findAllByText("Cost data unavailable")).not.toHaveLength(0);
  });
});
