import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getDatalakeDashboard, getFleetTrends, listTables } from "../api/client";
import { getCryoVitals } from "../api/vitals";
import {
  DatalakeHealth,
  TrendPointDot,
  notGradedCount,
  trendPointMarker,
  trendTooltipLabel
} from "./DatalakeHealth";

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

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    detail?: string;
    status?: number;
  },
  getDatalakeDashboard: vi.fn().mockResolvedValue({
    totals: {
      tables: 2,
      size_gb: 10,
      small_files: 100,
      monthly_cost_usd: 50,
      needs_compaction: 1
    },
    grade_distribution: [
      { grade: "B", count: 1 },
      { grade: "D", count: 1 }
    ],
    freshness_distribution: [
      { key: "within_24h", label: "Updated in last 24 hours", count: 1 },
      { key: "3_7d", label: "Not updated 3-7 days", count: 1 },
      { key: "failed", label: "Failed / corrupt tables", count: 0 }
    ],
    freshness_as_of: "2026-08-04T12:00:00Z",
    top_offenders: [],
    cost_by_schema: [],
    drift: []
  }),
  getFleetTrends: vi.fn().mockResolvedValue({
    days: 30,
    health_trend: [],
    cost_trend: []
  }),
  getSmartMaintenanceOverview: vi.fn().mockResolvedValue(null),
  getFastPathFindings: vi.fn().mockResolvedValue([]),
  listTables: vi.fn(),
  listRuns: vi.fn().mockResolvedValue([])
}));

vi.mock("../api/vitals", () => ({
  getCryoVitals: vi.fn().mockResolvedValue({
    score: 91,
    status: "HEALTHY",
    deductions: [{ reason: "dispatch_heartbeat: stalled", points: 8 }],
    impact: { measurement_state: "unmeasured", gb_freed_yesterday: null }
  })
}));

