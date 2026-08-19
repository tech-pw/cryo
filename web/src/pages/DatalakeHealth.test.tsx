import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DatalakeHealth } from "./DatalakeHealth";

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
  listTables: vi.fn()
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
});
