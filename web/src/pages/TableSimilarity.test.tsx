import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TableSimilarity } from "./TableSimilarity";

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
  getSimilarityClusters: vi.fn(),
  getSimilarityPairs: vi.fn(),
  runDedupPipeline: vi.fn(),
  verifySimilarityPair: vi.fn()
}));

vi.mock("../lib/exportCsv", () => ({
  exportCsv: vi.fn()
}));

import { getSimilarityClusters, getSimilarityPairs } from "../api/client";
import { exportCsv } from "../lib/exportCsv";
import type { SimilarityClustersResponse, SimilarityPairRead } from "../api/types";

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
          table_name: "silver_infinite_practice_result_questions",
          schema_name: "cdp_revenue",
          total_size_gb: 5187.9,
          role: "keep",
          keep_score: 1.2,
          keep_reason: "1200 queries in last 30d",
          query_count_30d: 1200
        },
        {
          table_id: "t2",
          table_name: "silver_infinite_practice_result_questions_temp",
          schema_name: "cdp_revenue",
          total_size_gb: 4969.4,
          role: "drop_candidate",
          keep_score: -0.1,
          keep_reason: "zero reads in last 30d; name matches backup/temp/copy pattern",
          query_count_30d: 0
        }
      ],
      total_size_gb: 10157.3,
      reclaimable_gb: 4969.4,
      max_pair_similarity: 0.91,
      generation: 3,
      computed_at: "2026-08-13T12:00:00Z"
    }
  ],
  generation: 3,
  total_clusters: 1,
  total_tables_in_clusters: 2,
  total_reclaimable_gb: 4969.4
};

const POPULATED_PAIRS: SimilarityPairRead[] = [
  {
    id: "pair-1",
    table_a_id: "t1",
    table_a_name: "cdp.cdp_revenue.silver_infinite_practice_result_questions",
    table_b_id: "t2",
    table_b_name: "cdp.cdp_revenue.silver_infinite_practice_result_questions_temp",
    stage: "scored",
    similarity_score: 0.91,
    schema_similarity: 1.0,
    stats_similarity: 0.96,
    row_jaccard_estimate: 0.87,
    naming_pattern_boost: 0.08,
    file_overlap_detected: false,
    verified_relationship: null,
    exact_row_overlap_pct: null,
    score_components: {},
    computed_at: "2026-08-13T12:00:00Z"
  }
];

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/similarity"]}>
        <Routes>
          <Route path="/similarity" element={<TableSimilarity />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("TableSimilarity page", () => {
  it("renders an empty state when there are no clusters yet", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(EMPTY_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue([]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No duplicate clusters found yet/i)).toBeInTheDocument();
    });
  });

  it("renders cluster members with role and reclaimable storage", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue(POPULATED_PAIRS);

    renderPage();

    await waitFor(() => {
      expect(
        screen.getAllByText(/silver_infinite_practice_result_questions_temp/i).length
      ).toBeGreaterThan(0);
    });

    // Both roles should be visible somewhere on the page (cluster list + bubble tooltip
    // is not queried here, but the ranked-cluster card always renders role badges).
    expect(screen.getAllByText(/Keep/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Drop candidate/i).length).toBeGreaterThan(0);
  });

  it("renders the evidence breakdown for a scored pair, not a bare score", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue(POPULATED_PAIRS);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/row overlap \(est\.\)/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/name pattern match/i)).toBeInTheDocument();
  });

  it("shows a Verify exact action for pairs not yet Stage-3 verified", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue(POPULATED_PAIRS);

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /verify exact/i })).toBeInTheDocument();
    });
  });

  it("does not show Verify exact once a pair is already Stage-3 verified", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue([
      { ...POPULATED_PAIRS[0], stage: "verified", verified_relationship: "near_duplicate", exact_row_overlap_pct: 0.93 }
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/near duplicate/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /verify exact/i })).not.toBeInTheDocument();
  });

  it("downloads a CSV with every cluster's members, cluster ID, and role when clicked", async () => {
    vi.mocked(getSimilarityClusters).mockResolvedValue(POPULATED_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue(POPULATED_PAIRS);

    renderPage();

    // Wait for real cluster data to render, not just for the button to exist -- the
    // button is present (disabled) during the initial loading state too, since
    // `clusters` defaults to [] before the query resolves.
    await waitFor(() => {
      expect(screen.getAllByText(/silver_infinite_practice_result_questions_temp/i).length).toBeGreaterThan(0);
    });
    const downloadButton = screen.getByRole("button", { name: /download csv/i });
    expect(downloadButton).toBeEnabled();
    fireEvent.click(downloadButton);

    expect(exportCsv).toHaveBeenCalledTimes(1);
    const [filename, headers, rows] = vi.mocked(exportCsv).mock.calls[0];

    expect(filename).toMatch(/^cryo-similarity-clusters-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(headers).toEqual([
      "cluster_id",
      "schema_name",
      "table_name",
      "table_id",
      "role",
      "total_size_gb",
      "keep_score",
      "keep_reason",
      "query_count_30d",
      "cluster_reclaimable_gb",
      "cluster_max_pair_similarity"
    ]);

    // One row per cluster member, both members of the one populated cluster included,
    // each carrying its cluster_id so a downstream analyst can group/pivot by cluster.
    expect(rows).toHaveLength(2);
    expect(rows).toContainEqual([
      "cluster-1",
      "cdp_revenue",
      "silver_infinite_practice_result_questions",
      "t1",
      "keep",
      5187.9,
      1.2,
      "1200 queries in last 30d",
      1200,
      4969.4,
      0.91
    ]);
    expect(rows).toContainEqual([
      "cluster-1",
      "cdp_revenue",
      "silver_infinite_practice_result_questions_temp",
      "t2",
      "drop_candidate",
      4969.4,
      -0.1,
      "zero reads in last 30d; name matches backup/temp/copy pattern",
      0,
      4969.4,
      0.91
    ]);
  });

  it("disables the Download CSV button when there are no clusters", async () => {
    // Real browsers block clicks on a disabled button entirely (disabled:pointer-events-none
    // in Button's own class list) -- jsdom's fireEvent.click doesn't compute pointer-events,
    // so it would still invoke the handler even on a disabled button, making a "click it and
    // assert no-op" test misleading here. Asserting the disabled attribute itself is the
    // real, meaningful check: it's what actually blocks a real user.
    vi.mocked(getSimilarityClusters).mockResolvedValue(EMPTY_CLUSTERS);
    vi.mocked(getSimilarityPairs).mockResolvedValue([]);

    renderPage();

    const downloadButton = await screen.findByRole("button", { name: /download csv/i });
    expect(downloadButton).toBeDisabled();
  });
});
