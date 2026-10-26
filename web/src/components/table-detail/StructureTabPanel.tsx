import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import { AsyncBoundary } from "../AsyncBoundary";
import { ColumnStatsTab } from "./ColumnStatsTab";
import { ChartCard } from "./ChartCard";
import { PartitionAccessChart } from "./PartitionAccessChart";
import { PartitionHistogram } from "./PartitionHistogram";
import { SchemaTab, type SchemaColumn } from "./SchemaTab";
import { SectionNav } from "./SectionNav";
import { StructureTabLabel } from "./tableDetailTabLabels";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { chartTooltipProps, cryoColors } from "../../lib/theme";

const measuredNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const countText = (value: number | null): string => (value == null ? "Not measured" : value.toLocaleString());

const sizeText = (value: number | null): string => (value == null ? "Not measured" : `${value.toFixed(1)} GB`);

export interface StructureHistoryPoint {
  timestamp: number;
  small_files_count: number | null;
  total_size_gb: number | null;
}

export const StructureStatsHistory = ({ points }: { points: StructureHistoryPoint[] }) => (
  <div className="flex flex-col gap-4">
    <section aria-label="Small-file count">
      <h3 className="mb-1 text-sm font-medium">Small-file count</h3>
      <div
        className="chart-frame"
        style={{ height: 180 }}
        role="img"
        aria-label={`Small-file count across ${points.length} scans.`}
      >
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
            <XAxis
              dataKey="timestamp"
              tickFormatter={(ts) => formatShortDate(ts)}
              tick={{ fontSize: 11 }}
              stroke="var(--muted, #5c667a)"
            />
            <YAxis tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" />
            <RechartsTooltip
              {...chartTooltipProps}
              labelFormatter={(ts) => formatDateTime(Number(ts))}
              formatter={(value) => [countText(measuredNumber(value)), "Small files"]}
            />
            <Line
              type="stepAfter"
              dataKey="small_files_count"
              stroke={cryoColors.chartPrimary}
              strokeWidth={2}
              connectNulls={false}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
    <section aria-label="Table size">
      <h3 className="mb-1 text-sm font-medium">Size (GB)</h3>
      <div
        className="chart-frame"
        style={{ height: 180 }}
        role="img"
        aria-label={`Table size in GB across ${points.length} scans.`}
      >
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
            <XAxis
              dataKey="timestamp"
              tickFormatter={(ts) => formatShortDate(ts)}
              tick={{ fontSize: 11 }}
              stroke="var(--muted, #5c667a)"
            />
            <YAxis tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" />
            <RechartsTooltip
              {...chartTooltipProps}
              labelFormatter={(ts) => formatDateTime(Number(ts))}
              formatter={(value) => [sizeText(measuredNumber(value)), "Size"]}
            />
            <Line
              type="stepAfter"
              dataKey="total_size_gb"
              stroke="var(--muted, #5c667a)"
              strokeWidth={2}
              connectNulls={false}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
    <ChartDataTable
      caption="Table statistics by scan time"
      rows={points}
      getRowKey={(row, index) => `${row.timestamp}-${index}`}
      columns={[
        {
          key: "time",
          header: "Scan time",
          render: (row) => formatDateTime(row.timestamp)
        },
        {
          key: "small-files",
          header: "Small files",
          align: "right",
          render: (row) => countText(row.small_files_count)
        },
        {
          key: "size",
          header: "Size",
          align: "right",
          render: (row) => sizeText(row.total_size_gb)
        }
      ]}
    />
  </div>
);
import { ChartDataTable } from "../ui/chart-data-table";
import { formatDateTime, formatShortDate } from "../../lib/format";

