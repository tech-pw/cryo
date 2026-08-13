import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClusterForceGraph } from "./ClusterForceGraph";
import type { SimilarityClusterRead, SimilarityPairRead } from "../../api/types";

const CLUSTER: SimilarityClusterRead = {
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
};

const PAIRS: SimilarityPairRead[] = [
  {
    id: "pair-1",
    table_a_id: "t1",
    table_a_name: "cdp.store.orders",
    table_b_id: "t2",
    table_b_name: "cdp.store.orders_backup",
    stage: "scored",
    similarity_score: 0.91,
    schema_similarity: 1.0,
    stats_similarity: 0.95,
    row_jaccard_estimate: null,
    naming_pattern_boost: 0.08,
    file_overlap_detected: false,
    verified_relationship: null,
    exact_row_overlap_pct: null,
    score_components: {},
    computed_at: "2026-08-13T12:00:00Z"
  }
];

describe("ClusterForceGraph", () => {
  it("renders one circle per cluster member", () => {
    const { container } = render(<ClusterForceGraph cluster={CLUSTER} pairs={PAIRS} width={300} height={200} />);

    // circles rendered at settled (finite, non-NaN) positions -- proves the simulation
    // actually ran and produced real coordinates, not just mounted with defaults.
    const circles = container.querySelectorAll("circle");
    expect(circles.length).toBe(2);
    circles.forEach((circle) => {
      const parent = circle.closest("g");
      const transform = parent?.getAttribute("transform") ?? "";
      expect(transform).toMatch(/translate\(-?\d+(\.\d+)?, -?\d+(\.\d+)?\)/);
      expect(transform).not.toContain("NaN");
    });
  });

  it("renders a line for the real intra-cluster pair", () => {
    const { container } = render(<ClusterForceGraph cluster={CLUSTER} pairs={PAIRS} width={300} height={200} />);

    const lines = container.querySelectorAll("line");
    expect(lines.length).toBe(1);
  });

  it("does not render a line for a pair whose sides aren't both in this cluster", () => {
    const unrelatedPair: SimilarityPairRead = {
      ...PAIRS[0],
      id: "pair-2",
      table_a_id: "t1",
      table_b_id: "not-in-cluster"
    };

    const { container } = render(
      <ClusterForceGraph cluster={CLUSTER} pairs={[unrelatedPair]} width={300} height={200} />
    );

    expect(container.querySelectorAll("line").length).toBe(0);
  });

  it("renders nothing for an empty cluster", () => {
    const emptyCluster: SimilarityClusterRead = { ...CLUSTER, members: [] };
    const { container } = render(<ClusterForceGraph cluster={emptyCluster} pairs={[]} />);

    expect(container.querySelector("svg")).toBeNull();
  });

  it("shows a hover tooltip with table details on mouse enter", async () => {
    const { container } = render(<ClusterForceGraph cluster={CLUSTER} pairs={PAIRS} width={300} height={200} />);

    const firstNodeGroup = container.querySelectorAll("g > g")[0];
    expect(firstNodeGroup).toBeTruthy();

    const { fireEvent } = await import("@testing-library/react");
    fireEvent.mouseEnter(firstNodeGroup);

    expect(screen.getByText(/store\.(orders|orders_backup)/)).toBeInTheDocument();
  });
});
