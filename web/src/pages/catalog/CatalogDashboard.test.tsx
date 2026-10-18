import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getMultiCatalogDashboard, getTablesMaintenanceFunnel, listTables } from "../../api/client";
import { formatCurrency } from "../../lib/format";
import { ThemeProvider } from "../../lib/themeContext";
import { CatalogDashboard } from "./CatalogDashboard";

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
  getMultiCatalogDashboard: vi.fn(),
  getTablesMaintenanceFunnel: vi.fn(),
  listTables: vi.fn().mockResolvedValue({ items: [], total: 0 })
}));

const catalog = {
  catalog_id: "cat-1",
  catalog_name: "prod",
  catalog_type: "glue",
  table_count: 4,
  total_size_gb: 12,
  small_files_count: 5000,
  needs_compaction_count: 0,
  monthly_cost_usd: 80,
  projected_savings_usd_month: 0.4,
  avg_health_score: 67.4,
  enabled: true
};

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter>
          <CatalogDashboard />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

describe("CatalogDashboard", () => {
  it("shows triage cards with exact counts and the catalog with an integer score", async () => {
    vi.mocked(getTablesMaintenanceFunnel).mockResolvedValue({
      run_id: null,
      total_active: 0,
      nodes: [],
      links: []
    });
    vi.mocked(listTables).mockResolvedValue({ items: [], total: 3, limit: 1, offset: 0 });
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      catalogs: [catalog],
      total_catalogs: 1,
      total_tables: 4,
      total_cost_usd: 80
    });
    renderPage();

    const failed = await screen.findByRole("button", { name: /Failed last run/ });
    expect(failed).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: /Scan issues/ })).toBeInTheDocument();
    expect(await screen.findByText("67")).toBeInTheDocument();
    expect(screen.queryByText("67.4")).not.toBeInTheDocument();
    expect(screen.getByText(/saves <\$1\/mo/)).toBeInTheDocument();
    expect(screen.getByText(formatCurrency(80) + "/mo")).toBeInTheDocument();
    expect(await screen.findByText(/No nightly batch has run yet/)).toBeInTheDocument();
    fireEvent.click(failed);
    expect(failed).toHaveAttribute("aria-pressed", "true");
  });

  it("does not turn a missing fleet cost into zero", async () => {
    vi.mocked(getTablesMaintenanceFunnel).mockResolvedValue({ run_id: null, total_active: 0, nodes: [], links: [] });
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      catalogs: [{ ...catalog, table_count: 0, monthly_cost_usd: 0, projected_savings_usd_month: null }],
      total_catalogs: 1,
      total_tables: 0,
      total_cost_usd: 0
    });
    renderPage();

    expect(await screen.findByText("prod")).toBeInTheDocument();
    expect(screen.queryByText(/Modeled storage cost/)).not.toBeInTheDocument();
  });
});
