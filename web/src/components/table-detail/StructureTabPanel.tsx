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
import { PartitionHistogram } from "./PartitionHistogram";
import { SchemaTab, type SchemaColumn } from "./SchemaTab";
import { SectionNav } from "./SectionNav";
import { StructureTabLabel } from "./tableDetailTabLabels";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { chartTooltipProps, cryoColors } from "../../lib/theme";

export const StructureTabPanel = () => {
  const {
    api,
    tableId,
    mergedColumns,
    table,
    pii,
    dataQuality,
    partitions,
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
            <PartitionHistogram partitions={partitions.data ?? []} />
          </AsyncBoundary>
        </ChartCard>
        <ColumnStatsTab api={api} tableId={tableId} />
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
            <div className="chart-frame" style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={historyPoints}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(ts) =>
                      new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })
                    }
                    tick={{ fontSize: 11 }}
                    stroke="var(--muted, #5c667a)"
                  />
                  <YAxis yAxisId="left" tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={{ fontSize: 11 }}
                    stroke="var(--muted, #5c667a)"
                  />
                  <RechartsTooltip
                    {...chartTooltipProps}
                    labelFormatter={(ts) => new Date(Number(ts)).toLocaleString()}
                    formatter={(value, name) => {
                      const n = Number(value ?? 0);
                      if (String(name) === "total_size_gb") return [`${n.toFixed(1)} GB`, "Size"];
                      return [n.toLocaleString(), String(name)];
                    }}
                  />
                  <Line
                    yAxisId="left"
                    type="monotone"
                    dataKey="small_files_count"
                    stroke={cryoColors.chartPrimary}
                    strokeWidth={2}
                    dot={false}
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="total_size_gb"
                    stroke={cryoColors.gradeA}
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
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
