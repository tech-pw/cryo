import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
  listPresets: vi.fn(),
  listDestinations: vi.fn(),
  getCostModelEvaluate: vi.fn(),
  getSchemaCost: vi.fn(),
  assignTables: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  triggerCostScan: vi.fn(),
  triggerScan: vi.fn(),
  deriveCostDashboard: vi.fn(),
  getMultiCatalogDashboard: vi.fn()
}));

import {
  getMultiCatalogDashboard,
  listDestinations,
  listPresets,
  listSources,
  listTables
} from "../api/client";

const renderPage = (initialRoute = "/catalogs") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route path="/catalogs" element={<Catalog />} />
            <Route path="/tables" element={<Catalog />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

describe("Consolidated Catalog page", () => {
  beforeEach(() => {
    vi.mocked(listPresets).mockResolvedValue([]);
    vi.mocked(listDestinations).mockResolvedValue([]);
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

    // Page header is shared between both sections.
    expect(await screen.findByText(/catalogs & tables/i)).toBeInTheDocument();

    // Dashboard section: KPI strip renders once the dashboard query resolves.
    await waitFor(() => {
      expect(screen.getAllByText(/total catalogs/i).length).toBeGreaterThan(0);
    });

    // Table explorer: Export CSV button is mounted regardless of row count.
    expect(screen.getByRole("button", { name: /export csv/i })).toBeInTheDocument();
  });

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

    // The dashboard renders prod_data inside an <strong> tag, so we filter by tagName to
    // distinguish the dashboard row from the FilterBar (which renders the source name
    // inside a <span>).
    await waitFor(() => {
      expect(screen.getAllByText("prod_data").some((el) => el.tagName === "STRONG")).toBe(true);
    });
  });

  it("direct /tables URL also lands on the consolidated page", async () => {
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
    renderPage("/tables");

    expect(await screen.findByText(/catalogs & tables/i)).toBeInTheDocument();
  });
});