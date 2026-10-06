import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  getBloomFilterRecommendations,
  getColumnStats,
  getDataQualityProfile,
  getPropertyRecommendations,
  getTable,
  getTableHistory,
  getTablePartitions,
  getTablePiiClassification,
  type PropertyRecommendationRead
} from "../../../api/client";
import type { ColumnSketchRead } from "../../../api/types";
import { MissingPattern } from "../../charts/MissingPattern";
import { RankedBars } from "../../charts/RankedBars";
import { TimeSeries } from "../../charts/TimeSeries";
import { useAuth } from "../../../lib/auth";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { Badge } from "../../ui/badge";
import { Input } from "../../ui/input";
import { PropertyRecommendations } from "../PropertyRecommendations";
import { SectionNav } from "../SectionNav";
import { DataTable } from "../../ui/data-table";
import "./files.css";
import { WairRecluster } from "../WairRecluster";
import {
  compareSchemaColumns,
  fileMeasures,
  formatCount,
  formatNullRate,
  isUnpartitionedTable,
  layoutChecks,
  mergeSchemaColumns,
  NOT_MEASURED,
  partitionSpecInWords,
  propertySource,
  propertyValue,
  rankPartitions,
  smallFileSeries,
  sortOrderLabel,
  type ScanStats,
  type SchemaColumnRow
} from "./schemaColumns";

const PAGE_SIZE = 50;

const SECTIONS = [
  { id: "files-schema", label: "Schema" },
  { id: "files-files", label: "Files" },
  { id: "files-layout", label: "Layout" },
  { id: "files-partitions", label: "Partitions" },
  { id: "files-properties", label: "Properties" }
];

const textOrMissing = (value: string | null): string => value ?? NOT_MEASURED;

const SchemaBadges = ({ row }: { row: SchemaColumnRow }) => (
  <span className="inline-flex flex-wrap gap-1">
    {row.isPartition ? <Badge>Partition</Badge> : null}
    {row.isSort ? <Badge>Sort</Badge> : null}
    {row.piiCategory ? (
      <Badge
        variant="warning"
        title="Untagged name guess. Optimize is deferred until this column is tagged."
      >
        Likely {row.piiCategory}
      </Badge>
    ) : null}
    {row.drift ? <Badge variant="warning">Drift</Badge> : null}
    {row.bloom ? (
      <Badge variant="secondary" title="Recommended from query patterns. Not enabled on existing files.">
        Bloom candidate
      </Badge>
    ) : null}
  </span>
);

const NullRateCell = ({ rate }: { rate: number | null }) =>
  rate == null ? <MissingPattern /> : <span>{formatNullRate(rate)}</span>;

const hasSketches = (rows: SchemaColumnRow[]): boolean =>
  rows.some((row) => row.ndv != null || row.p50 != null || row.p95 != null);

