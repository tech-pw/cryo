import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  getQueryPerformanceDashboard,
  getTableLineage,
  getTablePartitionAccess,
  getUnionable,
  type QueryPerformanceData
} from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatDuration, formatTime } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { ChartCard } from "../ChartCard";
import { LineageGraph } from "../LineageGraph";
import { PartitionAccessChart } from "../PartitionAccessChart";
import { UnionableTab } from "../UnionableTab";

const QUERY_WINDOW_HOURS = 168;
const QUERY_WINDOW = "the last 168 hours";

const shortenSql = (sql: string | null): string => {
  if (!sql) return "SQL not recorded";
  const oneLine = sql.replace(/\s+/g, " ").trim();
  return oneLine.length > 140 ? `${oneLine.slice(0, 140)}…` : oneLine;
};

const latencyText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "Not measured" : formatDuration(value);

const coverageText = (data: QueryPerformanceData): string => {
  const listed = data.queries.length;
  if (data.total === 0) {
    return "Coverage: query log returned no rows in this window. Telemetry coverage is not measured.";
  }
  if (data.total > listed) {
    return `Coverage: ${listed} of ${data.total} query-log rows in this window. Telemetry coverage beyond the query log is not measured.`;
  }
  return `Coverage: ${data.total} query-log rows in this window.`;
};

