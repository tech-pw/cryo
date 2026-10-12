import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { VIEWER_REASON } from "../../lib/permissions";
import type { TableRead } from "../../api/types";
import { CostOverviewTab } from "./CostOverviewTab";

const authRole = vi.hoisted(() => ({ current: "operator" as "viewer" | "operator" | "admin" }));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: authRole.current,
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../../lib/runtimeFlags", () => ({
  getRuntimeFlags: vi.fn().mockResolvedValue({
    native_engine_enabled: false,
    maintenance_writes_enabled: true,
    native_engine_fallback: "trino"
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

import { getCostDashboard, listTables, savingsOpportunityFilter } from "../../api/client";

const renderTab = (initialEntry = "/cost") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CryoTooltipProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <CostOverviewTab active />
        </MemoryRouter>
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

const dashboard = {
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
  trend: [] as Array<{ scan_at: string; projected_savings_usd_month: number | null; realized_usd_month: number | null }>
};

const opportunityRow = (id: string, tableName: string, reclaimable: number | null) => ({
  id,
  source_id: "src-1",
  schema_name: "gold",
  table_name: tableName,
  first_seen_at: "2026-01-01T00:00:00Z",
  is_active: true,
  stats: {
    grade: "C",
    total_size_gb: 12,
    reclaimable_bytes: reclaimable,
    scan_at: "2026-09-01T00:00:00Z"
  }
}) as TableRead;

describe("CostOverviewTab", () => {
  beforeEach(() => {
    authRole.current = "operator";
    vi.mocked(listTables).mockReset();
    vi.mocked(listTables).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    vi.mocked(getCostDashboard).mockReset();
  });

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

  it("does not invent a saved-to-date dollar on Storage", async () => {
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

    expect(await screen.findByText("Estimated storage cost")).toBeInTheDocument();
    expect(screen.queryByText("Saved to date")).not.toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
  });

  it("renders the opportunity page from the same filter as the candidate count", async () => {
    vi.mocked(getCostDashboard).mockResolvedValue(dashboard);
    vi.mocked(listTables).mockResolvedValue({
      items: [opportunityRow("t-orders", "orders", 2 * 1024 ** 3), opportunityRow("t-events", "events", null)],
      total: 3,
      limit: 50,
      offset: 0
    });
    renderTab();

    expect(await screen.findByRole("link", { name: "gold.orders" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "gold.events" })).toBeInTheDocument();
    expect(screen.queryByText("No savings opportunities")).not.toBeInTheDocument();
    expect(screen.getByText("2 of 3 candidates")).toBeInTheDocument();

    const orders = screen.getByRole("row", { name: /gold\.orders/ });
    expect(within(orders).getByText("12 GB")).toBeInTheDocument();
    expect(within(orders).getByText("2 GB")).toBeInTheDocument();

    const events = screen.getByRole("row", { name: /gold\.events/ });
    expect(within(events).getByText("Not measured")).toBeInTheDocument();
    expect(within(events).queryByText("0 GB")).not.toBeInTheDocument();
    expect(within(events).queryByText("0 MB")).not.toBeInTheDocument();

    expect(listTables).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        predicate: savingsOpportunityFilter,
        limit: 50,
        offset: 0,
        sort: "-reclaimable_bytes"
      })
    );
    expect(listTables).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ limit: 500 }));
  });

  it("labels an unknown opportunity total as Not measured", async () => {
    vi.mocked(getCostDashboard).mockResolvedValue(dashboard);
    vi.mocked(listTables).mockResolvedValue({ items: [], total: null as unknown as number, limit: 50, offset: 0 });
    renderTab();

    expect(await screen.findByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0 candidates")).not.toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
  });

  it("pages opportunities from oppOffset without downloading the fleet", async () => {
    vi.mocked(getCostDashboard).mockResolvedValue(dashboard);
    vi.mocked(listTables).mockResolvedValue({
      items: [opportunityRow("t-late", "late", 1024 ** 3)],
      total: 60,
      limit: 50,
      offset: 50
    });
    renderTab("/cost?oppOffset=50");

    expect(await screen.findByRole("link", { name: "gold.late" })).toBeInTheDocument();
    expect(screen.getByText("1 of 60 candidates")).toBeInTheDocument();
    expect(listTables).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        predicate: savingsOpportunityFilter,
        limit: 50,
        offset: 50
      })
    );
  });

  it("disables write controls for a viewer", async () => {
    authRole.current = "viewer";
    vi.mocked(getCostDashboard).mockResolvedValue(dashboard);
    vi.mocked(listTables).mockResolvedValue({
      items: [opportunityRow("t-orders", "orders", 2 * 1024 ** 3), opportunityRow("t-events", "events", null)],
      total: 3,
      limit: 50,
      offset: 0
    });
    renderTab();

    const scan = await screen.findByRole("button", { name: "Run full cost scan" });
    expect(scan).toBeDisabled();
    expect(screen.getByText(VIEWER_REASON)).toBeInTheDocument();

    const [selectRow] = await screen.findAllByRole("checkbox", { name: "Select row" });
    fireEvent.click(selectRow);
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Assign preset" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run now" })).toHaveAttribute("title", VIEWER_REASON);
    expect(screen.getByRole("button", { name: "Assign preset" })).toHaveAttribute("title", VIEWER_REASON);
  });
});
