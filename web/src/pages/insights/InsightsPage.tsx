import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  getQueryPerformanceDashboard,
  getSharedS3Anomalies,
  refreshSharedS3Anomalies,
  getSimilarityClusters,
  getSimilarityPairs,
  runDedupPipeline,
  listDestinations,
  listSources,
  type QueryPerformanceData,
  type SharedS3Group
} from "../../api/client";
import type {
  DestinationRead,
  SimilarityClusterRead,
  SimilarityPairRead,
  SourceRead
} from "../../api/types";
import { EmptyState, PageHeader, QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { Skeleton } from "../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { useAuth } from "../../lib/auth";
import { useWriteControl } from "../../lib/useWriteControl";
import { toast } from "sonner";
import { formatBytesGb, formatDuration, formatGb, formatTime } from "../../lib/format";

const INSIGHTS_TABS = ["duplicates", "shared-paths", "queries"] as const;
type InsightsTab = (typeof INSIGHTS_TABS)[number];
const DEFAULT_TAB: InsightsTab = "duplicates";
const QUERY_WINDOW_HOURS = 24;
const QUERY_LOG_SETUP = "/connections?tab=destinations";
const SHARED_PATH_PAGE_SIZE = 25;

const isInsightsTab = (value: string | null): value is InsightsTab =>
  value === "duplicates" || value === "shared-paths" || value === "queries";

const formatRatioPercent = (value: number | null | undefined): string => {
  if (value == null || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
};

const formatLatency = (ms: number | null | undefined): string => {
  if (ms == null || Number.isNaN(ms)) return "—";
  return formatDuration(ms);
};

const errorText = (error: unknown): string => (error instanceof Error ? error.message : "Failed to load data");

const destinationQueryLogEnabled = (destination: DestinationRead, engine?: string): boolean => {
  if (destination.enabled === false) return false;
  if (destination.engine !== "trino") return false;
  if (engine && destination.engine !== engine) return false;
  const config = destination.query_log_config;
  if (!config || typeof config !== "object") return false;
  if (config.enabled !== true) return false;
  if (config.mode === "read_through") return false;
  if (typeof config.sample_rate === "number" && config.sample_rate <= 0) return false;
  return true;
};

const sourceWritesActivityOnly = (source: SourceRead): boolean => {
  const block = source.config.query_log;
  if (!block || typeof block !== "object" || Array.isArray(block)) return false;
  return (block as { mode?: unknown }).mode === "iceberg_table";
};

const pairsForCluster = (cluster: SimilarityClusterRead, pairs: SimilarityPairRead[]): SimilarityPairRead[] => {
  const ids = new Set(cluster.members.map((member) => member.table_id));
  return pairs.filter((pair) => ids.has(pair.table_a_id) && ids.has(pair.table_b_id));
};

const topPair = (pairs: SimilarityPairRead[]): SimilarityPairRead =>
  pairs.reduce((best, pair) => ((pair.similarity_score ?? -1) > (best.similarity_score ?? -1) ? pair : best));

const whyTheyMatch = (pairs: SimilarityPairRead[], status: "loading" | "error" | "ready"): string => {
  if (status === "loading") return "Loading pair evidence";
  if (status === "error") return "Pair evidence failed to load";
  if (pairs.length === 0) return "Not in the loaded pair sample";
  const pair = topPair(pairs);
  const parts: string[] = [];
  if (pair.schema_similarity != null) parts.push(`schema ${formatRatioPercent(pair.schema_similarity)}`);
  if (pair.stats_similarity != null) parts.push(`stats ${formatRatioPercent(pair.stats_similarity)}`);
  if (pair.naming_pattern_boost) parts.push("name pattern");
  if (pair.verified_relationship) parts.push(pair.verified_relationship.replace(/_/g, " "));
  return parts.length > 0 ? parts.join(", ") : "Scored, but match components were not reported";
};

const observedOverlap = (pairs: SimilarityPairRead[], status: "loading" | "error" | "ready"): string => {
  if (status === "loading") return "Loading pair evidence";
  if (status === "error") return "Pair evidence failed to load";
  if (pairs.length === 0) return "Not observed in the loaded pair sample";
  const exact = pairs.find((pair) => pair.exact_row_overlap_pct != null);
  const estimate = pairs.find((pair) => pair.row_jaccard_estimate != null);
  const fileOverlap = pairs.some((pair) => pair.file_overlap_detected);
  let overlap = "Row overlap not sampled";
  if (exact?.exact_row_overlap_pct != null) {
    overlap = `Exact row overlap ${formatRatioPercent(exact.exact_row_overlap_pct)}`;
  } else if (estimate?.row_jaccard_estimate != null) {
    overlap = `Estimated row overlap ${formatRatioPercent(estimate.row_jaccard_estimate)} (sampled)`;
  } else if (fileOverlap) {
    return "File overlap observed";
  }
  return fileOverlap ? `${overlap}; file overlap observed` : overlap;
};

const RoleLabel = ({ role }: { role: "keep" | "drop_candidate" }) => (
  <Badge variant="outline">{role === "keep" ? "Keep" : "Drop candidate"}</Badge>
);

const RetryButton = ({ onClick, pending, label }: { onClick: () => void; pending: boolean; label: string }) => (
  <Button type="button" variant="outline" size="sm" onClick={onClick} disabled={pending}>
    {label}
  </Button>
);

const DuplicatesPanel = () => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const scanAccess = useWriteControl("runDedup");
  const scan = useMutation({
    mutationFn: () => runDedupPipeline(api, { sample_rows: true }),
    onSuccess: () => {
      toast.success("Similarity scan finished");
      void queryClient.invalidateQueries({ queryKey: ["similarity-clusters"] });
      void queryClient.invalidateQueries({ queryKey: ["similarity-pairs"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Similarity scan failed")
  });
  const clustersQuery = useQuery({
    queryKey: ["similarity-clusters"],
    queryFn: () => getSimilarityClusters(api)
  });
  const clusters = useMemo(
    () => [...(clustersQuery.data?.clusters ?? [])].sort((a, b) => b.reclaimable_gb - a.reclaimable_gb),
    [clustersQuery.data?.clusters]
  );
  const pairsQuery = useQuery({
    queryKey: ["similarity-pairs", clusters.map((cluster) => cluster.cluster_id).join(",")],
    queryFn: async () => {
      const pages = await Promise.all(
        clusters.map((cluster) =>
          getSimilarityPairs(api, {
            table_ids: cluster.members.map((member) => member.table_id),
            min_score: 0,
            limit: 200
          })
        )
      );
      return pages.flat();
    },
    enabled: clusters.length > 0
  });

  const pairStatus = pairsQuery.isLoading ? "loading" : pairsQuery.isError ? "error" : "ready";
  const asOf = clusters[0]?.computed_at;

  if (clustersQuery.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (clustersQuery.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm text-foreground">
          {errorText(clustersQuery.error)}
        </p>
        <RetryButton
          label="Retry"
          pending={clustersQuery.isFetching}
          onClick={() => void clustersQuery.refetch()}
        />
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-foreground">Which tables look like the same data?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Ranked by drop-candidate size. A match is not proof that dropping one name is safe.
          {clustersQuery.data?.generation != null ? ` Scan generation ${clustersQuery.data.generation}.` : ""}
          {asOf ? ` As of ${formatTime(asOf)}.` : ""}
        </p>
      </div>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={!scanAccess.allowed || scan.isPending}
        title={scanAccess.reason ?? undefined}
        onClick={() => scan.mutate()}
      >
        {scan.isPending ? "Scanning…" : "Run similarity scan"}
      </Button>
      {clusters.length === 0 ? (
        <EmptyState
          title={
            clustersQuery.data?.generation == null
              ? "Similarity has not been scanned"
              : "No duplicate groups in the latest scan"
          }
          description={
            clustersQuery.data?.generation == null
              ? "A missing scan is not zero duplicate tables. This list fills in after a similarity scan."
              : "The latest similarity scan did not find candidate groups."
          }
        />
      ) : (
        <ol className="flex flex-col gap-4">
          {clusters.map((cluster, index) => {
            const matched = pairsForCluster(cluster, pairsQuery.data ?? []);
            const members = cluster.members
              .slice()
              .sort(
                (a, b) =>
                  (a.role === "keep" ? 0 : 1) - (b.role === "keep" ? 0 : 1) || b.total_size_gb - a.total_size_gb
              );
            return (
              <li key={cluster.cluster_id} className="rounded-lg border border-[var(--color-border)] p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground">Rank {index + 1}</h3>
                  <p className="text-xs tabular-nums text-muted-foreground">
                    Drop-candidate size {formatGb(cluster.reclaimable_gb)}
                  </p>
                </div>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-xs text-muted-foreground">Score</dt>
                    <dd>{formatRatioPercent(cluster.max_pair_similarity)} max pair similarity</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Group size</dt>
                    <dd>{formatGb(cluster.total_size_gb)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Why they match</dt>
                    <dd>{whyTheyMatch(matched, pairStatus)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Observed overlap</dt>
                    <dd>{observedOverlap(matched, pairStatus)}</dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-xs text-muted-foreground">Uncertainty</dt>
                    <dd>Similarity is not proof deletion is safe.</dd>
                  </div>
                </dl>
                <ul className="mt-3 flex flex-col gap-2">
                  {members.map((member) => (
                    <li key={member.table_id} className="flex items-start justify-between gap-3 text-sm">
                      <div className="min-w-0">
                        <Link
                          to={`/tables/${member.table_id}`}
                          className="text-[var(--color-primary)] hover:underline"
                        >
                          {member.schema_name}.{member.table_name}
                        </Link>
                        {member.keep_reason ? (
                          <p className="truncate text-xs text-muted-foreground">{member.keep_reason}</p>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="tabular-nums text-xs text-muted-foreground">
                          {formatGb(member.total_size_gb)}
                        </span>
                        <RoleLabel role={member.role} />
                      </div>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
};

const SharedPathsPanel = () => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const refreshAccess = useWriteControl("refreshAnomalies");
  const [pageIndex, setPageIndex] = useState(0);
  const reportQuery = useQuery({
    queryKey: ["shared-s3-anomalies", api.token],
    queryFn: () => getSharedS3Anomalies(api)
  });
  const refresh = useMutation({
    mutationFn: () => refreshSharedS3Anomalies(api),
    onSuccess: (data) => {
      queryClient.setQueryData(["shared-s3-anomalies", api.token], data);
      toast.success("Shared-path harvest finished");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Harvest failed")
  });
  const report = reportQuery.data;
  const groups = report?.groups ?? [];
  const pageGroups = groups.slice(pageIndex * SHARED_PATH_PAGE_SIZE, (pageIndex + 1) * SHARED_PATH_PAGE_SIZE);
  const hasHarvest = Boolean(report?.generated_at);
  const fallbackSources = (report?.sources ?? []).filter((source) => source.mode === "db_fallback" || source.error);

  const columns = useMemo(
    () => [
      {
        accessorKey: "shared_path",
        header: "Shared S3 path",
        cell: ({ row }: { row: { original: SharedS3Group } }) => (
          <code className="break-all text-xs">{row.original.shared_path}</code>
        )
      },
      {
        accessorKey: "kind",
        header: "Kind",
        cell: ({ row }: { row: { original: SharedS3Group } }) => (row.original.kind === "exact" ? "Exact" : "Nested")
      },
      {
        id: "tables",
        header: "Tables",
        cell: ({ row }: { row: { original: SharedS3Group } }) => (
          <ul className="space-y-1">
            {row.original.tables.map((member) => (
              <li key={`${member.source_id}.${member.schema_name}.${member.table_name}`}>
                {member.table_id ? (
                  <Link
                    className="text-sm text-[var(--color-primary)] hover:underline"
                    to={`/tables/${member.table_id}`}
                  >
                    {member.qualified_name}
                  </Link>
                ) : (
                  <span className="text-sm">{member.qualified_name} · Not in Cryo</span>
                )}
              </li>
            ))}
          </ul>
        )
      }
    ],
    []
  );

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-foreground">Which names share one S3 location?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Two catalog names on one warehouse prefix are one physical table. Unregister an alias, or drop it
          without purge. Cryo skips orphan removal on these names. Expire snapshots does not delete the sibling.
        </p>
      </div>

      {reportQuery.isError ? (
        <div className="flex flex-col items-start gap-3">
          <QueryErrorAlert error={reportQuery.error} />
          <RetryButton label="Retry" pending={reportQuery.isFetching} onClick={() => void reportQuery.refetch()} />
        </div>
      ) : null}

      {reportQuery.isLoading ? <Skeleton className="h-40 w-full" /> : null}

      {fallbackSources.length > 0 ? (
        <Alert
          variant="warning"
          title="Some catalogs used scanned locations instead of live metastore metadata"
          description={fallbackSources
            .map((source) => `${source.source_name} (${source.mode}${source.error ? `: ${source.error}` : ""})`)
            .join(" · ")}
        />
      ) : null}

      {!reportQuery.isLoading && !reportQuery.isError && !hasHarvest ? (
        <EmptyState
          title="Shared-path overlap has not been measured"
          description="No harvest yet. A missing harvest is not zero overlapping paths."
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!refreshAccess.allowed || refresh.isPending}
              title={refreshAccess.reason ?? undefined}
              onClick={() => refresh.mutate()}
            >
              {refresh.isPending ? "Harvesting…" : "Refresh from catalogs"}
            </Button>
          }
        />
      ) : null}

      {!reportQuery.isLoading && !reportQuery.isError && hasHarvest && groups.length === 0 ? (
        <EmptyState
          title="No overlapping S3 locations"
          description={`The last harvest${report?.generated_at ? ` (${formatTime(report.generated_at)})` : ""} found no shared warehouse prefixes.`}
        />
      ) : null}

      {groups.length > 0 ? (
        <>
          <p className="text-xs text-muted-foreground">
            Last harvest {formatTime(report?.generated_at)}. Do not drop a name until the shared path is unique.
          </p>
          <DataTable
            columns={columns}
            data={pageGroups}
            getRowId={(row) => row.shared_path}
            emptyTitle="No overlapping groups"
            pagination={{
              pageIndex,
              pageSize: SHARED_PATH_PAGE_SIZE,
              totalRows: groups.length,
              onPageChange: setPageIndex
            }}
          />
        </>
      ) : null}
    </section>
  );
};

const QuerySetupLink = ({ children }: { children: string }) => (
  <Link to={QUERY_LOG_SETUP} className="text-[var(--color-primary)] hover:underline">
    {children}
  </Link>
);

const QueryResults = ({
  data,
  hours,
  engine
}: {
  data: QueryPerformanceData;
  hours: number;
  engine?: string;
}) => {
  const columns = useMemo(
    () => [
      {
        id: "query_text",
        header: "Query",
        cell: ({ row }: { row: { original: QueryPerformanceData["queries"][number] } }) => {
          const text = row.original.query_text;
          if (!text) return <span className="text-muted-foreground">Query text not captured</span>;
          const preview = text.length > 80 ? `${text.slice(0, 80)}…` : text;
          return (
            <code className="text-[11px]" title={text}>
              {preview}
            </code>
          );
        }
      },
      {
        id: "engine",
        header: "Engine",
        cell: ({ row }: { row: { original: QueryPerformanceData["queries"][number] } }) => row.original.engine
      },
      {
        id: "duration_ms",
        header: "Duration",
        cell: ({ row }: { row: { original: QueryPerformanceData["queries"][number] } }) =>
          formatDuration(row.original.duration_ms)
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }: { row: { original: QueryPerformanceData["queries"][number] } }) => row.original.status
      },
      {
        id: "table_name",
        header: "Table",
        cell: ({ row }: { row: { original: QueryPerformanceData["queries"][number] } }) => {
          const name = row.original.table_name;
          if (!name) return "—";
          if (row.original.table_id) {
            return (
              <Link to={`/tables/${row.original.table_id}`} className="text-[var(--color-primary)] hover:underline">
                {name}
              </Link>
            );
          }
          return name;
        }
      }
    ],
    []
  );

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Window</dt>
          <dd>Last {hours} hours</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Workload scope</dt>
          <dd>{engine ?? "All engines"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Query count</dt>
          <dd>
            {data.total === 1
              ? "1 query in this window"
              : data.total > 1
                ? `${data.total} queries in this window`
                : "Count not available"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">P50</dt>
          <dd>{formatLatency(data.p50_latency_ms)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">P95</dt>
          <dd>{formatLatency(data.p95_latency_ms)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">P99</dt>
          <dd>{formatLatency(data.p99_latency_ms)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Average data scanned</dt>
          <dd>{data.avg_bytes_scanned == null ? "—" : formatBytesGb(data.avg_bytes_scanned)}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs text-muted-foreground">Failures</dt>
          <dd>
            {data.failed_count > 0
              ? `${data.failed_count} failed among the slowest queries returned`
              : "No failures among the slowest queries returned"}
          </dd>
        </div>
      </dl>
      <h3 className="text-sm font-semibold text-foreground">Slowest queries</h3>
      <DataTable
        columns={columns}
        data={data.queries}
        getRowId={(row) => row.query_id}
        emptyTitle="Queries were counted but not listed"
        emptyDescription="The window has a count, but no query rows were returned."
      />
    </div>
  );
};

const queryWindowHours = (raw: string | null): number => {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 168) return QUERY_WINDOW_HOURS;
  return Math.floor(parsed);
};

const QueriesPanel = () => {
  const { api } = useAuth();
  const [searchParams] = useSearchParams();
  const hours = queryWindowHours(searchParams.get("hours"));
  const engine = searchParams.get("engine")?.trim() || undefined;
  const performanceQuery = useQuery({
    queryKey: ["query-performance", api.token, engine, hours],
    queryFn: () => getQueryPerformanceDashboard(api, hours, engine)
  });
  const destinationsQuery = useQuery({
    queryKey: ["destinations", api.token],
    queryFn: () => listDestinations(api)
  });
  const sourcesQuery = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api)
  });

  const total = performanceQuery.data?.total ?? 0;
  const listed = performanceQuery.data?.queries.length ?? 0;
  const hasRows = performanceQuery.isSuccess && (total > 0 || listed > 0);
  const coverageLoading =
    !hasRows && (performanceQuery.isLoading || destinationsQuery.isLoading || sourcesQuery.isLoading);

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-foreground">Which queries are slow?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Named percentiles over the last {hours} hours{engine ? `, ${engine}` : ", all engines"}. A missing query log is not a count
          of slow queries.
        </p>
      </div>

      {coverageLoading ? <Skeleton className="h-40 w-full" /> : null}

      {performanceQuery.isError ? (
        <div className="flex flex-col items-start gap-3">
          <QueryErrorAlert error={performanceQuery.error} />
          <RetryButton
            label="Retry"
            pending={performanceQuery.isFetching}
            onClick={() => void performanceQuery.refetch()}
          />
        </div>
      ) : null}

      {hasRows && performanceQuery.data ? (
        <QueryResults data={performanceQuery.data} hours={hours} engine={engine} />
      ) : null}

      {!coverageLoading && !performanceQuery.isError && !hasRows && sourcesQuery.isSuccess && sourcesQuery.data?.some(sourceWritesActivityOnly) ? (
        <QueryCoverageNotice connected={false} activityOnly hours={hours} />
      ) : !coverageLoading && !performanceQuery.isError && !hasRows && (destinationsQuery.isError || sourcesQuery.isError) ? (
        <Alert
          variant="info"
          title="Query log status is unknown"
          description="Cryo could not check whether query-log ingestion is enabled. An empty window is not a count of slow queries."
        />
      ) : !coverageLoading && !performanceQuery.isError && !hasRows && destinationsQuery.isSuccess && sourcesQuery.isSuccess ? (
        <QueryCoverageNotice
          connected={(destinationsQuery.data ?? []).some((destination) => destinationQueryLogEnabled(destination, engine))}
          activityOnly={false}
          hours={hours}
          engine={engine}
        />
      ) : null}
    </section>
  );
};

const QueryCoverageNotice = ({
  connected,
  activityOnly,
  hours,
  engine
}: {
  connected: boolean;
  activityOnly: boolean;
  hours: number;
  engine?: string;
}) => {
  if (connected) {
    return (
      <Alert
        variant="info"
        title={`No query log rows in the last ${hours} hours`}
        description={
          <span>
            Query-log ingestion is enabled. Workload scope is {engine ?? "all engines"}. This window has no rows, so percentiles are
            not available.{" "}
            <QuerySetupLink>Inspect query log ingestion</QuerySetupLink>
          </span>
        }
      />
    );
  }

  if (activityOnly) {
    return (
      <Alert
        variant="info"
        title="Query log rows are missing"
        description={
          <span>
            A source reads an Iceberg query-log table into table activity. That path does not write the query log this
            list reads, so slow queries are not available.{" "}
            <QuerySetupLink>Set up query log ingestion</QuerySetupLink>
          </span>
        }
      />
    );
  }

  return (
    <Alert
      variant="info"
      title="Query log is missing"
      description={
        <span>
          Slow queries need query-log ingestion on an engine. None is enabled, and this list has no query log rows. A
          missing log is not a count of slow queries.{" "}
          <QuerySetupLink>Set up query log ingestion</QuerySetupLink>
        </span>
      }
    />
  );
};

export const InsightsPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: InsightsTab = isInsightsTab(tabParam) ? tabParam : DEFAULT_TAB;

  const setTab = (next: string) => {
    if (!isInsightsTab(next)) return;
    const params = new URLSearchParams(searchParams);
    if (next === DEFAULT_TAB) params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Insights"
        subtitle="Which tables are the same physical data, and which queries are slow?"
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="Insights">
          <TabsTrigger value="duplicates">Duplicates</TabsTrigger>
          <TabsTrigger value="shared-paths">Shared paths</TabsTrigger>
          <TabsTrigger value="queries">Queries</TabsTrigger>
        </TabsList>
        <TabsContent value="duplicates">{tab === "duplicates" ? <DuplicatesPanel /> : null}</TabsContent>
        <TabsContent value="shared-paths">{tab === "shared-paths" ? <SharedPathsPanel /> : null}</TabsContent>
        <TabsContent value="queries">{tab === "queries" ? <QueriesPanel /> : null}</TabsContent>
      </Tabs>
    </div>
  );
};
