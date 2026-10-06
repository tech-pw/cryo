import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  listFleetAlerts: vi.fn().mockResolvedValue([]),
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
  listTables: vi.fn().mockResolvedValue({
    items: [
      {
        id: "table-1",
        source_id: "source-1",
        schema_name: "demo",
        table_name: "broken_orders",
        first_seen_at: "2026-07-01T00:00:00Z",
        is_active: true,
        consecutive_scan_failures: 3,
        last_scan_error: "AWS Error ACCESS_DENIED during HeadObject operation",
        failure_category: "permission_denied",
        failure_category_label: "Permission denied",
        failure_category_guidance: "Check the IAM policy attached to the source's credentials.",
        failure_category_actionable_from_cryo: false
      }
    ],
    total: 1
  }),
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
  deleteTierPolicy: vi.fn(),
  getObservationStats: vi.fn().mockResolvedValue({
    total_files: 0,
    total_bytes: 0,
    oldest_event: null,
    newest_event: null
  }),
  searchObservations: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  compactObservations: vi.fn(),
  listAbacPolicies: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    enforcement_note: "Enforced on maintenance-execute paths only."
  }),
  getGitOpsStatus: vi.fn().mockResolvedValue({
    configured: false,
    repo_path: null,
    policy_dir: null,
    policies: [],
    conflicts: [],
    message: "GitOps is not configured."
  }),
  getTable: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail?: unknown;
    constructor(status: number, message: string, detail?: unknown) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.detail = detail;
    }
  }
}));

const renderGovernance = (initialRoute = "/governance") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Governance />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("Governance page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders governance sections and tier policies", async () => {
    renderGovernance("/governance?tab=policies");
    expect(screen.getByRole("heading", { name: "Governance" })).toBeInTheDocument();
    expect(await screen.findByText("Tier policies")).toBeInTheDocument();
    expect(await screen.findByText("Cold after 30d")).toBeInTheDocument();
  }, 15000);

  it("shows the failure category for broken tables", async () => {
    renderGovernance("/governance?tab=inventory");
    expect(await screen.findByText("demo.broken_orders")).toBeInTheDocument();
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
  }, 15000);

  it("resolves legacy tab aliases to the new task-oriented tabs", async () => {
    renderGovernance("/governance?tab=attention");
    expect(screen.getByRole("heading", { name: "Governance" })).toBeInTheDocument();
    expect(await screen.findByText("Pending approvals")).toBeInTheDocument();
  }, 15000);

  it("only mounts the active tab's content and data fetching", async () => {
    const client = await import("../api/client");
    renderGovernance("/governance?tab=triage");
    expect(await screen.findByText("Pending approvals")).toBeInTheDocument();
    // Tier policies / ABAC policies are rendered only by the Access & policy tab.
    expect(screen.queryByText("Tier policies")).not.toBeInTheDocument();
    expect(client.listTierPolicies).not.toHaveBeenCalled();
    expect(client.listAbacPolicies).not.toHaveBeenCalled();
  }, 15000);

  it("switches tabs and mounts the newly active tab's content", async () => {
    renderGovernance("/governance?tab=access");
    expect(await screen.findByText("Tier policies")).toBeInTheDocument();
    expect(screen.queryByText("Pending approvals")).not.toBeInTheDocument();
  }, 15000);
});
