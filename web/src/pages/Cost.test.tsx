import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../components/ui/tooltip";
import { Cost } from "./Cost";

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
  // Overview-tab queries -- the test never drives the Overview tab's data through, so
  // we return safe defaults and let the Overview tab render its loading / empty states.
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
  deriveCostDashboard: vi.fn()
}));

import {
  deriveCostDashboard,
  getAwsIntegration,
  getAwsUsage,
  getCostDashboard,
  getSchemaCost,
  getSourceS3RequestCostRollup,
  listSourceS3LogConfigs,
  listSources,
  listTables
} from "../api/client";
import type { CostDashboard } from "../api/types";

const EMPTY_DASHBOARD: CostDashboard = {
  totals: {
    current_monthly_cost_usd: 0,
    projected_monthly_cost_usd: 0,
    projected_savings_usd_month: 0,
    current_storage_bytes: 0,
    reclaimable_bytes: 0
  },
  savings_by_operation: [],
  cost_by_schema: [],
  top_opportunities: [],
  trend: []
};

const mockDefaultOverview = () => {
  vi.mocked(getCostDashboard).mockResolvedValue(EMPTY_DASHBOARD);
  vi.mocked(listTables).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  // `deriveCostDashboard` is the fallback when getCostDashboard fails; the test must return
  // a valid CostDashboard here because CostOverviewTab reads `.trend` / `.totals` unconditionally
  // from whichever path the data comes through.
  vi.mocked(deriveCostDashboard).mockReturnValue(EMPTY_DASHBOARD);
};

const renderPage = (initialRoute = "/cost") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <CryoTooltipProvider>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route path="/cost" element={<Cost />} />
            <Route path="/cost/aws" element={<Cost />} />
            <Route path="/aws" element={<Cost />} />
          </Routes>
        </MemoryRouter>
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

