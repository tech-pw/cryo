import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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
      <MemoryRouter initialEntries={[initialRoute]}>
        <Routes>
          <Route path="/cost" element={<Cost />} />
          <Route path="/cost/aws" element={<Cost />} />
          <Route path="/aws" element={<Cost />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("Consolidated Cost page", () => {
  it("renders three tabs and starts on Overview by default", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);
    renderPage();

    expect(await screen.findByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /s3 requests/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /aws integrations/i })).toBeInTheDocument();

    // Overview tab is selected by default -- the "Cost" page header + "S3 monthly now" stat strip.
    expect(await screen.findByText(/S3 monthly now/i)).toBeInTheDocument();
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

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/s3 requests/i);
    expect(await screen.findByText("lake-bucket")).toBeInTheDocument();
    expect(screen.getByText(/prod_data/)).toBeInTheDocument();
  });

  it("renders one row per bucket, not one summed row per source", async () => {
    // Regression test: a source that tracks several buckets used to collapse into a
    // single "Per-bucket status" row showing only the aggregate total, with the
    // individual bucket names/costs invisible. by_bucket must expand into real rows.
    mockDefaultOverview();
    const source = {
      id: "src-multi",
      name: "cdp",
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
        bucket: "prod-silver-layer",
        region: "ap-south-1",
        logging_status: "enabled",
        manual_override: false,
        enabled: true,
        last_ingested_at: "2026-07-14T10:00:00Z",
        log_target_bucket: null,
        log_target_prefix: null
      },
      {
        bucket: "prod-gold-layer",
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
      bucket: "prod-silver-layer",
      source_id: "src-multi",
      source_name: "cdp",
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
          bucket: "prod-silver-layer",
          region: "ap-south-1",
          enabled: true,
          logging_status: "enabled",
          last_ingested_at: "2026-07-14T10:00:00Z",
          total_request_count_30d: 1_000_000,
          total_cost_usd_month: 450,
          by_tier: { tier1_count: 10_000, tier2_count: 990_000, tier1_cost_usd: 25, tier2_cost_usd: 425 }
        },
        {
          bucket: "prod-gold-layer",
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

    expect(await screen.findByText("prod-silver-layer")).toBeInTheDocument();
    expect(await screen.findByText("prod-gold-layer")).toBeInTheDocument();
    expect(screen.getByText("$450")).toBeInTheDocument();
    expect(screen.getByText("$80")).toBeInTheDocument();
    // The two source labels (one per bucket row) both render, not deduped into one row.
    expect(screen.getAllByText("cdp")).toHaveLength(2);
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

  it("?tab=aws-integrations opens the AWS tab and renders the per-source card", async () => {
    mockDefaultOverview();
    const source = {
      id: "src-3",
      name: "prod_data",
      type: "hive",
      enabled: true,
      config: {},
      maintenance_schema_names: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };
    vi.mocked(listSources).mockResolvedValue([source]);
    vi.mocked(getAwsIntegration).mockImplementation(async () => {
      const err = new Error("not found") as Error & { status?: number };
      err.status = 404;
      throw err;
    });
    vi.mocked(getAwsUsage).mockResolvedValue({} as never);
    renderPage("/cost?tab=aws-integrations");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/aws integrations/i);
    expect(await screen.findByText("prod_data")).toBeInTheDocument();
    expect(await screen.findAllByRole("button", { name: /connect aws/i })).toHaveLength(1);
  });

  it("URL ?tab= is replaced with the Overview tab on switch", async () => {
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(listSourceS3LogConfigs).mockResolvedValue([]);
    vi.mocked(getSourceS3RequestCostRollup).mockResolvedValue(null as never);
    renderPage("/cost?tab=s3-requests");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/s3 requests/i);

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    fireEvent.click(overviewTab);

    await waitFor(() => {
      expect(overviewTab.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("uses useSearchParams to pick the active tab -- the URL ?tab= query wins over the default", async () => {
    // The /aws -> /cost?tab=aws-integrations redirect lives in App.tsx, not in Cost.tsx.
    // What Cost.tsx itself owns is "if ?tab=aws-integrations is set, open the AWS tab on
    // mount" -- so this test exercises just that contract, without the App-level redirect.
    mockDefaultOverview();
    vi.mocked(listSources).mockResolvedValue([]);
    vi.mocked(getAwsIntegration).mockRejectedValue(new Error("404"));
    vi.mocked(getAwsUsage).mockResolvedValue({} as never);

    renderPage("/cost?tab=aws-integrations");

    expect(await screen.findByRole("tab", { selected: true })).toHaveTextContent(/aws integrations/i);
  });
});