describe("DatalakeHealth page", () => {
  it("renders the dashboard heading and metric tiles", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByText("Datalake health")).toBeInTheDocument();
    expect(await screen.findByText("Tracked tables")).toBeInTheDocument();
    expect(screen.getByText("Tables at grade D/F")).toBeInTheDocument();
  });

  it("scopes the Catalog deep links to the exact offenders shown (grade D/F, needs-compaction)", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByText("Tracked tables");
    // Two "Fix in catalog" hints exist (grade D/F tile, needs-compaction tile) -- each must
    // carry its own scoped filter, not a generic, unscoped Catalog link.
    const fixLinks = await screen.findAllByRole("link", { name: /fix in catalog/i });
    expect(fixLinks).toHaveLength(2);
    expect(fixLinks.map((link) => link.getAttribute("href"))).toEqual(
      expect.arrayContaining(["/catalogs?grades=D,F", "/catalogs?needsCompaction=1"])
    );

    // The primary header CTA is also scoped, not a bare "/catalogs".
    const fixTopOffenders = screen.getByRole("link", { name: /fix top offenders/i });
    expect(fixTopOffenders).toHaveAttribute("href", "/catalogs?needsCompaction=1");
  });

  it("announces the offender counts in a hidden, accessible live region", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByText("Tracked tables");
    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
    await waitFor(() => expect(liveRegion).toHaveTextContent(/tables at grade D or F/i));
    expect(liveRegion).toHaveTextContent(/tables need compaction/i);
  });

  it("does not duplicate the same KPI across the top and Health metric grids", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByText("Tracked tables");
    // "Tracked tables" (top grid) must not be repeated as its own tile inside the "Health" grid.
    expect(screen.getAllByText("Tracked tables")).toHaveLength(1);
    expect(screen.getAllByText("Tables at grade D/F")).toHaveLength(1);
  });

  it("renders freshness counts with keyboard-operable Catalog drill-downs", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Table freshness and staleness")).toBeInTheDocument();
    const staleLink = screen.getByRole("link", { name: "Not updated 3-7 days" });
    expect(staleLink).toHaveAttribute(
      "href",
      "/catalogs?freshness=3_7d&freshnessAsOf=2026-08-04T12%3A00%3A00Z"
    );
    expect(screen.getByText(/mutually exclusive snapshot freshness bucket/i)).toBeInTheDocument();
    const chartBarLink = screen.getByRole("link", {
      name: "Not updated 3-7 days: 1 table. Open matching tables."
    });
    expect(chartBarLink).toHaveAttribute(
      "href",
      "/catalogs?freshness=3_7d&freshnessAsOf=2026-08-04T12%3A00%3A00Z"
    );
    chartBarLink.focus();
    expect(chartBarLink).toHaveFocus();
  });

  it("shows an empty recently-discovered card when there are no fast-path findings", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Recently discovered")).toBeInTheDocument();
    expect(screen.getByText("No recently discovered flags")).toBeInTheDocument();
  });

  it("does not turn a missing reclaimable total into 0 GB", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Reclaimable storage")).toBeInTheDocument();
    const tile = screen.getByText("Reclaimable storage").closest(".cryo-metric-tile");
    expect(tile).toHaveTextContent("Not measured");
    expect(tile).not.toHaveTextContent("0 GB");
    const savings = screen.getByText("Monthly savings").closest(".cryo-metric-tile");
    expect(savings).toHaveTextContent("Cost data unavailable");
    expect(savings).not.toHaveTextContent("$0");
  });

  it("shows Dashboard unavailable with retry instead of a 500-table fleet total", async () => {
    vi.mocked(getDatalakeDashboard).mockRejectedValue(new Error("dashboard down"));
    vi.mocked(listTables).mockClear();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Dashboard unavailable", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Tracked tables")).not.toBeInTheDocument();
    expect(listTables).not.toHaveBeenCalled();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();

    vi.mocked(getDatalakeDashboard).mockResolvedValue({
      totals: {
        tables: 2,
        size_gb: 10,
        small_files: 100,
        monthly_cost_usd: 50,
        needs_compaction: 1
      },
      grade_distribution: [
        { grade: "B", count: 1 },
        { grade: "D", count: 1 }
      ],
      freshness_distribution: [],
      freshness_as_of: "2026-08-04T12:00:00Z",
      top_offenders: [],
      cost_by_schema: [],
      drift: []
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Tracked tables")).toBeInTheDocument();
  });

  it("labels modeled S3 cost and shows a Not graded segment for ungraded tracked tables", async () => {
    vi.mocked(getDatalakeDashboard).mockResolvedValue({
      totals: {
        tables: 5,
        size_gb: 10,
        small_files: 100,
        monthly_cost_usd: 50,
        needs_compaction: 1
      },
      grade_distribution: [
        { grade: "B", count: 1 },
        { grade: "D", count: 1 }
      ],
      freshness_distribution: [],
      freshness_as_of: "2026-08-04T12:00:00Z",
      top_offenders: [],
      cost_by_schema: [],
      drift: []
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Not graded 3")).toBeInTheDocument();
    expect(notGradedCount(5, [{ count: 1 }, { count: 1 }])).toBe(3);
    expect(notGradedCount(null, [{ count: 1 }])).toBeNull();
    const modeled = screen.getByText("Modeled");
    expect(modeled).toHaveAttribute(
      "title",
      "Modeled from each table's cost row when one exists, otherwise the scanner's estimated monthly S3 cost, summed over active tables."
    );
    expect(screen.getByText("S3 monthly now")).toBeInTheDocument();
    expect(screen.getByTitle("Every table row Cryo tracks.")).toBeInTheDocument();

    vi.mocked(getDatalakeDashboard).mockResolvedValue({
      totals: {
        tables: 2,
        size_gb: 10,
        small_files: 100,
        monthly_cost_usd: 50,
        needs_compaction: 1
      },
      grade_distribution: [
        { grade: "B", count: 1 },
        { grade: "D", count: 1 }
      ],
      freshness_distribution: [],
      freshness_as_of: "2026-08-04T12:00:00Z",
      top_offenders: [],
      cost_by_schema: [],
      drift: []
    });
  });

  it("labels a partial fleet trend point and does not zero-fill tables counted", async () => {
    vi.mocked(getFleetTrends).mockResolvedValue({
      days: 30,
      health_trend: [
        { day: "2026-09-27", avg_health_score: 80, tables_at_grade_df: 1, is_partial: false, tables_counted: 10 },
        { day: "2026-09-28", avg_health_score: 70, tables_at_grade_df: 2, is_partial: true, tables_counted: 4 }
      ],
      cost_trend: [
        {
          day: "2026-09-27",
          total_monthly_cost_usd: 10,
          projected_savings_usd_month: 1,
          is_partial: false,
          tables_counted: 10
        },
        {
          day: "2026-09-28",
          total_monthly_cost_usd: 12,
          projected_savings_usd_month: 1,
          is_partial: true
        }
      ]
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect((await screen.findAllByText(/partial/i)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tables counted").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(trendPointMarker(true)).toBe("hollow");
    expect(trendPointMarker(false)).toBe("solid");
    expect(trendTooltipLabel("Sep 28", true)).toBe("Sep 28 (partial)");
    const { container } = render(<TrendPointDot cx={8} cy={8} stroke="#277abe" payload={{ is_partial: true }} />);
    expect(container.querySelector("circle")).toHaveAttribute("fill", "none");
    const solid = render(<TrendPointDot cx={8} cy={8} stroke="#277abe" payload={{ is_partial: false }} />);
    expect(solid.container.querySelector("circle")).toBeNull();

    vi.mocked(getFleetTrends).mockResolvedValue({ days: 30, health_trend: [], cost_trend: [] });
  });

  it("does not show 0 GB for an unmeasured reclaim day on the Now strip", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const now = await screen.findByRole("region", { name: "Now" });
    expect(now).toHaveTextContent("Reclaim yesterday");
    await waitFor(() => expect(now).toHaveTextContent("Not measured"));
    expect(now).not.toHaveTextContent("0 GB");
    fireEvent.click(await screen.findByRole("button", { name: /91/ }));
    expect(await screen.findByText("dispatch_heartbeat: stalled (8)")).toBeInTheDocument();
    expect(vi.mocked(getCryoVitals)).toHaveBeenCalled();
  });
});
