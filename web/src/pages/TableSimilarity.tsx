import { useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { getSimilarityClusters, getSimilarityPairs, runDedupPipeline, verifySimilarityPair } from "../api/client";
import type { SimilarityClusterRead, SimilarityPairRead } from "../api/types";
import { ClusterForceGraph } from "../components/dedup/ClusterForceGraph";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { exportCsv } from "../lib/exportCsv";
import { formatGb, formatNumber, formatPercent, formatTime } from "../lib/format";

// Rendering all ~498 clusters' force graphs at once would still be too much to scan --
// same information-density problem the old single-scatter-chart-of-5,245-points had, just
// moved up a level. Default to the highest-value clusters (already the page's own primary
// sort order) and let the operator explicitly ask to see more.
const DEFAULT_GRAPH_LIMIT = 12;

const roleVariant = (role: "keep" | "drop_candidate") => (role === "keep" ? "success" : "warning");

const CSV_HEADERS = [
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
];

// Exports every cluster's every member -- not just the ones with a rendered force
// graph (DEFAULT_GRAPH_LIMIT caps the UI at 12 for readability, but the underlying
// `clusters` data already holds all of them) -- so the CSV is the complete real dataset
// an operator can filter/pivot/sort in a spreadsheet, independent of what's currently
// scrolled into view on the page.
const downloadClustersCsv = (clusters: SimilarityClusterRead[]) => {
  const rows = clusters.flatMap((cluster) =>
    cluster.members.map((member) => [
      cluster.cluster_id,
      member.schema_name,
      member.table_name,
      member.table_id,
      member.role,
      member.total_size_gb,
      member.keep_score,
      member.keep_reason,
      member.query_count_30d,
      cluster.reclaimable_gb,
      cluster.max_pair_similarity
    ])
  );
  const today = new Date().toISOString().slice(0, 10);
  exportCsv(`cryo-similarity-clusters-${today}.csv`, CSV_HEADERS, rows);
};

const PairEvidenceRow = ({ pair }: { pair: SimilarityPairRead }) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const verifyMutation = useMutation({
    mutationFn: () =>
      verifySimilarityPair(api, { table_a_id: pair.table_a_id, table_b_id: pair.table_b_id }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["similarity-pairs"] })
  });

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--color-border)] py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {pair.table_a_name} <span className="text-muted-foreground">&harr;</span> {pair.table_b_name}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            schema {formatPercent(pair.schema_similarity ?? 0)} &middot; stats{" "}
            {formatPercent(pair.stats_similarity ?? 0)}
            {pair.row_jaccard_estimate !== null
              ? ` · row overlap (est.) ${formatPercent(pair.row_jaccard_estimate)}`
              : " · row overlap: not sampled"}
            {pair.naming_pattern_boost ? " · name pattern match" : ""}
          </p>
          {pair.verified_relationship ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              Stage 3 verified: <span className="font-medium">{pair.verified_relationship.replace(/_/g, " ")}</span>
              {pair.exact_row_overlap_pct !== null
                ? ` (${formatPercent(pair.exact_row_overlap_pct)} exact overlap)`
                : ""}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant={pair.file_overlap_detected ? "danger" : "secondary"} className="tabular-nums">
            {formatPercent(pair.similarity_score ?? 0)}
          </Badge>
          {pair.stage !== "verified" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => verifyMutation.mutate()}
              disabled={verifyMutation.isPending}
            >
              {verifyMutation.isPending ? "Verifying…" : "Verify exact"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export const TableSimilarity = () => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [graphLimit, setGraphLimit] = useState(DEFAULT_GRAPH_LIMIT);

  const clustersQuery = useQuery({
    queryKey: ["similarity-clusters"],
    queryFn: () => getSimilarityClusters(api)
  });
  const pairsQuery = useQuery({
    queryKey: ["similarity-pairs"],
    queryFn: () => getSimilarityPairs(api, { min_score: 0.3, limit: 100 })
  });

  const runMutation = useMutation({
    mutationFn: () => runDedupPipeline(api, { sample_rows: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["similarity-clusters"] });
      queryClient.invalidateQueries({ queryKey: ["similarity-pairs"] });
    }
  });

  const clusters = clustersQuery.data?.clusters ?? [];
  // Reclaimable-storage order is already the page's primary priority signal (design doc
  // SS7: triage by $ impact) -- reuse it for which clusters get a force graph by default.
  const graphClusters = useMemo(() => clusters.slice(0, graphLimit), [clusters, graphLimit]);

  // One real per-cluster pairs fetch each, not a single global list -- the global
  // min_score=0.3/limit=100 pairsQuery below exists for the flat evidence table, but a
  // force graph needs the REAL intra-cluster edges (every scored pair among that
  // specific cluster's members), which a fleet-wide top-100 list would routinely miss
  // for any cluster outside the very highest overall scores.
  const clusterPairQueries = useQueries({
    queries: graphClusters.map((cluster) => ({
      queryKey: ["similarity-pairs", "cluster", cluster.cluster_id],
      queryFn: () =>
        getSimilarityPairs(api, {
          min_score: 0,
          limit: 500,
          table_ids: cluster.members.map((m) => m.table_id)
        }),
      enabled: cluster.members.length > 1
    }))
  });

  const isLoading = clustersQuery.isLoading;
  const isError = clustersQuery.isError;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Table Similarity"
        subtitle="Duplicate, backup, and temp-copy detection across the lakehouse — clustered by schema, statistical, and row-set similarity."
        actions={
          <Button onClick={() => runMutation.mutate()} disabled={runMutation.isPending}>
            {runMutation.isPending ? "Running pipeline…" : "Run similarity scan"}
          </Button>
        }
      />

      {runMutation.isSuccess ? (
        <Alert
          variant={runMutation.data.errors.length ? "warning" : "success"}
          title={`Scanned ${formatNumber(runMutation.data.tables_considered)} tables`}
          description={
            <>
              Found {formatNumber(runMutation.data.candidate_pairs)} candidate pairs (
              {formatNumber(runMutation.data.pairs_sampled)} row-sampled),{" "}
              {formatNumber(runMutation.data.clusters_found)} clusters.
              {runMutation.data.errors.length
                ? ` ${runMutation.data.errors.length} warning(s) — see logs.`
                : ""}
            </>
          }
        />
      ) : null}
      {runMutation.isError ? <QueryErrorAlert error={runMutation.error} /> : null}

      {isError ? <QueryErrorAlert error={clustersQuery.error} /> : null}

      <MetricGrid columns={4}>
        <MetricTile
          label="Duplicate clusters"
          value={isLoading ? undefined : formatNumber(clustersQuery.data?.total_clusters ?? 0)}
          loading={isLoading}
        />
        <MetricTile
          label="Tables in clusters"
          value={isLoading ? undefined : formatNumber(clustersQuery.data?.total_tables_in_clusters ?? 0)}
          loading={isLoading}
        />
        <MetricTile
          label="Reclaimable storage"
          value={isLoading ? undefined : formatGb(clustersQuery.data?.total_reclaimable_gb ?? 0)}
          emphasis
          loading={isLoading}
        />
        <MetricTile
          label="Last scan generation"
          value={isLoading ? undefined : (clustersQuery.data?.generation ?? "—")}
          loading={isLoading}
        />
      </MetricGrid>

      <Card>
        <CardHeader>
          <CardTitle>Cluster similarity layout</CardTitle>
          <CardDescription>
            One force-directed layout per cluster — node position is driven by real pairwise
            similarity (more similar tables pull closer together), node size is storage size, and
            the green-outlined node is the recommended keeper. Showing the {formatNumber(graphClusters.length)}{" "}
            highest-value clusters by reclaimable storage; hover a node for details.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-96 w-full" />
          ) : clusters.length === 0 ? (
            <EmptyState
              title="No duplicate clusters found yet"
              description="Run a similarity scan to detect backup, temp, and near-duplicate tables across the fleet."
            />
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {graphClusters.map((cluster, i) => {
                  const pairsResult = clusterPairQueries[i];
                  return (
                    <div
                      key={cluster.cluster_id}
                      className="rounded-lg border border-[var(--color-border)] p-3"
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <p className="text-xs font-medium text-muted-foreground">
                          {cluster.members.length} tables
                        </p>
                        <Badge variant="warning" className="tabular-nums text-[10px]">
                          {formatGb(cluster.reclaimable_gb)} reclaimable
                        </Badge>
                      </div>
                      {pairsResult?.isLoading ? (
                        <Skeleton className="h-[240px] w-full" />
                      ) : (
                        <ClusterForceGraph
                          cluster={cluster}
                          pairs={pairsResult?.data ?? []}
                          width={320}
                          height={240}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
              {clusters.length > graphLimit ? (
                <div className="mt-4 flex justify-center">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setGraphLimit((n) => n + DEFAULT_GRAPH_LIMIT)}
                  >
                    Show {Math.min(DEFAULT_GRAPH_LIMIT, clusters.length - graphLimit)} more clusters
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div>
            <CardTitle>Clusters ranked by reclaimable storage</CardTitle>
            <CardDescription>Every recommendation shows its evidence — never a bare score.</CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloadClustersCsv(clusters)}
            disabled={clusters.length === 0}
          >
            <Download className="mr-1.5 size-3.5" />
            Download CSV
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : clusters.length === 0 ? (
            <EmptyState title="No clusters yet" description="Run a similarity scan to populate this list." />
          ) : (
            clusters.map((cluster) => (
              <div key={cluster.cluster_id} className="rounded-lg border border-[var(--color-border)] p-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-foreground">
                    {cluster.members.length} tables &middot; {formatGb(cluster.total_size_gb)} total
                  </p>
                  <Badge variant="warning" className="tabular-nums">
                    {formatGb(cluster.reclaimable_gb)} reclaimable
                  </Badge>
                </div>
                <div className="mt-3 flex flex-col gap-2">
                  {cluster.members
                    .slice()
                    .sort((a, b) => (b.keep_score ?? 0) - (a.keep_score ?? 0))
                    .map((member) => (
                      <div key={member.table_id} className="flex items-center justify-between gap-3 text-sm">
                        <div className="min-w-0">
                          <p className="truncate text-foreground">
                            {member.schema_name}.{member.table_name}
                          </p>
                          {member.keep_reason ? (
                            <p className="truncate text-xs text-muted-foreground">{member.keep_reason}</p>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <span className="tabular-nums text-xs text-muted-foreground">
                            {formatGb(member.total_size_gb)}
                          </span>
                          <Badge variant={roleVariant(member.role)}>
                            {member.role === "keep" ? "Keep" : "Drop candidate"}
                          </Badge>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Scored pairs (evidence detail)</CardTitle>
          <CardDescription>
            Every pair Stage 1/2 flagged as a candidate, with its full score breakdown. Use "Verify
            exact" to run Stage 3's real Trino row-overlap comparison for a specific pair.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {pairsQuery.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : pairsQuery.isError ? (
            <QueryErrorAlert error={pairsQuery.error} />
          ) : (pairsQuery.data ?? []).length === 0 ? (
            <EmptyState title="No scored pairs yet" description="Run a similarity scan first." />
          ) : (
            <div className="flex flex-col">
              {(pairsQuery.data ?? []).map((pair) => (
                <PairEvidenceRow key={pair.id} pair={pair} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {clustersQuery.dataUpdatedAt ? (
        <p className="text-xs text-muted-foreground">
          Last refreshed {formatTime(new Date(clustersQuery.dataUpdatedAt).toISOString())}
        </p>
      ) : null}
    </div>
  );
};
