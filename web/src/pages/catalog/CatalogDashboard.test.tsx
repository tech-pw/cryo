import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getMultiCatalogDashboard, listTables } from "../../api/client";
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
  listTables: vi.fn().mockResolvedValue({ items: [], total: 0 })
}));

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
  it("labels tables with stats and modeled fleet cost, and uses the API compaction count", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      catalogs: [
        {
          catalog_id: "cat-1",
          catalog_name: "prod",
          catalog_type: "glue",
          table_count: 4,
          total_size_gb: 12,
          small_files_count: 5000,
          needs_compaction_count: 0,
          monthly_cost_usd: 80,
          projected_savings_usd_month: null,
          avg_health_score: 70,
          enabled: true
        }
      ],
      total_catalogs: 1,
      total_tables: 4,
      total_cost_usd: 80
    });
    renderPage();

    expect(await screen.findAllByText("Tables with stats")).not.toHaveLength(0);
    expect(screen.queryByText("Tables scanned")).not.toBeInTheDocument();
    const compaction = screen.getByRole("button", { name: /Needs compaction/ }).closest(".cryo-metric-tile");
    expect(compaction).toHaveTextContent("0");
    expect(screen.queryByText("Tables scanned")).not.toBeInTheDocument();
    const fleet = screen.getByTitle(
      "Modeled sum of scanner-estimated monthly S3 cost for tables with stats on enabled catalogs. Not the Health figure, and not a bill."
    );
    expect(fleet).toHaveTextContent("Modeled");
    expect(fleet).toHaveTextContent(formatCurrency(80));
    expect(listTables).toHaveBeenCalled();
  });

  it("does not turn a missing fleet cost or table count into zero", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      catalogs: [
        {
          catalog_id: "cat-1",
          catalog_name: "prod",
          catalog_type: "glue",
          table_count: 0,
          total_size_gb: 0,
          small_files_count: 0,
          needs_compaction_count: 0,
          monthly_cost_usd: 0,
          projected_savings_usd_month: null,
          avg_health_score: 0,
          enabled: true
        }
      ],
      total_catalogs: 0,
      total_tables: undefined as unknown as number,
      total_cost_usd: undefined as unknown as number
    });
    renderPage();

    const tablesLabel = await screen.findByText("Tables with stats", { selector: "p" });
    const tables = tablesLabel.closest(".cryo-metric-tile");
    expect(tables).toHaveTextContent("Not measured");
    expect(tables).not.toHaveTextContent("0");
    const fleet = screen.getByText(/Fleet cost/);
    expect(fleet).toHaveTextContent("Cost data unavailable");
    expect(fleet).not.toHaveTextContent("$0");
  });

  it("does not show $0 when no tables have been scanned", async () => {
    vi.mocked(getMultiCatalogDashboard).mockResolvedValue({
      catalogs: [
        {
          catalog_id: "cat-1",
          catalog_name: "prod",
          catalog_type: "glue",
          table_count: 0,
          total_size_gb: 0,
          small_files_count: 0,
          needs_compaction_count: 0,
          monthly_cost_usd: 0,
          projected_savings_usd_month: null,
          avg_health_score: 0,
          enabled: true
        }
      ],
      total_catalogs: 1,
      total_tables: 0,
      total_cost_usd: 0
    });
    renderPage();
    const fleet = await screen.findByText(/Fleet cost/);
    expect(fleet).toHaveTextContent("Cost data unavailable");
    expect(fleet).not.toHaveTextContent("$0");
  });
});
