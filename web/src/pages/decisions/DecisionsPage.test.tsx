import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SmartLayoutRecommendationRead } from "../../api/client";
import { VIEWER_REASON } from "../../lib/permissions";
import { DecisionsPage } from "./DecisionsPage";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer",
    subject: "viewer",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../../lib/runtimeFlags", () => ({
  getRuntimeFlags: vi.fn().mockResolvedValue({
    native_engine_enabled: false,
    maintenance_writes_enabled: true,
    native_engine_fallback: "trino"
  })
}));

vi.mock("../../api/client", () => ({
  approvalAction: vi.fn(),
  approveSmartLayoutRecommendation: vi.fn(),
  dismissSmartLayoutRecommendation: vi.fn(),
  executeSmartLayoutRecommendation: vi.fn(),
  getDetectionTiers: vi.fn(),
  getSlaStatusDashboard: vi.fn(),
  getSmartMaintenanceOverview: vi.fn(),
  getTableRoiBreakdown: vi.fn(),
  getTableWorkloadForecast: vi.fn(),
  listPendingApprovals: vi.fn(),
  listSmartLayoutRecommendations: vi.fn()
}));

import {
  getDetectionTiers,
  getSlaStatusDashboard,
  getSmartMaintenanceOverview,
  getTableRoiBreakdown,
  getTableWorkloadForecast,
  listPendingApprovals,
  listSmartLayoutRecommendations
} from "../../api/client";

const pending: SmartLayoutRecommendationRead = {
  id: "rec-1",
  table_id: "table-1",
  table_ref: "analytics.events",
  recommended_strategy: "z-order",
  recommended_columns: ["event_time"],
  status: "pending",
  confidence: 0.82,
  estimated_prune_improvement: 0.35,
  observed_prune_improvement: null,
  approved_at: null,
  dismissed_at: null,
  applied_at: null
};

const hiddenNone: SmartLayoutRecommendationRead = {
  id: "rec-none",
  table_id: "table-none",
  table_ref: "analytics.hidden",
  recommended_strategy: "none",
  recommended_columns: [],
  status: "pending",
  confidence: 0,
  estimated_prune_improvement: null,
  observed_prune_improvement: null,
  approved_at: null,
  dismissed_at: null,
  applied_at: null
};

const renderPage = (initialEntries: string[] = ["/decisions"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="*" element={<DecisionsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("DecisionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSmartMaintenanceOverview).mockResolvedValue({
      generated_at: "2026-02-01T00:00:00Z",
      tables_with_stats: 42,
      tiers: { hot: 2, warm: 5, cold: 10, dormant: 25, unknown: 0 },
      roi_shadow_decisions_30d: 3,
      roi_enforced: false,
      pending_layout_recommendations: 1
    });
    vi.mocked(getDetectionTiers).mockResolvedValue({ items: [] });
    vi.mocked(listSmartLayoutRecommendations).mockResolvedValue({ items: [pending, hiddenNone] });
    vi.mocked(listPendingApprovals).mockResolvedValue([]);
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({ slas: [], total_breached: 0, total_monitored: 0 });
    vi.mocked(getTableRoiBreakdown).mockResolvedValue({
      table_id: "table-1",
      decision: {
        action: "skip",
        reason: "below threshold",
        bcr: 0.4,
        threshold: 2,
        shadow: true,
        benefit_usd_month: 1,
        cost_usd_month: 4
      },
      monthly_benefit_usd: 1,
      monthly_cost_usd: 4,
      details: {}
    });
    vi.mocked(getTableWorkloadForecast).mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-02-01T00:00:00Z",
      horizon_hours: 24,
      expected_total_queries: 0,
      points: []
    });
  });

  it("renders the title and subtitle", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Decisions" })).toBeInTheDocument();
    expect(screen.getByText("What is waiting on a person, and why was a table skipped?")).toBeInTheDocument();
    expect(await screen.findByText("analytics.events")).toBeInTheDocument();
    expect(screen.getByText("pending")).toHaveClass("w-fit");
    expect(screen.queryByText("analytics.hidden")).not.toBeInTheDocument();
  });

  it("does not enable Approve for a viewer", async () => {
    renderPage();
    const approveButtons = await screen.findAllByRole("button", { name: "Approve" });
    expect(approveButtons).toHaveLength(1);
    expect(approveButtons[0]).toBeDisabled();
    expect(approveButtons[0]).toHaveAttribute("title", VIEWER_REASON);
    expect(screen.getByText(VIEWER_REASON)).toBeInTheDocument();
  });

  it("does not invent a skip count on the skipped tab", async () => {
    renderPage(["/decisions?tab=skipped"]);
    expect(await screen.findByRole("heading", { name: "No skip list is stored" })).toBeInTheDocument();
    expect(screen.getByText(/does not estimate how many tables were skipped/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skipped · —" })).toBeInTheDocument();
    expect(screen.queryByText("500+")).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Reason" })).not.toBeInTheDocument();
  });
});
