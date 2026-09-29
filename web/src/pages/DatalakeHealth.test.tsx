import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getDatalakeDashboard, listPendingApprovals, listTables } from "../api/client";
import { getCryoVitals } from "../api/vitals";
import { DatalakeHealth, notGradedCount, tablesAtGradeDf } from "./DatalakeHealth";

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
  listRuns: vi.fn().mockResolvedValue([]),
  listPendingApprovals: vi.fn().mockResolvedValue([
    {
      id: "approval-1",
      table_id: "t1",
      table_name: "db.t",
      operation_type: "expire_snapshots",
      status: "pending",
      requested_by: "governor",
      impact_preview: null,
      created_at: "2026-09-28T00:00:00Z",
      expires_at: null
    }
  ])
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
    expect(await screen.findByText("Home")).toBeInTheDocument();
    expect(screen.getByText("What happened last night, and is the lake worse?")).toBeInTheDocument();
    expect(await screen.findByText("Tracked tables")).toBeInTheDocument();
    expect(screen.getByText("Tables at grade D/F")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What got worse" })).toBeInTheDocument();
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

    expect(screen.queryByRole("link", { name: /fix top offenders/i })).not.toBeInTheDocument();
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
    expect(screen.queryByText("Monthly savings")).not.toBeInTheDocument();
    expect(screen.queryByText("Top offenders")).not.toBeInTheDocument();
    expect(screen.queryByText("Smart maintenance posture")).not.toBeInTheDocument();
    expect(screen.queryByText("Recent scan trend")).not.toBeInTheDocument();
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
    expect(screen.queryByText("Estimated storage cost")).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Platform" })).toBeInTheDocument();
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

  it("replaces fleet trend charts with a Tables at grade D/F number and does not invent 0 from an empty series", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const tile = (await screen.findByText("Tables at grade D/F")).closest(".cryo-metric-tile");
    expect(tile).toHaveTextContent("1");
    expect(tile).not.toHaveTextContent("Unknown");
    expect(screen.queryByText("Fleet health trend")).not.toBeInTheDocument();
    expect(screen.queryByText("Fleet cost trend")).not.toBeInTheDocument();
    expect(screen.getByText("Table freshness and staleness")).toBeInTheDocument();
    expect(screen.queryByText("Modeled")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Scanner/ })).toHaveAttribute("href", "/runs/scanner");
    expect(screen.getByRole("region", { name: "Now" })).toBeInTheDocument();
    expect(listTables).not.toHaveBeenCalled();
    expect(tablesAtGradeDf(undefined)).toBeNull();
    expect(tablesAtGradeDf([])).toBe(0);
    expect(tablesAtGradeDf([{ grade: "D", count: 1 }])).toBe(1);
  });

  it("shows Unknown for Tables at grade D/F when the grade distribution field is missing", async () => {
    vi.mocked(getDatalakeDashboard).mockResolvedValue({
      totals: {
        tables: 2,
        size_gb: 10,
        small_files: 100,
        monthly_cost_usd: 50,
        needs_compaction: 1
      },
      freshness_distribution: [
        { key: "within_24h", label: "Updated in last 24 hours", count: 1 }
      ],
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

    const tile = (await screen.findByText("Tables at grade D/F")).closest(".cryo-metric-tile");
    expect(tile).toHaveTextContent("Unknown");
    expect(tile).not.toHaveTextContent(/\b0\b/);
    expect(screen.getByRole("status")).toHaveTextContent(/Tables at D\/F unknown/i);
    expect(screen.getByRole("status")).not.toHaveTextContent(/no tables currently at grade D or F/i);
    expect(screen.queryByText("Fleet health trend")).not.toBeInTheDocument();
    expect(screen.queryByText("Fleet cost trend")).not.toBeInTheDocument();

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

  it("counts pending approvals from the approvals client", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const tile = (await screen.findByText("Awaiting a person")).closest(".cryo-metric-tile");
    expect(tile).toHaveAttribute("data-metric-id", "awaiting_person");
    await waitFor(() => expect(tile).toHaveTextContent("1"));
    expect(tile).not.toHaveTextContent("Not recorded");
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/governance");
  });

  it("shows Not recorded instead of zero when pending approvals cannot be loaded", async () => {
    vi.mocked(listPendingApprovals).mockRejectedValue(new Error("approvals down"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const tile = (await screen.findByText("Awaiting a person")).closest(".cryo-metric-tile");
    await waitFor(() => expect(tile).toHaveTextContent("Not recorded"), { timeout: 3000 });
    expect(tile).not.toHaveTextContent(/\b0\b/);

    vi.mocked(listPendingApprovals).mockResolvedValue([
      {
        id: "approval-1",
        table_id: "t1",
        table_name: "db.t",
        operation_type: "expire_snapshots",
        status: "pending",
        requested_by: "governor",
        impact_preview: null,
        created_at: "2026-09-28T00:00:00Z",
        expires_at: null
      }
    ]);
  });

  it("links engine holds without inventing a slot count", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DatalakeHealth />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const section = await screen.findByRole("region", { name: "What needs an engine" });
    expect(screen.getByRole("link", { name: "View holds" })).toHaveAttribute("href", "/runs?tab=holds");
    expect(section).not.toHaveTextContent(/slot/i);
    expect(section).not.toHaveTextContent(/\d/);
  });
});
