import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
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
import { SimpleTablePagination } from "../SimpleTablePagination";
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
const VIRTUALIZE_AFTER = 200;

const SECTIONS = [
  { id: "files-schema", label: "Schema" },
  { id: "files-layout", label: "Layout" },
  { id: "files-properties", label: "Properties" },
  { id: "files-files", label: "Files" },
  { id: "files-partitions", label: "Partitions" }
];

const textOrMissing = (value: string | null): string => value ?? NOT_MEASURED;

const SchemaBadges = ({ row }: { row: SchemaColumnRow }) => (
  <span className="inline-flex flex-wrap gap-1">
    {row.isPartition ? <Badge>Partition</Badge> : null}
    {row.isSort ? <Badge>Sort</Badge> : null}
    {row.piiCategory ? <Badge variant="warning">PII: {row.piiCategory}</Badge> : null}
    {row.drift ? <Badge variant="warning">Drift</Badge> : null}
    {row.bloom ? <Badge variant="secondary">Bloom</Badge> : null}
  </span>
);

const NullRateCell = ({ rate }: { rate: number | null }) =>
  rate == null ? <MissingPattern /> : <span>{formatNullRate(rate)}</span>;

const SchemaRow = ({ row, highlighted }: { row: SchemaColumnRow; highlighted: boolean }) => (
  <>
    <td className={highlighted ? "td-schema-row-highlight" : undefined}>
      <span data-testid="schema-column-name" className="font-mono text-xs">
        {row.name}
      </span>
    </td>
    <td>{row.type}</td>
    <td>
      <SchemaBadges row={row} />
    </td>
    <td>
      <NullRateCell rate={row.nullRate} />
    </td>
    <td>{textOrMissing(row.lowerBound)}</td>
    <td>{textOrMissing(row.upperBound)}</td>
    <td>{formatCount(row.ndv)}</td>
    <td>{formatCount(row.p50)}</td>
    <td>{formatCount(row.p95)}</td>
  </>
);

const SchemaHeader = () => (
  <tr>
    <th>Name</th>
    <th>Type</th>
    <th>Badges</th>
    <th>Null rate</th>
    <th>Lower bound</th>
    <th>Upper bound</th>
    <th>NDV</th>
    <th>p50</th>
    <th>p95</th>
  </tr>
);

