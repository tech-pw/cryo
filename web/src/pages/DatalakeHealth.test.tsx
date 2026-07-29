import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
    grade_distribution: [{ grade: "B", count: 2 }],
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
});