const SchemaTable = ({
  rows,
  highlight,
  columnStatsError
}: {
  rows: SchemaColumnRow[];
  highlight: string | null;
  columnStatsError?: string | null;
}) => {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matched = query ? rows.filter((row) => row.name.toLowerCase().includes(query)) : rows;
    return [...matched].sort(compareSchemaColumns);
  }, [rows, search]);
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [search, rows]);

  useEffect(() => {
    if (!highlight) return;
    const index = filtered.findIndex((row) => row.name.toLowerCase() === highlight);
    if (index >= 0) setPage(Math.floor(index / PAGE_SIZE));
  }, [filtered, highlight]);

  useEffect(() => {
    if (!highlight) return;
    const timer = window.setTimeout(() => {
      document.getElementById("files-schema-highlight")?.scrollIntoView({ block: "center" });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [highlight, page, rows]);

  const columns = useMemo<ColumnDef<SchemaColumnRow, unknown>[]>(() => [
    { id: "name", header: "Name", cell: ({ row }) => <span id={row.original.name.toLowerCase() === highlight ? "files-schema-highlight" : undefined} data-testid="schema-column-name" className="font-mono text-xs">{row.original.name}</span> },
    { id: "type", header: "Type", cell: ({ row }) => row.original.type },
    { id: "badges", header: "Signals", cell: ({ row }) => <SchemaBadges row={row.original} /> },
    { id: "null", header: "Null rate", cell: ({ row }) => <NullRateCell rate={row.original.nullRate} /> },
    { id: "lower", header: "Lower bound", cell: ({ row }) => textOrMissing(row.original.lowerBound) },
    { id: "upper", header: "Upper bound", cell: ({ row }) => textOrMissing(row.original.upperBound) },
    ...(hasSketches(rows) ? [
      { id: "ndv", header: "Distinct values", cell: ({ row }: { row: { original: SchemaColumnRow } }) => formatCount(row.original.ndv) },
      { id: "p50", header: "Median (p50)", cell: ({ row }: { row: { original: SchemaColumnRow } }) => formatCount(row.original.p50) },
      { id: "p95", header: "p95", cell: ({ row }: { row: { original: SchemaColumnRow } }) => formatCount(row.original.p95) }
    ] : [])
  ], [highlight, rows]);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="td-files-schema-toolbar">
        <Input aria-label="Search columns" placeholder="Search columns…" value={search} onChange={(event) => setSearch(event.target.value)} />
        <span className="text-xs text-muted-foreground">{filtered.length.toLocaleString()} of {rows.length.toLocaleString()} columns</span>
      </div>
      <DataTable columns={columns} data={paged} density="compact" stickyFirstColumn
        getRowId={(row) => row.name}
        getRowClassName={(row) => row.name.toLowerCase() === highlight ? "td-schema-row-highlight" : undefined}
        emptyTitle={search ? "No matching columns" : "No column metadata yet"}
        emptyDescription={search ? "Try a different column name." : "Column metadata appears after a scan records schema statistics."}
        pagination={{ pageIndex: page, pageSize: PAGE_SIZE, totalRows: filtered.length, onPageChange: setPage }}
      />
      {columnStatsError ? (
        <p role="alert" className="text-xs text-destructive">{columnStatsError}</p>
      ) : !hasSketches(rows) && rows.length > 0 ? (
        <p className="text-xs text-muted-foreground">Distinct counts and percentiles appear after a statistics pass computes column sketches.</p>
      ) : null}
    </div>
  );
};

const queryError = (error: unknown): string => (error instanceof Error ? error.message : "Request failed");

export const FilesTabPanel = () => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const [searchParams] = useSearchParams();
  const highlight = searchParams.get("column")?.trim().toLowerCase() || null;
  const enabled = tableId.length > 0;

  const table = useQuery({
    queryKey: tableKeys.detail(tableId, token),
    queryFn: () => getTable(api, tableId),
    enabled,
    retry: false
  });
  const partitions = useQuery({
    queryKey: tableKeys.partitions(tableId, token),
    queryFn: () => getTablePartitions(api, tableId),
    enabled,
    retry: false
  });
  const history = useQuery({
    queryKey: tableKeys.history(tableId, token),
    queryFn: () => getTableHistory(api, tableId),
    enabled,
    retry: false
  });
  const pii = useQuery({
    queryKey: tableKeys.pii(tableId, token),
    queryFn: () => getTablePiiClassification(api, tableId),
    enabled,
    retry: false
  });
  const dataQuality = useQuery({
    queryKey: tableKeys.dataQuality(tableId, token),
    queryFn: () => getDataQualityProfile(api, tableId),
    enabled,
    retry: false
  });
  const columnStats = useQuery({
    queryKey: tableKeys.columnStats(tableId, token),
    queryFn: () => getColumnStats(api, tableId),
    enabled,
    retry: false
  });
  const bloom = useQuery({
    queryKey: tableKeys.bloom(tableId, token),
    queryFn: () => getBloomFilterRecommendations(api, tableId),
    enabled,
    retry: false
  });
  const properties = useQuery({
    queryKey: tableKeys.properties(tableId, token),
    queryFn: () => getPropertyRecommendations(api, tableId),
    enabled,
    retry: false
  });

  const stats = (table.data?.stats ?? null) as ScanStats | null;
  const sketches = (columnStats.data?.columns ?? null) as Record<string, ColumnSketchRead> | null;
  const columns = useMemo(
    () =>
      mergeSchemaColumns({
        stats,
        pii: pii.data?.matches ?? [],
        quality: dataQuality.data,
        sketches,
        bloomColumns: (bloom.data?.recommendations ?? []).map((item) => item.column)
      }),
    [bloom.data?.recommendations, dataQuality.data, pii.data?.matches, sketches, stats]
  );
  const checks = layoutChecks(stats);
  const measures = fileMeasures(stats);
  const unpartitioned = isUnpartitionedTable(stats);
  const partitionPage = Array.isArray(partitions.data)
    ? { items: partitions.data, total: partitions.data.length }
    : {
        items: partitions.data?.items ?? [],
        total: partitions.data?.total ?? partitions.data?.items.length ?? 0
      };
  const ranked = useMemo(() => rankPartitions(partitionPage.items), [partitionPage.items]);
  const top = ranked.ranked.slice(0, 15);
  const series = useMemo(() => smallFileSeries(history.data), [history.data]);
  const propertyItems = (properties.data?.items ?? []) as Array<
    PropertyRecommendationRead & { source?: string | null }
  >;

  return (
    <div className="td-tab-content td-files-workspace">
      <header className="td-files-intro"><h2>Schema & file layout</h2><p>Understand the table’s structure, inspect file pressure, then review improvements.</p></header>
      <SectionNav sections={SECTIONS} />

      <section id="files-schema" className="td-files-section" aria-labelledby="files-schema-title">
        <h2 id="files-schema-title" className="text-sm font-semibold">
          Columns
        </h2>
        <p className="td-files-caption">Column types, bounds and signals from recorded metadata. Missing measurements are shown explicitly.</p>
        {table.isLoading ? <p className="text-sm text-muted-foreground">Loading</p> : null}
        {table.isError ? <p role="alert">{queryError(table.error)}</p> : null}
        {columnStats.isError || pii.isError || dataQuality.isError || bloom.isError ? (
          <p role="alert">{queryError(columnStats.error ?? pii.error ?? dataQuality.error ?? bloom.error)}</p>
        ) : null}
        {table.isSuccess ? (
          <SchemaTable
            rows={columns}
            highlight={highlight}
            columnStatsError={columnStats.isError ? queryError(columnStats.error) : null}
          />
        ) : null}
      </section>

      <section id="files-files" className="td-files-section" aria-labelledby="files-files-title">
        <h2 id="files-files-title" className="text-sm font-semibold">
          Files
        </h2>
        <p className="td-files-caption">Data-file counts and metadata overhead from the latest scan. Small-file candidates are counted only in partitions selected for compaction.</p>
        <dl className="td-files-measures">
          {measures.map((measure) => (
            <div key={measure.label}>
              <dt className="text-xs text-muted-foreground">{measure.label}</dt>
              <dd>{measure.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="td-files-section" aria-label="Small-file candidates over scans" data-series="small-file count">
        {history.isError ? <p role="alert">{queryError(history.error)}</p> : null}
        {history.isError ? null : series.length === 0 ? (
          <p className="text-sm text-muted-foreground">{history.isLoading ? "Loading" : NOT_MEASURED}</p>
        ) : (
          <TimeSeries
            title="Small-file candidates over scans"
            unit="candidates"
            points={series.map((point) => ({ at: point.scanAt, value: point.smallFiles }))}
          />
        )}
      </section>
      <section id="files-layout" className="td-files-section" aria-labelledby="files-layout-title">
        <h2 id="files-layout-title" className="text-sm font-semibold">
          Layout
        </h2>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-muted-foreground">Partition spec</dt>
            <dd>{partitionSpecInWords(stats)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Sort order</dt>
            <dd>{sortOrderLabel(stats)}</dd>
          </div>
          {checks.map((check) => (
            <div key={check.name}>
              <dt className="text-xs text-muted-foreground">{check.name}</dt>
              <dd>
                Observed: {check.observed}. {check.status}
              </dd>
            </div>
          ))}
        </dl>
        <details className="td-files-disclosure"><summary>Workload-aware layout recommendations</summary>
          <h3 className="sr-only">Layout analysis (WAIR: workload-aware reclustering)</h3><WairRecluster />
        </details>
      </section>

      <section id="files-partitions" className="td-files-section" aria-labelledby="files-partitions-title">
        <h2 id="files-partitions-title" className="text-sm font-semibold">
          Partitions
        </h2>
        {partitions.isError ? <p role="alert">{queryError(partitions.error)}</p> : null}
        {!table.isSuccess ? null : unpartitioned ? (
          <p>Unpartitioned table</p>
        ) : partitions.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading</p>
        ) : partitions.isError ? null : top.length === 0 ? (
          <p className="text-sm text-muted-foreground">{NOT_MEASURED}</p>
        ) : (
          <>
            <RankedBars
              title={`Top ${top.length} of ${partitionPage.total} partitions`}
              unit={ranked.metric}
              total={ranked.ranked.length}
              items={top.map((item) => ({ id: item.id, label: item.label, value: item.value }))}
            />
            <details className="td-files-disclosure"><summary>Exact partition counts</summary><div className="overflow-x-auto"><table className="w-full text-sm">
              <caption className="sr-only">Top {top.length} of {partitionPage.total} partitions</caption>
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="px-2 py-1 font-medium">Partition</th>
                  <th className="px-2 py-1 font-medium">{ranked.metric}</th>
                  <th className="px-2 py-1 font-medium">Small-file rate</th>
                </tr>
              </thead>
              <tbody>
                {top.map((item) => (
                  <tr key={item.id}>
                    <td className="px-2 py-1">{item.label}</td>
                    <td className="px-2 py-1">{item.value == null ? NOT_MEASURED : item.value.toLocaleString()}</td>
                    <td className="px-2 py-1">{item.smallFileRate}</td>
                  </tr>
                ))}
              </tbody>
            </table></div></details>
          </>
        )}
      </section>

      <section id="files-properties" className="td-files-section" aria-labelledby="files-properties-title">
        <h2 id="files-properties-title" className="text-sm font-semibold">
          Properties
        </h2>
        {properties.isError ? <p role="alert">{queryError(properties.error)}</p> : null}
        {propertyItems.length ? (<details className="td-files-disclosure"><summary>Property values and provenance</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="px-2 py-1 font-medium">Property</th>
                  <th className="px-2 py-1 font-medium">Current</th>
                  <th className="px-2 py-1 font-medium">Recommended</th>
                  <th className="px-2 py-1 font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {propertyItems.map((item) => (
                  <tr key={item.property_key}>
                    <td className="px-2 py-1 font-mono text-xs">{item.property_key}</td>
                    <td className="px-2 py-1 font-mono text-xs">{propertyValue(item.current_value)}</td>
                    <td className="px-2 py-1 font-mono text-xs">{propertyValue(item.recommended_value)}</td>
                    <td className="px-2 py-1 text-xs">{propertySource(item)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div></details>
        ) : null}
        <PropertyRecommendations />
      </section>

    </div>
  );
};