export const StructureTabPanel = () => {
  const {
    api,
    tableId,
    mergedColumns,
    table,
    pii,
    dataQuality,
    partitions,
    partitionAccess,
    history,
    historyPoints,
    bloomRecommendations,
    searchParams
  } = useTableDetailContext();

  const highlightColumn = searchParams.get("column");
  const bloomRows = bloomRecommendations.data?.recommendations ?? [];

  return (
    <div className="td-tab-content">
      <SectionNav
        sections={[
          { id: "structure-schema", label: "Schema" },
          { id: "structure-partitions", label: "Partitions" },
          { id: "structure-partition-access", label: "Partition Access" },
          { id: "structure-history", label: "History" },
          { id: "structure-bloom", label: "Bloom Filters" }
        ]}
      />
      <div id="structure-schema">
        <AsyncBoundary
          isLoading={table.isLoading || pii.isLoading || dataQuality.isLoading}
          isError={Boolean(table.error)}
          error={table.error}
          onRetry={() => void table.refetch()}
        >
          <SchemaTab
            columns={mergedColumns as SchemaColumn[]}
            loading={table.isLoading || pii.isLoading || dataQuality.isLoading}
            highlightColumn={highlightColumn}
          />
        </AsyncBoundary>
      </div>
      <div id="structure-partitions" style={{ marginTop: 16 }}>
        <ChartCard
          title="Partition breakdown"
          subtitle="Top partitions by data size and small-file pressure"
        >
          <AsyncBoundary
            isLoading={partitions.isLoading}
            isError={Boolean(partitions.error)}
            error={partitions.error}
            onRetry={() => void partitions.refetch()}
            skeleton={<span className="text-sm text-muted-foreground">Loading partition breakdown…</span>}
          >
            <PartitionHistogram
              partitions={Array.isArray(partitions.data) ? partitions.data : partitions.data?.items ?? []}
            />
          </AsyncBoundary>
        </ChartCard>
        <ColumnStatsTab api={api} tableId={tableId} />
      </div>
      <div id="structure-partition-access" style={{ marginTop: 16 }}>
        <ChartCard
          title="Partition access"
          subtitle="Which partitions are actually being read, and when — from real S3 request logs"
        >
          <AsyncBoundary
            isLoading={partitionAccess.isLoading}
            isError={Boolean(partitionAccess.error)}
            error={partitionAccess.error}
            onRetry={() => void partitionAccess.refetch()}
            isEmpty={!(partitionAccess.data?.points.length)}
            emptyTitle="No partition access data yet"
            emptyDescription="Requires an enabled S3 request-log bucket and at least one completed partition scan for this table -- see Settings → S3 Request Logging."
            skeleton={<span className="text-sm text-muted-foreground">Loading partition access…</span>}
          >
            <PartitionAccessChart
              points={partitionAccess.data?.points ?? []}
              partitions={partitionAccess.data?.partitions ?? []}
            />
          </AsyncBoundary>
        </ChartCard>
      </div>
      <div id="structure-history" style={{ marginTop: 16 }}>
        <ChartCard title="Stats history" subtitle="Scan-over-scan trend">
          <AsyncBoundary
            isLoading={history.isLoading}
            isError={Boolean(history.error)}
            error={history.error}
            onRetry={() => void history.refetch()}
            isEmpty={!historyPoints.length}
            emptyTitle="No stats history yet"
            emptyDescription="History appears after multiple scans."
          >
            <StructureStatsHistory
              points={historyPoints.map((point) => ({
                timestamp: point.timestamp,
                small_files_count: measuredNumber(point.small_files_count),
                total_size_gb: measuredNumber(point.total_size_gb)
              }))}
            />
          </AsyncBoundary>
        </ChartCard>
      </div>
      <div id="structure-bloom" style={{ marginTop: 16 }}>
        <ChartCard
          title="Bloom filter recommendations"
          subtitle="Equality-heavy high-cardinality columns"
        >
          <AsyncBoundary
            isLoading={bloomRecommendations.isLoading}
            isError={Boolean(bloomRecommendations.error)}
            error={bloomRecommendations.error}
            onRetry={() => void bloomRecommendations.refetch()}
            isEmpty={bloomRows.length === 0}
            emptyTitle="No bloom recommendations yet"
            emptyDescription={
              bloomRecommendations.data?.message ??
              "Ingest query telemetry to generate bloom-filter candidates."
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-2 py-1.5 font-medium">Column</th>
                    <th className="px-2 py-1.5 font-medium">Cardinality</th>
                    <th className="px-2 py-1.5 text-right font-medium">Skip rate</th>
                    <th className="px-2 py-1.5 text-right font-medium">Equality filters</th>
                  </tr>
                </thead>
                <tbody>
                  {bloomRows.map((row) => (
                    <tr key={row.column} className="border-b border-border last:border-0">
                      <td className="px-2 py-1.5">{row.column}</td>
                      <td className="px-2 py-1.5">{row.cardinality}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {(row.estimated_skip_rate * 100).toFixed(0)}%
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{row.equality_filter_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AsyncBoundary>
        </ChartCard>
      </div>
    </div>
  );
};

export { StructureTabLabel };
