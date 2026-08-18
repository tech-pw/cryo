import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Anomalies } from "./Anomalies";
import type { SharedS3Report } from "../api/client";
import { ThemeProvider } from "../lib/themeContext";

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
  getSharedS3Anomalies: vi.fn(),
  refreshSharedS3Anomalies: vi.fn()
}));

import { getSharedS3Anomalies, refreshSharedS3Anomalies } from "../api/client";

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter>
          <Anomalies />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

const report = (overrides: Partial<SharedS3Report> = {}): SharedS3Report => ({
  id: "rep-1",
  generated_at: "2026-08-18T04:30:00Z",
  sources_harvested: 2,
  table_count: 4,
  overlapping_group_count: 1,
  overlapping_table_count: 2,
  skipped_count: 0,
  sources: [
    {
      source_id: "src-1",
      source_name: "local-hms",
      source_type: "hive",
      mode: "live",
      table_count: 4,
      skipped_count: 0,
      error: null
    }
  ],
  groups: [
    {
      shared_path: "s3://warehouse/demo/orders",
      kind: "exact",
      table_count: 2,
      tables: [
        {
          source_id: "src-1",
          source_name: "local-hms",
          source_type: "hive",
          schema_name: "demo",
          table_name: "orders",
          location: "s3://warehouse/demo/orders",
          table_id: "tbl-orders",
          qualified_name: "local-hms.demo.orders"
        },
        {
          source_id: "src-1",
          source_name: "local-hms",
          source_type: "hive",
          schema_name: "demo",
          table_name: "orders_evolving",
          location: "s3://warehouse/demo/orders",
          table_id: null,
          qualified_name: "local-hms.demo.orders_evolving"
        }
      ]
    }
  ],
  ...overrides
});

describe("Anomalies page", () => {
  beforeEach(() => {
    vi.mocked(getSharedS3Anomalies).mockReset();
    vi.mocked(refreshSharedS3Anomalies).mockReset();
  });

  it("renders overlapping groups with catalog names", async () => {
    vi.mocked(getSharedS3Anomalies).mockResolvedValue(report());
    renderPage();
    expect(await screen.findByText("s3://warehouse/demo/orders")).toBeInTheDocument();
    expect(screen.getByText("local-hms.demo.orders")).toBeInTheDocument();
    expect(screen.getByText("local-hms.demo.orders_evolving")).toBeInTheDocument();
    expect(screen.getByText("Exact")).toBeInTheDocument();
  });

  it("shows an empty harvest state when no snapshot exists", async () => {
    vi.mocked(getSharedS3Anomalies).mockResolvedValue({
      id: null,
      generated_at: null,
      sources_harvested: 0,
      table_count: 0,
      overlapping_group_count: 0,
      overlapping_table_count: 0,
      skipped_count: 0,
      sources: [],
      groups: []
    });
    renderPage();
    expect(await screen.findByText("No shared-S3 harvest yet")).toBeInTheDocument();
  });

  it("refreshes from catalogs when the operator clicks harvest", async () => {
    vi.mocked(getSharedS3Anomalies).mockResolvedValue(report({ groups: [], overlapping_group_count: 0 }));
    vi.mocked(refreshSharedS3Anomalies).mockResolvedValue(report());
    renderPage();
    await screen.findByRole("button", { name: /refresh from catalogs/i });
    fireEvent.click(screen.getByRole("button", { name: /refresh from catalogs/i }));
    await waitFor(() => expect(refreshSharedS3Anomalies).toHaveBeenCalled());
    expect(await screen.findByText("local-hms.demo.orders_evolving")).toBeInTheDocument();
  });
});
