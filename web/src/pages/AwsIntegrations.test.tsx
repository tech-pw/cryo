import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AwsIntegrations } from "./AwsIntegrations";

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

const connectedIntegration = {
  id: "aws-1",
  source_id: "src-1",
  bucket: "lake-bucket",
  region: "ap-south-1",
  access_key_id: null,
  enabled: true,
  last_tested_at: "2026-07-06T10:00:00Z",
  last_test_ok: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const usagePayload = {
  bucket: "lake-bucket",
  region: "ap-south-1",
  measured_at: "2026-07-06T10:00:00Z",
  size_bytes_by_storage_class: { StandardStorage: 50 * 1024 ** 3 },
  total_size_bytes: 50 * 1024 ** 3,
  object_count: 12345,
  price_per_gb_month: 0.023,
  price_source: "aws_price_list_api"
};

vi.mock("../api/client", () => ({
  listSources: vi.fn(),
  getAwsIntegration: vi.fn(),
  getAwsUsage: vi.fn(),
  testAwsIntegration: vi.fn(),
  createAwsIntegration: vi.fn(),
  patchAwsIntegration: vi.fn(),
  deleteAwsIntegration: vi.fn()
}));

import { getAwsIntegration, getAwsUsage, listSources, testAwsIntegration } from "../api/client";

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AwsIntegrations />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("AwsIntegrations page", () => {
  it("shows empty state when no lakehouses exist", async () => {
    vi.mocked(listSources).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/AWS integrations/i)).toBeInTheDocument();
    expect(await screen.findByText(/No lakehouses yet/i)).toBeInTheDocument();
  });

  it("shows a card per lakehouse and a Connect CTA for unconfigured sources", async () => {
    vi.mocked(listSources).mockResolvedValue([
      baseSource,
      { ...baseSource, id: "src-2", name: "staging-lakehouse", type: "rest" }
    ]);
    // Both lakehouses missing (404)
    vi.mocked(getAwsIntegration).mockImplementation(async (_api, id) => {
      const err = new Error("not found") as Error & { status?: number };
      err.status = 404;
      throw err;
    });
    renderPage();

    expect(await screen.findByText("sample-lakehouse")).toBeInTheDocument();
    expect(await screen.findByText("staging-lakehouse")).toBeInTheDocument();
    const connectButtons = await screen.findAllByRole("button", { name: /connect aws/i });
    expect(connectButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("renders live bucket stats when an integration is connected", async () => {
    vi.mocked(listSources).mockResolvedValue([baseSource]);
    vi.mocked(getAwsIntegration).mockResolvedValue(connectedIntegration);
    vi.mocked(getAwsUsage).mockResolvedValue(usagePayload);
    renderPage();

    const card = (await screen.findByText("sample-lakehouse")).closest(".ant-card") as HTMLElement;
    expect(card).not.toBeNull();
    const scoped = within(card);
    expect(scoped.getByText("lake-bucket")).toBeInTheDocument();
    expect(scoped.getByText("ap-south-1")).toBeInTheDocument();
    // 50 GB rendered via formatBytesGb after the bulk usage fetch resolves
    expect(await scoped.findByText(/50\s*GB/i)).toBeInTheDocument();
  });

  it("surfaces auth-failure warning and Test-all button when an integration has last_test_ok=false", async () => {
    vi.mocked(listSources).mockResolvedValue([baseSource]);
    vi.mocked(getAwsIntegration).mockResolvedValue({
      ...connectedIntegration,
      last_test_ok: false,
      last_tested_at: "2026-07-06T10:00:00Z"
    });
    vi.mocked(getAwsUsage).mockResolvedValue(usagePayload);
    vi.mocked(testAwsIntegration).mockResolvedValue({
      ok: true,
      account_id: "123",
      arn: null,
      error: null
    });
    renderPage();

    expect(await screen.findByText(/failing auth/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /test all/i })).toBeInTheDocument();
  });
});