const VirtualSchemaRows = ({
  rows,
  highlight
}: {
  rows: SchemaColumnRow[];
  highlight: string | null;
}) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 44,
    overscan: 8
  });

  useEffect(() => {
    if (!highlight) return;
    const index = rows.findIndex((row) => row.name.toLowerCase() === highlight);
    if (index >= 0) virtualizer.scrollToIndex(index, { align: "center" });
  }, [highlight, rows, virtualizer]);

  return (
    <div ref={parentRef} className="max-h-[480px] overflow-auto">
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((item) => {
          const row = rows[item.index];
          if (!row) return null;
          const highlighted = highlight != null && row.name.toLowerCase() === highlight;
          return (
            <div
              key={row.name}
              id={highlighted ? "files-schema-highlight" : undefined}
              data-index={item.index}
              ref={virtualizer.measureElement}
              className="absolute left-0 top-0 w-full"
              style={{ transform: `translateY(${item.start}px)` }}
            >
              <table className="td-schema-table w-full">
                <tbody>
                  <tr className={highlighted ? "td-schema-row-highlight" : undefined}>
                    <SchemaRow row={row} highlighted={highlighted} />
                  </tr>
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const SchemaTable = ({ rows, highlight }: { rows: SchemaColumnRow[]; highlight: string | null }) => {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matched = query ? rows.filter((row) => row.name.toLowerCase().includes(query)) : rows;
    return [...matched].sort(compareSchemaColumns);
  }, [rows, search]);
  const virtualize = filtered.length > VIRTUALIZE_AFTER;
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [search, rows]);

  useEffect(() => {
    if (!highlight || virtualize) return;
    const index = filtered.findIndex((row) => row.name.toLowerCase() === highlight);
    if (index >= 0) setPage(Math.floor(index / PAGE_SIZE));
  }, [filtered, highlight, virtualize]);

  useEffect(() => {
    if (!highlight || virtualize) return;
    const timer = window.setTimeout(() => {
      document.getElementById("files-schema-highlight")?.scrollIntoView({ block: "center" });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [highlight, page, virtualize]);

  return (
    <div className="flex flex-col gap-3">
      <Input
        aria-label="Search columns"
        placeholder="Search columns"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {virtualize ? (
        <>
          <p className="text-xs text-muted-foreground">Virtualized past 200 rows.</p>
          <VirtualSchemaRows rows={filtered} highlight={highlight} />
        </>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">No column metadata yet.</p>
      ) : (
        <>
          {filtered.length > PAGE_SIZE ? (
            <p className="text-xs text-muted-foreground">This table is paginated.</p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="td-schema-table w-full">
              <thead>
                <SchemaHeader />
              </thead>
              <tbody>
                {paged.map((row) => {
                  const highlighted = highlight != null && row.name.toLowerCase() === highlight;
                  return (
                    <tr
                      key={row.name}
                      id={highlighted ? "files-schema-highlight" : undefined}
                      className={highlighted ? "td-schema-row-highlight" : undefined}
                    >
                      <SchemaRow row={row} highlighted={highlighted} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <SimpleTablePagination
            page={page}
            pageSize={PAGE_SIZE}
            total={filtered.length}
            onPageChange={setPage}
          />
        </>
      )}
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
  const ranked = useMemo(() => rankPartitions(partitions.data ?? []), [partitions.data]);
  const top = ranked.ranked.slice(0, 15);
  const series = useMemo(() => smallFileSeries(history.data), [history.data]);
  const propertyItems = (properties.data?.items ?? []) as Array<
    PropertyRecommendationRead & { source?: string | null }
  >;

  return (
    <div className="td-tab-content">
      <SectionNav sections={SECTIONS} />

      <section id="files-schema" className="mt-4 flex flex-col gap-3" aria-labelledby="files-schema-title">
        <h2 id="files-schema-title" className="text-sm font-semibold">
          Schema
        </h2>
        {table.isLoading ? <p className="text-sm text-muted-foreground">Loading</p> : null}
        {table.isError ? <p role="alert">{queryError(table.error)}</p> : null}
        {table.isSuccess ? <SchemaTable rows={columns} highlight={highlight} /> : null}
      </section>

      <section id="files-layout" className="mt-6 flex flex-col gap-3" aria-labelledby="files-layout-title">
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
        <h3 className="text-sm font-semibold">Layout analysis (WAIR: workload-aware reclustering)</h3>
        <WairRecluster />
      </section>

      <section id="files-properties" className="mt-6 flex flex-col gap-3" aria-labelledby="files-properties-title">
        <h2 id="files-properties-title" className="text-sm font-semibold">
          Properties
        </h2>
        {properties.isError ? <p role="alert">{queryError(properties.error)}</p> : null}
        {propertyItems.length ? (
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
          </div>
        ) : null}
        <PropertyRecommendations />
      </section>

      <section id="files-files" className="mt-6 flex flex-col gap-3" aria-labelledby="files-files-title">
        <h2 id="files-files-title" className="text-sm font-semibold">
          Files
        </h2>
        <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          {measures.map((measure) => (
            <div key={measure.label}>
              <dt className="text-xs text-muted-foreground">{measure.label}</dt>
              <dd>{measure.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="files-partitions" className="mt-6 flex flex-col gap-3" aria-labelledby="files-partitions-title">
        <h2 id="files-partitions-title" className="text-sm font-semibold">
          Partitions
        </h2>
        {partitions.isError ? <p role="alert">{queryError(partitions.error)}</p> : null}
        {!table.isSuccess ? null : unpartitioned ? (
          <p>Unpartitioned table</p>
        ) : partitions.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading</p>
        ) : top.length === 0 ? (
          <p className="text-sm text-muted-foreground">{NOT_MEASURED}</p>
        ) : (
          <>
            <RankedBars
              title={`Top ${top.length} of ${ranked.ranked.length} partitions`}
              unit={ranked.metric}
              total={ranked.ranked.length}
              items={top.map((item) => ({ id: item.id, label: item.label, value: item.value }))}
            />
            <table className="w-full text-sm">
              <caption className="sr-only">Top {top.length} of {ranked.ranked.length} partitions</caption>
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
            </table>
          </>
        )}
      </section>

      <section className="mt-6" aria-label="Small-file count" data-series="small-file count">
        {history.isError ? <p role="alert">{queryError(history.error)}</p> : null}
        {series.length === 0 ? (
          <p className="text-sm text-muted-foreground">{history.isLoading ? "Loading" : NOT_MEASURED}</p>
        ) : (
          <TimeSeries
            title="Small files over scans"
            unit="small files"
            points={series.map((point) => ({ at: point.scanAt, value: point.smallFiles }))}
          />
        )}
      </section>
    </div>
  );
};
