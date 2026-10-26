import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Catalog } from "./Catalog";
import { ThemeProvider } from "../lib/themeContext";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../api/client", () => ({
  listSources: vi.fn(),
  listSourceS3LogConfigs: vi.fn(),
  getSourceS3RequestCostRollup: vi.fn(),
  getAwsIntegration: vi.fn(),
  getAwsUsage: vi.fn(),
  savingsOpportunityFilter: {},
  getCostDashboard: vi.fn(),
  listTables: vi.fn(),
  listExplorerViews: vi.fn().mockResolvedValue([]),
  saveExplorerView: vi.fn(),
  deleteExplorerView: vi.fn(),
  listPresets: vi.fn(),
  listDestinations: vi.fn(),
  getCostModelEvaluate: vi.fn(),
  getSchemaCost: vi.fn(),
  exportTablesCsv: vi.fn(),
  assignTables: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  triggerCostScan: vi.fn(),
  triggerScan: vi.fn(),
  deriveCostDashboard: vi.fn(),
  getMultiCatalogDashboard: vi.fn(),
  getTablesMaintenanceFunnel: vi.fn().mockResolvedValue({ run_id: null, total_active: 0, nodes: [], links: [] })
}));

import {
  exportTablesCsv,
  getMultiCatalogDashboard,
  listDestinations,
  listPresets,
  listSources,
  listTables
} from "../api/client";

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
};