const QueryActivity = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const query = useQuery({
    queryKey: tableKeys.queries(tableId, token),
    queryFn: () => getQueryPerformanceDashboard(api, QUERY_WINDOW_HOURS, undefined, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const data = query.data;

  return (
    <section id="usage-queries" aria-label="Query activity">
      <ChartCard title="Query activity" subtitle={`Window: ${QUERY_WINDOW}`}>
        <p className="text-xs text-muted-foreground">Window: {QUERY_WINDOW}</p>
        {query.isLoading ? <p className="mt-2 text-sm text-muted-foreground">Loading query activity…</p> : null}
        {query.isError ? (
          <QueryErrorAlert error={query.error} title="Failed to load query activity" />
        ) : null}
        {data ? (
          <div className="mt-2 flex flex-col gap-3">
            <p className="text-xs text-muted-foreground">Sample size: {data.queries.length}</p>
            <p className="text-xs text-muted-foreground">{coverageText(data)}</p>
            {data.queries.length === 0 ? (
              <p className="text-sm">No reads observed in {QUERY_WINDOW}</p>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                  <div>
                    <dt className="text-xs text-muted-foreground">Queries</dt>
                    <dd>{data.total}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">p50 latency</dt>
                    <dd>{latencyText(data.p50_latency_ms)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">p95 latency</dt>
                    <dd>{latencyText(data.p95_latency_ms)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Bytes scanned</dt>
                    <dd>{data.avg_bytes_scanned == null ? "Not measured" : formatBytesGb(data.avg_bytes_scanned)}</dd>
                  </div>
                </dl>
                <table className="w-full text-sm">
                  <caption className="sr-only">Recent queries in {QUERY_WINDOW}</caption>
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted-foreground">
                      <th className="py-1.5 pr-3 font-medium">SQL</th>
                      <th className="py-1.5 pr-3 font-medium">Time</th>
                      <th className="py-1.5 pr-3 font-medium">Duration</th>
                      <th className="py-1.5 font-medium">User</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.queries.map((row) => (
                      <tr key={row.query_id} className="border-b border-border/60">
                        <td className="max-w-md py-1.5 pr-3 font-mono text-xs">{shortenSql(row.query_text)}</td>
                        <td className="py-1.5 pr-3">{formatTime(row.started_at)}</td>
                        <td className="py-1.5 pr-3">{latencyText(row.duration_ms)}</td>
                        <td className="py-1.5">{row.user ?? "Not recorded"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        ) : null}
      </ChartCard>
    </section>
  );
};

const PartitionAccess = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const query = useQuery({
    queryKey: tableKeys.partitionAccess(tableId, token),
    queryFn: () => getTablePartitionAccess(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const points = query.data?.points ?? [];
  const partitions = [...(query.data?.partitions ?? [])].sort((a, b) =>
    (a.partition_path || "").localeCompare(b.partition_path || "")
  );

  return (
    <section id="usage-partition-access" aria-label="Partition access" className="mt-4">
      <ChartCard title="Partition access">
        <p className="mb-2 text-xs text-muted-foreground">
          Partitions stay in a stable order. Day is on the horizontal axis. Intensity is request count.
        </p>
        {query.isLoading ? <p className="text-sm text-muted-foreground">Loading partition access…</p> : null}
        {query.isError ? (
          <QueryErrorAlert error={query.error} title="Failed to load partition access" />
        ) : null}
        {query.data ? (
          <PartitionAccessChart points={points} partitions={partitions} />
        ) : null}
      </ChartCard>
    </section>
  );
};

const LineageList = ({
  title,
  nodes
}: {
  title: string;
  nodes: Array<{ table_id: string; table_name: string; depth?: number; node_type?: string }>;
}) => (
  <section aria-label={title}>
    <h3 className="mb-1 text-sm font-medium">{title}</h3>
    {nodes.length === 0 ? (
      <p className="text-sm text-muted-foreground">None</p>
    ) : (
      <ul className="m-0 list-none space-y-1 p-0 text-sm">
        {nodes.map((node) => (
          <li key={node.table_id}>
            <Link to={`/tables/${node.table_id}`}>{node.table_name}</Link>
            {node.node_type ? <span className="text-muted-foreground"> · {node.node_type}</span> : null}
            {node.depth != null ? <span className="text-muted-foreground"> · depth {node.depth}</span> : null}
          </li>
        ))}
      </ul>
    )}
  </section>
);

const Lineage = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const query = useQuery({
    queryKey: ["table-lineage", token, tableId],
    queryFn: () => getTableLineage(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });

  return (
    <section id="usage-lineage" aria-label="Lineage" className="mt-4">
      <ChartCard title="Lineage">
        {query.isError ? (
          <QueryErrorAlert error={query.error} title="Failed to load lineage" />
        ) : (
          <LineageGraph data={query.data} isLoading={query.isLoading} error={null} />
        )}
        {query.data ? (
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <LineageList title="Upstream tables" nodes={query.data.upstream ?? []} />
            <LineageList title="Downstream tables" nodes={query.data.downstream ?? []} />
          </div>
        ) : null}
      </ChartCard>
    </section>
  );
};

const SameShape = ({ tableId }: { tableId: string }) => {
  const { api } = useAuth();
  const query = useQuery({
    queryKey: ["unionable", tableId],
    queryFn: () => getUnionable(api, tableId, { k: 10, useLlm: false }),
    enabled: Boolean(tableId),
    retry: false
  });
  const results = query.data?.results ?? [];

  return (
    <section id="usage-same-shape" aria-label="Same-shape tables" className="mt-4">
      <ChartCard
        title="Same-shape tables"
        actions={
          <Link to="/insights?tab=duplicates" className="text-xs font-medium text-primary hover:underline">
            Insights → Duplicates
          </Link>
        }
      >
        {query.isLoading ? <p className="text-sm text-muted-foreground">Loading same-shape tables…</p> : null}
        {query.isError ? (
          <QueryErrorAlert error={query.error} title="Failed to load same-shape tables" />
        ) : null}
        {query.data && results.length === 0 ? (
          <p className="text-sm text-muted-foreground">No same-shape tables in this sample.</p>
        ) : null}
        {results.length ? (
          <ul className="m-0 list-none space-y-3 p-0 text-sm">
            {results.slice(0, 5).map((row) => (
              <li key={row.candidate_table_id} className="border-b border-border/60 pb-2">
                <Link to={`/tables/${row.candidate_table_id}`} className="font-medium">
                  {row.candidate_schema_name ? `${row.candidate_schema_name}.` : ""}
                  {row.candidate_table_name || row.candidate_table_id}
                </Link>
                <p className="text-xs text-muted-foreground">
                  Score {Math.round(row.score * 100)}% · overlap {row.overlap_columns.slice(0, 5).join(", ") || "Not measured"}
                </p>
                <p className="text-xs">{row.rationale}</p>
                {row.union_sql ? (
                  <details className="mt-1 text-xs">
                    <summary>Suggested union SQL</summary>
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono">{row.union_sql}</pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="mt-4">
          <UnionableTab api={api} tableId={tableId} />
        </div>
      </ChartCard>
    </section>
  );
};

export const UsageTabPanel = ({ tableId }: { tableId: string }) => (
  <div className="td-tab-content flex flex-col">
    <QueryActivity tableId={tableId} />
    <PartitionAccess tableId={tableId} />
    <Lineage tableId={tableId} />
    <SameShape tableId={tableId} />
  </div>
);
