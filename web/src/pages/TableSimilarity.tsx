import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
  ZAxis
} from "recharts";
import {
  getSimilarityClusters,
  getSimilarityPairs,
  runDedupPipeline,
  verifySimilarityPair
} from "../api/client";
import type { SimilarityClusterRead, SimilarityPairRead } from "../api/types";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { chartAxisTick, cryoColors } from "../lib/theme";
import { formatGb, formatNumber, formatPercent, formatTime } from "../lib/format";

// One color per cluster in the bubble graph, cycling if there are more clusters than
// colors -- deliberately reuses the same palette family as grade pills (theme.ts) rather
// than inventing a new one, so the page reads as part of Cryo rather than a bolted-on tool.
const CLUSTER_PALETTE = [
  cryoColors.gradeA,
  cryoColors.gradeB,
  cryoColors.gradeC,
  cryoColors.gradeD,
  cryoColors.gradeF,
  cryoColors.primary,
  cryoColors.costProjected
];

const roleVariant = (role: "keep" | "drop_candidate") => (role === "keep" ? "success" : "warning");

interface BubblePoint {
  cluster_id: string;
  clusterIndex: number;
  table_id: string;
  table_name: string;
  schema_name: string;
  role: "keep" | "drop_candidate";
  total_size_gb: number;
  keep_score: number | null;
  keep_reason: string | null;
  query_count_30d: number;
  // Layout coordinates: x = similarity-to-cluster-canonical proxy (query activity, so
  // "closer to the read axis" reads as "more actively used" at a glance), y = a stable
  // per-cluster jitter so members of the same cluster visually group without overlapping
  // exactly -- this is a relationship graph substitute cheap enough to build with
  // recharts' ScatterChart rather than pulling in a dedicated force-graph library for v1.
  x: number;
  y: number;
}

const buildBubbleData = (clusters: SimilarityClusterRead[]): BubblePoint[] => {
  const points: BubblePoint[] = [];
  clusters.forEach((cluster, clusterIndex) => {
    cluster.members.forEach((member, memberIndex) => {
      // Simple deterministic layout: each cluster occupies one x-band, members spread
      // vertically by keep_score (higher keep_score = higher on the chart, so the "keep"
      // member visually sits above its drop_candidate siblings within the same band).
      const angle = (memberIndex / Math.max(cluster.members.length, 1)) * Math.PI * 2;
      points.push({
        cluster_id: cluster.cluster_id,
        clusterIndex,
        table_id: member.table_id,
        table_name: member.table_name,
        schema_name: member.schema_name,
        role: member.role,
        total_size_gb: member.total_size_gb,
        keep_score: member.keep_score,
        keep_reason: member.keep_reason,
        query_count_30d: member.query_count_30d,
        x: clusterIndex + Math.cos(angle) * 0.32,
        y: (member.keep_score ?? 0) + Math.sin(angle) * 0.18
      });
    });
  });
  return points;
};

const BubbleTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: BubblePoint }> }) => {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-xs shadow-lg">
      <p className="font-semibold text-foreground">
        {point.schema_name}.{point.table_name}
      </p>
      <p className="mt-1 text-muted-foreground">{formatGb(point.total_size_gb)}</p>
      <p className="mt-1">
        <Badge variant={roleVariant(point.role)} className="text-[10px]">
          {point.role === "keep" ? "Keep (canonical)" : "Drop candidate"}
        </Badge>
      </p>
      {point.keep_reason ? <p className="mt-1 text-muted-foreground">{point.keep_reason}</p> : null}
      <p className="mt-1 text-muted-foreground">{point.query_count_30d} queries / 30d</p>
    </div>
  );
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
  const bubbleData = useMemo(() => buildBubbleData(clusters), [clusters]);
  const bubbleByCluster = useMemo(() => {
    const groups = new Map<number, BubblePoint[]>();
    bubbleData.forEach((point) => {
      const list = groups.get(point.clusterIndex) ?? [];
      list.push(point);
      groups.set(point.clusterIndex, list);
    });
    return groups;
  }, [bubbleData]);

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
          <CardTitle>Cluster graph</CardTitle>
          <CardDescription>
            Each bubble is a table; bubble size is storage size; color groups tables in the same
            duplicate cluster. The gold-outlined bubble in each cluster is the recommended keeper —
            everything else is a drop candidate, pending confirmation.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-96 w-full" />
          ) : bubbleData.length === 0 ? (
            <EmptyState
              title="No duplicate clusters found yet"
              description="Run a similarity scan to detect backup, temp, and near-duplicate tables across the fleet."
            />
          ) : (
            <ResponsiveContainer width="100%" height={420}>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={cryoColors.border} />
                <XAxis
                  type="number"
                  dataKey="x"
                  hide
                  domain={["dataMin - 1", "dataMax + 1"]}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name="keep score"
                  tick={chartAxisTick}
                  label={{ value: "Keep-worthiness", angle: -90, position: "insideLeft", fill: cryoColors.muted }}
                />
                <ZAxis type="number" dataKey="total_size_gb" range={[80, 1400]} name="size (GB)" />
                <RechartsTooltip content={<BubbleTooltip />} cursor={{ strokeDasharray: "3 3" }} />
                {Array.from(bubbleByCluster.entries()).map(([clusterIndex, points]) => (
                  <Scatter
                    key={clusterIndex}
                    data={points}
                    fill={CLUSTER_PALETTE[clusterIndex % CLUSTER_PALETTE.length]}
                    fillOpacity={0.75}
                    shape={(props: unknown) => {
                      const point = props as { cx: number; cy: number; payload: BubblePoint };
                      const radius = Math.max(
                        6,
                        Math.min(40, Math.sqrt(point.payload.total_size_gb) * 2)
                      );
                      return (
                        <circle
                          cx={point.cx}
                          cy={point.cy}
                          r={radius}
                          fill={CLUSTER_PALETTE[point.payload.clusterIndex % CLUSTER_PALETTE.length]}
                          fillOpacity={0.75}
                          stroke={point.payload.role === "keep" ? cryoColors.gradeA : "transparent"}
                          strokeWidth={point.payload.role === "keep" ? 2.5 : 0}
                        />
                      );
                    }}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Clusters ranked by reclaimable storage</CardTitle>
          <CardDescription>Every recommendation shows its evidence — never a bare score.</CardDescription>
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