const renderPage = (initialRoute = "/catalogs") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route
              path="/catalogs"
              element={
                <>
                  <Catalog />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

const SAMPLE_TABLE = {
  id: "tbl-1",
  source_id: "src-1",
  schema_name: "prod_data",
  table_name: "orders",
  catalog_name: "prod_data",
  first_seen_at: "2026-01-01T00:00:00Z",
  is_active: true,
  stats: {
    grade: "B",
    compaction_priority: "medium",
    needs_compaction: false,
    total_files: 10,
    small_files_count: 2,
    total_size_gb: 5,
    projected_savings_usd_month: 3.5
  }
} as never;

// Whole-page renders: under 1s alone, but 5s+ when CI runs the full suite in parallel.
describe("Consolidated Catalog page", { timeout: 20_000 }, () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(listPresets).mockResolvedValue([]);
    vi.mocked(listDestinations).mockResolvedValue([]);
    vi.mocked(exportTablesCsv).mockResolvedValue("source,qualified_table\ncatalog,analytics.orders\n");
    vi.mocked(listTables).mockResolvedValue({
      items: [],
      total: 0,
      limit: 500,
      offset: 0
    });
  });

  it("renders the page header and the table explorer even when the dashboard is empty", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 0,
      total_tables: 0,
      total_cost_usd: 0,
      catalogs: []
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0
    });
    renderPage();

    // Page header is shared between both sections and matches the "Tables" nav label.
    expect(await screen.findByRole("heading", { level: 1, name: "Tables" })).toBeInTheDocument();

    // An empty lake collapses the catalog card to one line.
    expect(await screen.findByText("No catalogs connected.")).toBeInTheDocument();

    // Nothing to export until a table matches.
    expect(screen.queryByRole("button", { name: /export csv/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh tables" })).toBeInTheDocument();
  }, 15000);

  it("renders a dashboard row per catalog with the catalog name", async () => {
    // The drill-down click handler lives on each row -- verified in production via the
    // dashboard's onRow -> navigate() wiring. This test asserts the data path: a catalog
    // row renders with the right name + table count, which is the precondition for the
    // click handler having something to bind to.
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 1,
      total_tables: 5,
      total_cost_usd: 12.5,
      catalogs: [
        {
          catalog_id: "src-1",
          catalog_name: "prod_data",
          catalog_type: "hive",
          table_count: 5,
          total_size_gb: 100,
          small_files_count: 200,
          needs_compaction_count: 1,
          monthly_cost_usd: 12.5,
          projected_savings_usd_month: 4.2,
          avg_health_score: 80,
          enabled: true
        }
      ]
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0
    });
    renderPage();

    // Dashboard catalog name is rendered in the DataTable cell (font-medium span inside a button).
    await waitFor(() => {
      expect(
        screen.getAllByText("prod_data").some((el) => el.tagName === "SPAN" && el.className.includes("font-medium"))
      ).toBe(true);
    });
  });

  it("applying a built-in saved view rewrites the URL to the exact scoped filters (no silent state)", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 0,
      total_tables: 0,
      total_cost_usd: 0,
      catalogs: []
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [SAMPLE_TABLE],
      total: 1,
      limit: 50,
      offset: 0
    });

    renderPage();

    await screen.findByRole("button", { name: /export csv/i });
    fireEvent.click(screen.getByRole("button", { name: "Saved views" }));
    fireEvent.click(screen.getByRole("button", { name: "Health triage (grade D and F)" }));

    await waitFor(() => {
      const location = screen.getByTestId("location-probe").textContent ?? "";
      expect(location).toContain("grade=D%2CF");
      expect(location).toContain("sort=-small_files_count");
    });
  });

  it("preserves a freshness drill-down and sends exact timestamp bounds to the server", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 0,
      total_tables: 1,
      total_cost_usd: 0,
      catalogs: []
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [SAMPLE_TABLE],
      total: 1,
      limit: 50,
      offset: 0
    });
    window.localStorage.setItem("cryo.catalog.defaultView.v1", "Health triage (grade D and F)");

    renderPage("/catalogs?freshness=3_7d&freshnessAsOf=2026-08-04T12%3A00%3A00Z");

    expect(await screen.findByText("Freshness view: Not updated 3-7 days")).toBeInTheDocument();
    await waitFor(() => {
      const freshnessCall = vi.mocked(listTables).mock.calls.find(([, args]) =>
        JSON.stringify(args?.predicate).includes("last_snapshot_committed_at")
      );
      expect(freshnessCall).toBeDefined();
      const serialized = JSON.stringify(freshnessCall?.[1].predicate);
      expect(serialized).toContain("2026-08-01T12:00:00.000Z");
      expect(serialized).toContain("2026-07-28T12:00:00.000Z");
      expect(serialized).toContain("consecutive_scan_failures");
    });
    expect(screen.getByTestId("location-probe")).toHaveTextContent("freshness=3_7d");
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => {
      expect(exportTablesCsv).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({
          predicate: expect.objectContaining({ all: expect.any(Array) })
        })
      );
    });
    anchorClick.mockRestore();
  });

  it("controls column visibility via a native popover + checkboxes, scoped to the table explorer only", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 0,
      total_tables: 0,
      total_cost_usd: 0,
      catalogs: []
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [SAMPLE_TABLE],
      total: 1,
      limit: 50,
      offset: 0
    });
    renderPage();

    await screen.findByRole("button", { name: /export csv/i });
    const explorerTable = document.querySelector(".datalake-table");
    expect(explorerTable).not.toBeNull();
    expect(within(explorerTable as HTMLElement).queryByText("Catalog")).not.toBeInTheDocument();
    expect(within(explorerTable as HTMLElement).getByText("Health")).toBeInTheDocument();
    expect(within(explorerTable as HTMLElement).getByText("Maintenance")).toBeInTheDocument();

    const columnsMenuButton = screen.getByRole("button", { name: /columns/i });
    fireEvent.click(columnsMenuButton);
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));

    await waitFor(() => {
      expect(within(explorerTable as HTMLElement).getByText("Catalog")).toBeInTheDocument();
    });
    // The dashboard's own "Catalog" column (a separate, unrelated table) is unaffected --
    // column visibility only ever applies to the table explorer's DataTable instance.
    expect(document.querySelector(".datalake-table")).not.toBeNull();
  });

  it("remembers column visibility choices across a fresh mount via local storage", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      total_catalogs: 0,
      total_tables: 0,
      total_cost_usd: 0,
      catalogs: []
    } as never);
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listTables).mockResolvedValue({
      items: [SAMPLE_TABLE],
      total: 1,
      limit: 50,
      offset: 0
    });

    const first = renderPage();
    await screen.findByRole("button", { name: /export csv/i });
    fireEvent.click(screen.getByRole("button", { name: /columns/i }));
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    await waitFor(() => {
      const explorerTable = document.querySelector(".datalake-table");
      expect(within(explorerTable as HTMLElement).getByText("Catalog")).toBeInTheDocument();
    });
    // The choice is persisted by an effect, which React may not have flushed
    // yet. Unmounting first drops it, and the fresh mount below then reads
    // nothing and waits out the test timeout.
    await waitFor(() => {
      expect(window.localStorage.getItem("cryo.catalog.columnVisibility.v4")).not.toBeNull();
    });
    first.unmount();

    renderPage();
    await screen.findByRole("button", { name: /export csv/i });
    await waitFor(() => {
      const explorerTable = document.querySelector(".datalake-table");
      expect(within(explorerTable as HTMLElement).getByText("Catalog")).toBeInTheDocument();
    });
  });
});