describe("Consolidated Cost page", () => {
  it("renders four tabs and starts on Storage by default", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);
    renderPage();

    expect(await screen.findByRole("tab", { name: /^Storage$/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Requests$/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /^Impact$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Reads$/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /^AWS$/ })).not.toBeInTheDocument();

    // Overview tab is selected by default — hero MetricTile for total monthly cost.
    expect(await screen.findByText(/Estimated storage cost/i)).toBeInTheDocument();
  });

  it("Overview shows scan provenance and chart-equivalent data tables", async () => {
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);
    const dashboard: CostDashboard = {
      totals: {
        current_monthly_cost_usd: 1000,
        projected_monthly_cost_usd: 700,
        projected_savings_usd_month: 300,
        current_storage_bytes: 5_000_000_000,
        reclaimable_bytes: 1_000_000_000,
        last_full_cost_scan_at: "2026-07-01T00:00:00Z"
      },
      savings_by_operation: [
        { operation: "optimize", savings_usd_month: 200, reclaimable_bytes: 500_000_000 },
        { operation: "expire_snapshots", savings_usd_month: 100, reclaimable_bytes: 500_000_000 }
      ],
      cost_by_schema: [],
      top_opportunities: [],
      trend: [
        { scan_at: "2026-06-01T00:00:00Z", current_monthly_cost_usd: 1200, projected_savings_usd_month: 250, realized_usd_month: 50 },
        { scan_at: "2026-07-01T00:00:00Z", current_monthly_cost_usd: 1000, projected_savings_usd_month: 300, realized_usd_month: 120 }
      ]
    };
    vi.mocked(getCostDashboard).mockResolvedValue(dashboard);
    vi.mocked(deriveCostDashboard).mockReturnValue(dashboard);
    vi.mocked(listTables).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    vi.mocked(getSchemaCost).mockResolvedValue([
      { schema_name: "analytics", cost_usd: 400, size_gb: 200 },
      { schema_name: "raw", cost_usd: 600, size_gb: 900 }
    ]);
    renderPage();

    // Persistent (non-hover) provenance text next to the precision badge.
    expect(await screen.findByText(/Last full storage-truth scan:/i)).toBeInTheDocument();

    // Each chart pairs with a native <details> chart-equivalent data table (collapsed by
    // default, but its rows are present in the DOM so assistive tech / find-in-page can reach
    // them without needing to open the disclosure first).
    expect(await screen.findByText(/Cost waterfall from current monthly cost/i)).toBeInTheDocument();
    expect(screen.getByText(/Storage cost, projected savings and realized savings by scan date/i)).toBeInTheDocument();
    expect(screen.getByText(/Estimated monthly S3 cost by schema/i)).toBeInTheDocument();

    // The schema chart's paired table exposes the real row data (schema, cost, size).
    expect(await screen.findByText("analytics")).toBeInTheDocument();
    expect(screen.getByText("raw")).toBeInTheDocument();
    expect(screen.getByText("$600")).toBeInTheDocument();
  });

  it("?tab=s3-requests opens the S3 requests tab and renders per-bucket rows", async () => {
    mockDefaultOverview();
    const source = {
      id: "src-1",
      name: "prod_data",
      type: "hive",
      enabled: true,
      config: {},
      maintenance_schema_names: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };
    vi.mocked(listSources).mockResolvedValue([source]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([
      {
        bucket: "lake-bucket",
        region: "ap-south-1",
        logging_status: "enabled",
        log_target_bucket: "logs-bucket",
        log_target_prefix: "/prod",
        manual_override: false,
        enabled: true,
        last_ingested_at: "2026-07-07T10:00:00Z"
      }
    ]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue({
      bucket: "lake-bucket",
      source_id: "src-1",
      source_name: "prod_data",
      region: "ap-south-1",
      enabled: true,
      logging_status: "enabled",
      last_ingested_at: "2026-07-07T10:00:00Z",
      total_request_count_30d: 5_000_000,
      total_cost_usd_month: 2.5,
      by_tier: {
        tier1_count: 200_000,
        tier2_count: 4_800_000,
        tier1_cost_usd: 0.5,
        tier2_cost_usd: 2.0
      },
      by_day: [],
      by_bucket: [
        {
          bucket: "lake-bucket",
          region: "ap-south-1",
          enabled: true,
          logging_status: "enabled",
          last_ingested_at: "2026-07-07T10:00:00Z",
          total_request_count_30d: 5_000_000,
          total_cost_usd_month: 2.5,
          by_tier: {
            tier1_count: 200_000,
            tier2_count: 4_800_000,
            tier1_cost_usd: 0.5,
            tier2_cost_usd: 2.0
          }
        }
      ],
      approximate: false
    });
    renderPage("/cost?tab=s3-requests");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/^Requests$/);
    expect(await screen.findByText("lake-bucket")).toBeInTheDocument();
    // "prod_data" appears both as the per-bucket source label and inside the
    // chart-equivalent data table's "prod_data/lake-bucket" bucket identifier.
    expect(screen.getAllByText(/prod_data/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders one row per bucket, not one summed row per source", async () => {
    // Regression test: a source that tracks several buckets used to collapse into a
    // single "Per-bucket status" row showing only the aggregate total, with the
    // individual bucket names/costs invisible. by_bucket must expand into real rows.
    mockDefaultOverview();
    const source = {
      id: "src-multi",
name: "sample-lakehouse",
      type: "hive",
      enabled: true,
      config: {},
      maintenance_schema_names: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };
    vi.mocked(listSources).mockResolvedValue([source]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([
      {
        bucket: "lake-bucket-a",
        region: "ap-south-1",
        logging_status: "enabled",
        manual_override: false,
        enabled: true,
        last_ingested_at: "2026-07-14T10:00:00Z",
        log_target_bucket: null,
        log_target_prefix: null
      },
      {
        bucket: "lake-bucket-b",
        region: "ap-south-1",
        logging_status: "enabled",
        manual_override: false,
        enabled: true,
        last_ingested_at: "2026-07-14T10:00:00Z",
        log_target_bucket: null,
        log_target_prefix: null
      }
    ]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue({
      bucket: "lake-bucket-a",
      source_id: "src-multi",
      source_name: "sample-lakehouse",
      region: "ap-south-1",
      enabled: true,
      logging_status: "enabled",
      last_ingested_at: "2026-07-14T10:00:00Z",
      total_request_count_30d: 1_200_000,
      total_cost_usd_month: 530,
      by_tier: { tier1_count: 14_000, tier2_count: 1_186_000, tier1_cost_usd: 30, tier2_cost_usd: 500 },
      by_day: [],
      by_bucket: [
        {
          bucket: "lake-bucket-a",
          region: "ap-south-1",
          enabled: true,
          logging_status: "enabled",
          last_ingested_at: "2026-07-14T10:00:00Z",
          total_request_count_30d: 1_000_000,
          total_cost_usd_month: 450,
          by_tier: { tier1_count: 10_000, tier2_count: 990_000, tier1_cost_usd: 25, tier2_cost_usd: 425 }
        },
        {
          bucket: "lake-bucket-b",
          region: "ap-south-1",
          enabled: true,
          logging_status: "enabled",
          last_ingested_at: "2026-07-14T10:00:00Z",
          total_request_count_30d: 200_000,
          total_cost_usd_month: 80,
          by_tier: { tier1_count: 4_000, tier2_count: 196_000, tier1_cost_usd: 5, tier2_cost_usd: 75 }
        }
      ],
      approximate: false
    });
    renderPage("/cost?tab=s3-requests");

    expect(await screen.findByText("lake-bucket-a")).toBeInTheDocument();
    expect(await screen.findByText("lake-bucket-b")).toBeInTheDocument();
    // "$450"/"$80" render twice each: once in the per-bucket status table and once in the
    // chart-equivalent data table paired with the "Top buckets" bar chart.
    expect(screen.getAllByText("$450").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("$80").length).toBeGreaterThanOrEqual(1);
    // The two source labels (one per bucket row) both render, not deduped into one row.
    expect(screen.getAllByText("sample-lakehouse")).toHaveLength(2);
  });

  it("surfaces the approximate-pricing warning when one or more rollups fell back to defaults", async () => {
    mockDefaultOverview();
    const source = {
      id: "src-2",
      name: "staging",
      type: "rest",
      enabled: true,
      config: {},
      maintenance_schema_names: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };
    vi.mocked(listSources).mockResolvedValue([source]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([
      {
        bucket: "staging-bucket",
        region: "eu-south-1",
        logging_status: "enabled",
        manual_override: false,
        enabled: true,
        last_ingested_at: null,
        log_target_bucket: null,
        log_target_prefix: null
      }
    ]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue({
      bucket: "staging-bucket",
      source_id: "src-2",
      source_name: "staging",
      region: "eu-south-1",
      enabled: true,
      logging_status: "enabled",
      last_ingested_at: null,
      total_request_count_30d: 0,
      total_cost_usd_month: 0,
      by_tier: { tier1_count: 0, tier2_count: 0, tier1_cost_usd: 0, tier2_cost_usd: 0 },
      by_day: [],
      by_bucket: [
        {
          bucket: "staging-bucket",
          region: "eu-south-1",
          enabled: true,
          logging_status: "enabled",
          last_ingested_at: null,
          total_request_count_30d: 0,
          total_cost_usd_month: 0,
          by_tier: { tier1_count: 0, tier2_count: 0, tier1_cost_usd: 0, tier2_cost_usd: 0 }
        }
      ],
      approximate: true
    });
    renderPage("/cost?tab=s3-requests");

    expect(await screen.findByRole("alert")).toHaveTextContent(/default rate/i);
  });

  it("?tab=aws-integrations aliases to Storage; the connection form lives on Connections", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    renderPage("/cost?tab=aws-integrations");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/^Storage$/);
    expect(screen.queryByRole("button", { name: /connect aws/i })).not.toBeInTheDocument();
    expect(await screen.findByText(/Estimated storage cost/i)).toBeInTheDocument();
  });

  it("URL ?tab= is replaced with the Overview tab on switch", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);
    const { unmount } = renderPage("/cost?tab=s3-requests");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/^Requests$/);

    unmount();
    renderPage("/cost");

    expect(await screen.findByText(/Estimated storage cost/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Storage$/ })).toHaveAttribute("data-state", "active");
  });

  it("uses useSearchParams to pick the active tab -- the URL ?tab= query wins over the default", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);

    renderPage("/cost?tab=requests");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/^Requests$/);
  });
});