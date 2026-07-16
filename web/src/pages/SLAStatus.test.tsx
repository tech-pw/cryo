import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { bucketForFreshness, SLAStatus } from "./SLAStatus";
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

describe("bucketForFreshness", () => {
  it("returns null for a null freshness value", () => {
    expect(bucketForFreshness(null)).toBeNull();
  });

  it("buckets boundary values into the correct bucket, not the adjacent one", () => {
    expect(bucketForFreshness(0)).toBe("lt1h");
    expect(bucketForFreshness(0.99)).toBe("lt1h");
    expect(bucketForFreshness(1)).toBe("1to6h"); // exactly 1h rolls into the next bucket
    expect(bucketForFreshness(6)).toBe("6to24h");
    expect(bucketForFreshness(24)).toBe("1to3d");
    expect(bucketForFreshness(72)).toBe("3to7d");
    expect(bucketForFreshness(168)).toBe("gt7d");
  });

  it("has no gaps or overlaps -- every non-negative value lands in exactly one bucket", () => {
    for (const hours of [0, 0.5, 1, 5.9, 6, 23.9, 24, 71.9, 72, 167.9, 168, 1000]) {
      expect(bucketForFreshness(hours)).not.toBeNull();
    }
  });
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

  it("renders the freshness distribution card", async () => {
    // recharts' ResponsiveContainer reports zero width/height under jsdom, so axis ticks
    // and bars never actually paint here -- this only verifies the section mounts and the
    // click-to-filter affordance is present, not the chart's internal SVG output.
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({ table_id: "t-1", table_name: "lms.fresh", freshness_hours: 0.5 }),
        row({ table_id: "t-2", table_name: "lms.stale", freshness_hours: 200 })
      ],
      total_breached: 0,
      total_monitored: 2
    } satisfies SLAResponse);
    renderPage();

    expect(await screen.findByText("Freshness distribution")).toBeInTheDocument();
    expect(screen.getByText("Click a bar to filter the table below")).toBeInTheDocument();
  });

  it("filters the table by search text on the table name", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({ table_id: "t-1", table_name: "lms.gold_banners" }),
        row({ table_id: "t-2", table_name: "cdp_revenue.silver_x" })
      ],
      total_breached: 0,
      total_monitored: 2
    } satisfies SLAResponse);
    renderPage();

    expect(await screen.findByText("lms.gold_banners")).toBeInTheDocument();
    expect(screen.getByText("cdp_revenue.silver_x")).toBeInTheDocument();

    const search = screen.getByPlaceholderText("Search table name");
    fireEvent.change(search, { target: { value: "gold_banners" } });

    expect(await screen.findByText("lms.gold_banners")).toBeInTheDocument();
    expect(screen.queryByText("cdp_revenue.silver_x")).not.toBeInTheDocument();
  });

  it("search is case-insensitive and matches on partial table name", async () => {
    vi.mocked(getSlaStatusDashboard).mockResolvedValue({
      slas: [
        row({ table_id: "t-1", table_name: "lms.gold_banners" }),
        row({ table_id: "t-2", table_name: "cdp_revenue.silver_x" })
      ],
      total_breached: 0,
      total_monitored: 2
    } satisfies SLAResponse);
    renderPage();
    await screen.findByText("lms.gold_banners");

    const search = screen.getByPlaceholderText("Search table name");
    fireEvent.change(search, { target: { value: "REVENUE" } });

    expect(await screen.findByText("cdp_revenue.silver_x")).toBeInTheDocument();
    expect(screen.queryByText("lms.gold_banners")).not.toBeInTheDocument();
  });
});
