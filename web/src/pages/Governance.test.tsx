import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Governance } from "./Governance";

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
  listPendingApprovals: vi.fn().mockResolvedValue([]),
  listFleetAnomalies: vi.fn().mockResolvedValue([]),
  listMaintenanceSignals: vi.fn().mockResolvedValue([]),
  getCostForecast: vi.fn().mockResolvedValue({
    forecast_month: "2026-08",
    total_forecast_usd: 1200,
    forecast_storage_cost_usd: 800,
    forecast_compute_cost_usd: 300,
    forecast_maintenance_cost_usd: 100,
    alert_level: "ok"
  }),
  listTierPolicies: vi.fn().mockResolvedValue([
    {
      id: "policy-1",
      name: "Cold after 30d",
      source_id: "source-1",
      storage_class: "GLACIER",
      min_age_days: 30,
      enabled: true
    }
  ]),
  listSources: vi.fn().mockResolvedValue([{ id: "source-1", name: "demo", type: "rest", enabled: true }]),
  listTables: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  getTableLineage: vi.fn(),
  getS3TieringRecommendations: vi.fn().mockResolvedValue({
    recommendations: [],
    total_potential_savings_usd: 0
  }),
  getTierCandidates: vi.fn(),
  createApprovalRequest: vi.fn(),
  approvalAction: vi.fn(),
  createTierPolicy: vi.fn(),
  patchTierPolicy: vi.fn(),
  deleteTierPolicy: vi.fn()
}));

describe("Governance page", () => {
  it("renders governance sections and tier policies", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Governance />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByText("Governance")).toBeInTheDocument();
    expect(await screen.findByText("Tier policies")).toBeInTheDocument();
    expect(await screen.findByText("Cold after 30d")).toBeInTheDocument();
  });
});
