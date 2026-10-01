import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Activity, ArrowRight, GitBranch, Layers3, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
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
import { Badge } from "../../ui/badge";
import { Collapsible } from "../../ui/collapsible";
import { DataTable } from "../../ui/data-table";
import { ArchiveSection } from "../ArchiveSection";
import { ChartCard } from "../ChartCard";
import { LineageGraph } from "../LineageGraph";
import { PartitionAccessChart } from "../PartitionAccessChart";
import "./usage.css";

const QUERY_WINDOW_HOURS = 168;
const QUERY_PAGE_SIZE = 8;
type QueryRow = QueryPerformanceData["queries"][number];

const latencyText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) || value < 0 ? "Not measured" : formatDuration(value);

const bytesText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) || value < 0 ? "Not measured" : formatBytesGb(value);

const queryColumns: ColumnDef<QueryRow, unknown>[] = [
  {
    accessorKey: "query_text",
    header: "Query",
    cell: ({ row }) => (
      <div className="td-usage-query">
        <code title={row.original.query_text ?? undefined}>
          {row.original.query_text?.replace(/\s+/g, " ").trim() || "SQL not recorded"}
        </code>
        <span>{row.original.engine} · {row.original.user || "User not recorded"}</span>
      </div>
    )
  },
  { accessorKey: "started_at", header: "Started", cell: ({ row }) => formatTime(row.original.started_at) },
  { accessorKey: "duration_ms", header: "Duration", cell: ({ row }) => latencyText(row.original.duration_ms) },
  { accessorKey: "bytes_scanned", header: "Bytes scanned", cell: ({ row }) => bytesText(row.original.bytes_scanned) },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <Badge variant={row.original.status.toLowerCase() === "failed" ? "danger" : "secondary"}>{row.original.status || "Not recorded"}</Badge>
  }
];

const LatencyProfile = ({ data }: { data: QueryPerformanceData }) => {
  const values = [
    { label: "Median · p50", detail: "Half of logged queries finish within", value: data.p50_latency_ms },
    { label: "Tail · p95", detail: "95% of logged queries finish within", value: data.p95_latency_ms },
    { label: "Tail · p99", detail: "99% of logged queries finish within", value: data.p99_latency_ms }
  ];
  const max = Math.max(0, ...values.map(({ value }) => value != null && Number.isFinite(value) && value >= 0 ? value : 0));
  return (
    <section className="td-usage-latency" aria-label="Query latency percentiles">
      <h3>How long do queries take?</h3>
      <p>Duration across all logged queries in this window.</p>
      <dl>
        {values.map(({ label, detail, value }) => (
          <div key={label} className="td-usage-latency-row">
            <dt title={detail}>{label}</dt>
            <dd>{latencyText(value)}</dd>
            <div className="td-usage-bar-track" aria-hidden="true">
              <span style={{ width: value != null && Number.isFinite(value) && value >= 0 && max > 0 ? `${value / max * 100}%` : "0%" }} />
            </div>
          </div>
        ))}
      </dl>
    </section>
  );
};

