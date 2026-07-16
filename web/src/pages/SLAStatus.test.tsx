import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { SLAStatus } from "./SLAStatus";
import type { SLAResponse } from "../api/client";

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
  getSlaStatusDashboard: vi.fn()
}));

import { getSlaStatusDashboard } from "../api/client";

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SLAStatus />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const row = (overrides: Partial<SLAResponse["slas"][number]>): SLAResponse["slas"][number] => ({
  table_id: "t-1",
  table_name: "lms.gold_banners",
  schema_name: "lms",
  freshness_hours: 1,
  freshness_sla_hours: 24,
  freshness_breached: false,
  scan_staleness_hours: 1,
  maintenance_days: 1,
  maintenance_sla_days: 7,
  maintenance_breached: false,
  p95_latency_ms: 100,
  p95_latency_sla_ms: 5000,
  p95_latency_breached: false,
  has_preset: true,
  ...overrides
});

describe("SLAStatus page", () => {
  it("shows the breach-by-type breakdown in the alert text", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({
          table_id: "t-1",
          table_name: "lms.gold_banners",
          freshness_breached: true,
          freshness_hours: 100
        }),
        row({
          table_id: "t-2",
          table_name: "cdp_revenue.silver_x",
          schema_name: "cdp_revenue",
          maintenance_breached: true,
          maintenance_days: 40
        })
      ],
      total_breached: 2,
      total_monitored: 2
    } satisfies SLAResponse);
    renderPage();

    const alert = await screen.findByText(/SLA breaches detected/i);
    expect(alert.closest(".ant-alert")).toHaveTextContent(/1 freshness, 1 maintenance, 0 latency/i);
  });

  it("shows the missing-coverage banner only for breached tables without a preset", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({ table_id: "t-1", freshness_breached: true, has_preset: false }),
        // Breached but has a preset -- should not count toward the missing-coverage banner.
        row({ table_id: "t-2", maintenance_breached: true, has_preset: true }),
        // No preset but not breached -- should not count either.
        row({ table_id: "t-3", has_preset: false })
      ],
      total_breached: 2,
      total_monitored: 3
    } satisfies SLAResponse);
    renderPage();

    expect(await screen.findByText(/Missing maintenance coverage/i)).toBeInTheDocument();
    expect(screen.getByText(/1 breached table\(s\) have no preset assigned/i)).toBeInTheDocument();
  });

  it("does not show the missing-coverage banner when every breached table has a preset", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [row({ table_id: "t-1", freshness_breached: true, has_preset: true })],
      total_breached: 1,
      total_monitored: 1
    } satisfies SLAResponse);
    renderPage();

    await screen.findByText(/SLA breaches detected/i);
    expect(screen.queryByText(/Missing maintenance coverage/i)).not.toBeInTheDocument();
  });

  it("builds the SLA matrix by schema from the full table list", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({ table_id: "t-1", schema_name: "lms", freshness_breached: true }),
        row({ table_id: "t-2", schema_name: "lms" }),
        row({ table_id: "t-3", schema_name: "cdp_revenue", maintenance_breached: true })
      ],
      total_breached: 2,
      total_monitored: 3
    } satisfies SLAResponse);
    renderPage();

    expect(await screen.findByText("SLA matrix by schema")).toBeInTheDocument();
    expect(screen.getByText("lms")).toBeInTheDocument();
    expect(screen.getByText("cdp_revenue")).toBeInTheDocument();
    // cdp_revenue: 1 table, 1 breached -> 0.0% compliance
    expect(screen.getByText("0.0%")).toBeInTheDocument();
    // lms: 2 tables, 1 breached -> 50.0% compliance
    expect(screen.getByText("50.0%")).toBeInTheDocument();
  });

  it("hides the SLA matrix in breached-only mode, where table totals would be misleading", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [row({ table_id: "t-1", freshness_breached: true })],
      total_breached: 1,
      total_monitored: 5
    } satisfies SLAResponse);
    renderPage();

    await screen.findByText(/SLA breaches detected/i);
    const toggle = screen.getByRole("switch");
    toggle.click();

    await screen.findByRole("switch", { checked: true });
    expect(screen.queryByText("SLA matrix by schema")).not.toBeInTheDocument();
  });
});
