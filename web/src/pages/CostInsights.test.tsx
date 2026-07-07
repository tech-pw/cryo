import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CostInsights } from "./CostInsights";

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

const baseSource = {
  id: "src-1",
  name: "sample-lakehouse",
  type: "glue",
  enabled: true,
  config: {},
  maintenance_schema_names: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const baseConfig = {
  id: "cfg-1",
  source_id: "src-1",
  cur_bucket: "my-cur-bucket",
  cur_prefix: "cur-prod/",
  cur_report_name: "cryo-monthly",
  fixture_path: null,
  inventory_bucket: "my-inv-bucket",
  inventory_prefix: "inventory/prod/",
  inventory_format: "CSV",
  access_key_id: null,
  enabled: true,
  last_ingested_at: "2026-07-06T10:00:00Z",
  last_ingested_through: "2026-07-05T00:00:00Z",
  last_ingest_ok: true,
  last_ingest_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-07-06T10:00:00Z"
};

const byBucketResponse = {
  source_id: "src-1",
  start: null,
  end: null,
  total_cost_usd: 25.5,
  items: [
    { bucket: "lake-bucket", total_cost_usd: 20.0, line_count: 100, last_usage_date: "2026-07-06" },
    { bucket: "logs-archive", total_cost_usd: 5.5, line_count: 25, last_usage_date: "2026-07-06" }
  ]
};

const dailyResponse = {
  source_id: "src-1",
  items: [
    { usage_date: "2026-07-01", total_cost_usd: 8.0 },
    { usage_date: "2026-07-02", total_cost_usd: 9.5 },
    { usage_date: "2026-07-03", total_cost_usd: 8.0 }
  ]
};

const byPrefixResponse = {
  source_id: "src-1",
  inventory_date: "2026-07-06",
  items: [
    {
      table_prefix: "db/sales",
      size_bytes: 1024 * 1024 * 1024,
      object_count: 150,
      last_modified: "2026-07-05T00:00:00Z"
    },
    {
      table_prefix: "logs/audit",
      size_bytes: 512 * 1024 * 1024,
      object_count: 75,
      last_modified: "2026-07-04T00:00:00Z"
    }
  ]
};

vi.mock("../api/client", () => ({
  listSources: vi.fn(),
  getAwsCostInsightConfig: vi.fn(),
  createAwsCostInsightConfig: vi.fn(),
  patchAwsCostInsightConfig: vi.fn(),
  deleteAwsCostInsightConfig: vi.fn(),
  ingestAwsCostInsightCur: vi.fn(),
  ingestAwsCostInsightInventory: vi.fn(),
  getCostByBucket: vi.fn(),
  getCostDailySeries: vi.fn(),
  getSizeByPrefix: vi.fn()
}));

import {
  getAwsCostInsightConfig,
  getCostByBucket,
  getCostDailySeries,
  getSizeByPrefix,
  listSources
} from "../api/client";

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CostInsights />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("CostInsights page", () => {
  it("renders the empty-state prompt when no source is selected", async () => {
    vi.mocked(listSources).mockResolvedValue([baseSource]);
    vi.mocked(getAwsCostInsightConfig).mockImplementation(async (_api, id) => {
      const err = new Error("not found") as Error & { status?: number };
      err.status = 404;
      throw err;
    });
    renderPage();
    expect(await screen.findByText(/Cost insights/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/Pick a lakehouse above/i)
    ).toBeInTheDocument();
  });

  it("renders configuration status badges on source cards", async () => {
    vi.mocked(listSources).mockResolvedValue([
      baseSource,
      { ...baseSource, id: "src-2", name: "staging-lakehouse", type: "rest" }
    ]);
    vi.mocked(getAwsCostInsightConfig).mockImplementation(async (_api, id) => {
      if (id === "src-1") {
        return baseConfig;
      }
      const err = new Error("not found") as Error & { status?: number };
      err.status = 404;
      throw err;
    });
    renderPage();
    expect(
      (await screen.findAllByText("sample-lakehouse")).length
    ).toBeGreaterThanOrEqual(1);
    const sourceCard = document.querySelector(".cost-insights-source-card");
    expect(sourceCard).not.toBeNull();
    expect(within(sourceCard as HTMLElement).getByText("my-cur-bucket")).toBeInTheDocument();
  });

  it("renders aggregation charts once a source is selected", async () => {
    vi.mocked(listSources).mockResolvedValue([baseSource]);
    vi.mocked(getAwsCostInsightConfig).mockResolvedValue(baseConfig);
    vi.mocked(getCostByBucket).mockResolvedValue(byBucketResponse);
    vi.mocked(getCostDailySeries).mockResolvedValue(dailyResponse);
    vi.mocked(getSizeByPrefix).mockResolvedValue(byPrefixResponse);

    renderPage();
    expect(
      await screen.findByText(/Pick a lakehouse above/i)
    ).toBeInTheDocument();
  });
});