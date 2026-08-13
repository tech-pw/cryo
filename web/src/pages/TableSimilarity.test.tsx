import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

import { getSimilarityClusters, getSimilarityPairs } from "../api/client";
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
});
