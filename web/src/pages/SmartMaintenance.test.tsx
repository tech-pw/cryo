import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DetectionTierResponseRead,
  SmartMaintenanceOverviewRead,
  SmartLayoutRecommendationRead
} from "../api/client";
import { SmartMaintenance } from "./SmartMaintenance";

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
  approveSmartLayoutRecommendation: vi.fn(),
  dismissSmartLayoutRecommendation: vi.fn(),
  executeSmartLayoutRecommendation: vi.fn(),
  getDetectionTiers: vi.fn(),
  getSmartMaintenanceOverview: vi.fn(),
  getTableRoiBreakdown: vi.fn(),
  getTableWorkloadForecast: vi.fn(),
  listSmartLayoutRecommendations: vi.fn()
}));

import {
  getDetectionTiers,
  getSmartMaintenanceOverview,
  getTableRoiBreakdown,
  getTableWorkloadForecast,
  listSmartLayoutRecommendations
} from "../api/client";

const overview: SmartMaintenanceOverviewRead = {
  generated_at: "2026-02-01T00:00:00Z",
  tables_with_stats: 42,
  tiers: { hot: 2, warm: 5, cold: 10, dormant: 25, unknown: 0 },
  roi_shadow_decisions_30d: 3,
  roi_enforced: false,
  pending_layout_recommendations: 1
};

const tiers: DetectionTierResponseRead = {
  items: [
    {
      table_id: "table-1",
      table_name: "events",
      schema_name: "analytics",
      tier: "hot",
      score: 88,
      commits_24h: 12,
      commits_7d: 40,
      delete_file_pct: 2,
      query_count_30d: 500
    }
  ]
};

const recommendations: { items: SmartLayoutRecommendationRead[] } = {
  items: [
    {
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
    }
  ]
};

const renderPage = (initialEntries: string[] = ["/smart-maintenance"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/smart-maintenance" element={<SmartMaintenance />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("SmartMaintenance", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSmartMaintenanceOverview).mockResolvedValue(overview);
    vi.mocked(getDetectionTiers).mockResolvedValue(tiers);
    vi.mocked(listSmartLayoutRecommendations).mockResolvedValue(recommendations);
    vi.mocked(getTableRoiBreakdown).mockResolvedValue({
      table_id: "table-1",
      decision: {
        action: "compact",
        reason: "high benefit",
        bcr: 4.2,
        threshold: 2,
        shadow: true,
        benefit_usd_month: 120,
        cost_usd_month: 20
      },
      monthly_benefit_usd: 120,
      monthly_cost_usd: 20,
      details: {}
    });
    vi.mocked(getTableWorkloadForecast).mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-02-01T00:00:00Z",
      horizon_hours: 24,
      expected_total_queries: 10,
      points: []
    });
  });

  it("shows the last-evaluated time and ROI enforcement mode from the overview", async () => {
    renderPage();
    expect(await screen.findByText(/Last evaluated/)).toBeInTheDocument();
    expect(screen.getByText("ROI enforcement off (shadow mode)")).toBeInTheDocument();
  });

  it("shows confidence and estimated benefit for a pending recommendation", async () => {
    renderPage();
    expect(await screen.findByText("82%")).toBeInTheDocument();
    expect(screen.getByText(/35% est\./)).toBeInTheDocument();
  });

  it("persists the Detection Tiers tab selection in the URL", async () => {
    renderPage();
    const tiersTab = await screen.findByRole("tab", { name: "Detection Tiers" });
    fireEvent.mouseDown(tiersTab, { button: 0 });
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Detection Tiers", selected: true })).toBeInTheDocument();
    });
  });

  it("restores the inspected table ROI sheet from the URL", async () => {
    renderPage(["/smart-maintenance?tab=tiers&inspect=table-1"]);
    await waitFor(() => {
      expect(screen.getByText("ROI decision · analytics.events")).toBeInTheDocument();
    });
  });
});