const QueryActivity = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = useQuery({
    queryKey: tableKeys.queries(tableId, token),
    queryFn: () => getQueryPerformanceDashboard(api, QUERY_WINDOW_HOURS, undefined, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const data = query.data;
  const requestedPage = Number(searchParams.get("usageQueryPage") ?? 1);
  const page = Math.min(
    Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage - 1 : 0,
    Math.max(0, Math.ceil((data?.queries.length ?? 0) / QUERY_PAGE_SIZE) - 1)
  );

  return (
    <section id="usage-queries" aria-label="Query activity">
      <ChartCard title={<h2><Activity aria-hidden="true" /> Query activity</h2>} actions={<Badge variant="outline">Last 7 days</Badge>}>
        {query.isLoading ? <p className="td-usage-loading" role="status">Loading query activity…</p> : null}
        {query.isError ? <QueryErrorAlert error={query.error} title="Failed to load query activity" /> : null}
        {data && data.total === 0 ? (
          <div className="td-usage-empty">
            <Search aria-hidden="true" />
            <div>
              <h3>No query records in this window</h3>
              <p>This does not mean the table is unused. Query-log coverage is not measured; check ingestion for this source.</p>
            </div>
          </div>
        ) : null}
        {data && data.total > 0 ? (
          <>
            <div className="td-usage-query-summary">
              <div className="td-usage-query-volume">
                <dl className="td-usage-metrics">
                  <div><dt>Logged queries</dt><dd>{data.total.toLocaleString()}</dd><span>In the last 7 days</span></div>
                  <div><dt>Average bytes scanned</dt><dd>{bytesText(data.avg_bytes_scanned)}</dd><span>Per query with a recorded scan size</span></div>
                </dl>
                <p className="td-usage-coverage">Measured from query logs. Reads outside this telemetry may be missing.</p>
              </div>
              <LatencyProfile data={data} />
            </div>
            <Collapsible label={`Inspect slowest queries (${data.queries.length.toLocaleString()})`} className="td-usage-query-details" defaultOpen={searchParams.has("usageQueryPage")}>
              <p className="td-usage-caption">{data.queries.length.toLocaleString()} of {data.total.toLocaleString()} logged queries, ordered by duration. The list is limited to the slowest 100.</p>
              <DataTable
                columns={queryColumns}
                data={data.queries.slice(page * QUERY_PAGE_SIZE, (page + 1) * QUERY_PAGE_SIZE)}
                density="compact"
                showDensityToggle={false}
                getRowId={(row) => row.query_id}
                emptyTitle="No query details returned"
                pagination={{
                  pageIndex: page,
                  pageSize: QUERY_PAGE_SIZE,
                  totalRows: data.queries.length,
                  onPageChange: (pageIndex) => setSearchParams((previous) => {
                    const next = new URLSearchParams(previous);
                    next.set("usageQueryPage", String(pageIndex + 1));
                    return next;
                  })
                }}
              />
            </Collapsible>
          </>
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

  return (
    <section id="usage-partition-access" aria-label="Partition access">
      <ChartCard title={<h2><Layers3 aria-hidden="true" /> Partition access</h2>} actions={<Badge variant="outline">Last 90 days</Badge>}>
        <p className="td-usage-section-description">Where storage requests are concentrated. These counts are separate from query logs.</p>
        {query.isLoading ? <p className="td-usage-loading" role="status">Loading partition access…</p> : null}
        {query.isError ? <QueryErrorAlert error={query.error} title="Failed to load partition access" /> : null}
        {query.data ? <PartitionAccessChart points={query.data.points} partitions={query.data.partitions} /> : null}
      </ChartCard>
    </section>
  );
};

const Lineage = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const query = useQuery({
    queryKey: ["table-lineage", token, tableId],
    queryFn: () => getTableLineage(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const upstream = query.data?.upstream.length ?? 0;
  const downstream = query.data?.downstream.length ?? 0;

  return (
    <section id="usage-lineage" aria-label="Lineage">
      <ChartCard title={<h2><GitBranch aria-hidden="true" /> Table relationships</h2>}>
        {query.isLoading ? <p className="td-usage-loading" role="status">Loading lineage…</p> : null}
        {query.isError ? <QueryErrorAlert error={query.error} title="Failed to load lineage" /> : null}
        {query.data ? (
          <>
            <p className="td-usage-section-description">Understand dependencies before changing this table.</p>
            <dl className="td-usage-relationship-counts">
              <div><dt>Upstream</dt><dd>{upstream}</dd></div>
              <ArrowRight aria-hidden="true" />
              <div className="td-usage-current-table">This table</div>
              <ArrowRight aria-hidden="true" />
              <div><dt>Downstream</dt><dd>{downstream}</dd></div>
            </dl>
            {upstream + downstream > 0 ? (
              <Collapsible label="Explore lineage" className="td-usage-disclosure">
                <LineageGraph data={query.data} isLoading={false} error={null} />
              </Collapsible>
            ) : <p className="td-usage-caption">No relationships recorded yet. Lineage depends on query-log ingestion.</p>}
          </>
        ) : null}
      </ChartCard>
    </section>
  );
};

const SameShape = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const query = useQuery({
    queryKey: ["unionable", token, tableId],
    queryFn: () => getUnionable(api, tableId, { k: 10, useLlm: false }),
    enabled: Boolean(tableId),
    retry: false
  });
  const results = query.data?.results ?? [];

  return (
    <section id="usage-same-shape" aria-label="Same-shape tables">
      <ChartCard title={<h2><Layers3 aria-hidden="true" /> Similar schemas</h2>} actions={<Link to="/insights?tab=duplicates" className="td-usage-text-link">Find duplicates <ArrowRight aria-hidden="true" /></Link>}>
        <p className="td-usage-section-description">Candidates for reuse or consolidation. Schema similarity does not prove duplicate data.</p>
        {query.isLoading ? <p className="td-usage-loading" role="status">Loading same-shape tables…</p> : null}
        {query.isError ? <QueryErrorAlert error={query.error} title="Failed to load same-shape tables" /> : null}
        {query.data && results.length === 0 ? <p className="td-usage-caption">No similar schemas found in the current sample.</p> : null}
        {results.length > 0 ? (
          <Collapsible label={`Explore ${results.length} similar ${results.length === 1 ? "schema" : "schemas"}`} className="td-usage-disclosure">
            <ul className="td-usage-candidates">
              {results.map((row) => (
                <li key={row.candidate_table_id}>
                  <div className="td-usage-candidate-heading">
                    <Link to={`/tables/${row.candidate_table_id}`}>{row.candidate_schema_name ? `${row.candidate_schema_name}.` : ""}{row.candidate_table_name || row.candidate_table_id}</Link>
                    <Badge variant="secondary">{Math.round(row.score * 100)}% match</Badge>
                  </div>
                  <p className="td-usage-caption">Shared columns: {row.overlap_columns.slice(0, 5).join(", ") || "Not measured"}{row.overlap_columns.length > 5 ? ` +${row.overlap_columns.length - 5} more` : ""}</p>
                  <p>{row.rationale}</p>
                  {row.union_sql ? <Collapsible label="Suggested union SQL"><pre className="td-usage-sql">{row.union_sql}</pre></Collapsible> : null}
                </li>
              ))}
            </ul>
          </Collapsible>
        ) : null}
      </ChartCard>
    </section>
  );
};

export const UsageTabPanel = ({ tableId }: { tableId: string }) => (
  <div className="td-tab-content td-usage">
    <QueryActivity tableId={tableId} />
    <PartitionAccess tableId={tableId} />
    <ArchiveSection tableId={tableId} />
    <div className="td-usage-secondary">
      <Lineage tableId={tableId} />
      <SameShape tableId={tableId} />
    </div>
  </div>
);
