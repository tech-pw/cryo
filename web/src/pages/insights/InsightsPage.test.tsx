import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryPerformanceData, SharedS3Report } from "../../api/client";
import type { SimilarityClustersResponse } from "../../api/types";
import { InsightsPage } from "./InsightsPage";

vi.mock("../../lib/auth", () => ({
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

vi.mock("../../api/client", () => ({
  getSimilarityClusters: vi.fn(),
  getSimilarityPairs: vi.fn(),
  getSharedS3Anomalies: vi.fn(),
  getQueryPerformanceDashboard: vi.fn(),
  listDestinations: vi.fn(),
  listSources: vi.fn(),
  listPresets: vi.fn()
}));

import {
  getQueryPerformanceDashboard,
  getSharedS3Anomalies,
  getSimilarityClusters,
  getSimilarityPairs,
  listDestinations,
  listSources
} from "../../api/client";

const EMPTY_CLUSTERS: SimilarityClustersResponse = {
  clusters: [],
  generation: null,
  total_clusters: 0,
  total_tables_in_clusters: 0,
  total_reclaimable_gb: 0
};

const POPULATED_CLUSTERS: SimilarityClustersResponse = {
  clusters: [
    {
      cluster_id: "cluster-1",
      members: [
        {
          table_id: "t1",
          table_name: "orders",
          schema_name: "store",
          total_size_gb: 100,
          role: "keep",
          keep_score: 0.8,
          keep_reason: "42 queries in last 30d",
          query_count_30d: 42
        },
        {
          table_id: "t2",
          table_name: "orders_backup",
          schema_name: "store",
          total_size_gb: 95,
          role: "drop_candidate",
          keep_score: 0,
          keep_reason: "zero reads in last 30d",
          query_count_30d: 0
        }
      ],
      total_size_gb: 195,
      reclaimable_gb: 95,
      max_pair_similarity: 0.91,
      generation: 1,
      computed_at: "2026-08-13T12:00:00Z"
    }
  ],
  generation: 1,
  total_clusters: 1,
  total_tables_in_clusters: 2,
  total_reclaimable_gb: 95
};

const EMPTY_REPORT: SharedS3Report = {
  id: null,
  generated_at: null,
  sources_harvested: 0,
  table_count: 0,
  overlapping_group_count: 0,
  overlapping_table_count: 0,
  skipped_count: 0,
  sources: [],
  groups: []
};

const EMPTY_DASHBOARD: QueryPerformanceData = {
  queries: [],
  total: 0,
  p50_latency_ms: null,
  p95_latency_ms: null,
  p99_latency_ms: null,
  avg_bytes_scanned: null,
  failed_count: 0
};

const renderPage = (path = "/insights") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/insights" element={<InsightsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("InsightsPage", () => {
  beforeEach(() => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(EMPTY_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue([]);
    vi.mocked(getSharedS3Anomalies).mockResolvedValue(EMPTY_REPORT);
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(EMPTY_DASHBOARD);
    vi.mocked(listDestinations).mockResolvedValue([]);
    vi.mocked(listSources).mockResolvedValue([]);
  });

  it("uses the Insights subtitle exactly and does not render a force graph on the duplicates list", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);

    const { container } = renderPage();

    expect(
      screen.getByText("Which tables are the same physical data, and which queries are slow?")
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Insights" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("store.orders_backup")).toBeInTheDocument();
    });

    expect(screen.getByText("Keep")).toBeInTheDocument();
    expect(screen.getByText("Drop candidate")).toBeInTheDocument();
    expect(screen.getByText("Keep").className).not.toMatch(/grade|success|warning|danger/i);
    expect(screen.getByText("Drop candidate").className).not.toMatch(/grade|success|warning|danger/i);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector("circle")).toBeNull();
    expect(screen.queryByRole("img", { name: /similarity layout/i })).not.toBeInTheDocument();
    expect(getSimilarityClusters).toHaveBeenCalledWith(expect.objectContaining({ token: "test-token" }));
    expect(getSimilarityPairs).toHaveBeenCalledWith(expect.anything(), {
      table_ids: ["t1", "t2"],
      min_score: 0,
      limit: 200
    });
  });

  it("does not treat a missing query log as 0 queries", async () => {
    renderPage("/insights?tab=queries");

    expect(await screen.findByText("Query log is missing")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set up query log ingestion" })).toHaveAttribute(
      "href",
      "/connections?tab=destinations"
    );
    expect(screen.queryByText("0 queries")).not.toBeInTheDocument();
    expect(screen.queryByText(/0 queries/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no slow queries/i)).not.toBeInTheDocument();
  });
});
