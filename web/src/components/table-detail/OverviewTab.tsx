/**
 * "Health & Diagnosis" overview tab for the table detail page.
 *
 * Extracted from TableDetailRedesign.tsx so the page can focus on data fetching
 * and tab assembly. This tab is the densest on the page: the gauge + verdict, the
 * Act-now remediation card, the collapse-to-reveal Diagnostics section (health
 * trends, partition analyzer, query load, score factors) — all rendered against
 * stats pulled from the parent.
 */
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  GitBranch,
  XCircle
} from "lucide-react";
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import type { TableStatsRead, TableCostRead } from "../../api/types";
import type {
  QueryPerformanceData,
  TableSignalsResponse,
  BenefitScoreResponse,
  PartitionEvolutionRecommendationRead,
  TableHealthTrends
} from "../../api/client";

const formatScanRatioPct = (ratio: number | null | undefined): string | null => {
  if (ratio == null || Number.isNaN(ratio)) return null;
  const pct = ratio <= 1 ? ratio * 100 : ratio;
  return `${Math.round(pct)}%`;
};

const appendPartitionEvolutionWhy = (
  why: string,
  rec: PartitionEvolutionRecommendationRead | undefined
): string => {
  if (!rec) return why;
  const extras: string[] = [];
  if (rec.trigger === "scan_ratio") {
    const pct = formatScanRatioPct(rec.scan_ratio);
    extras.push(
      pct
        ? `Triggered by scan ratio: queries read ${pct} of table size on average.`
        : "Triggered by scan ratio: queries read a large share of this table."
    );
  } else if (rec.trigger === "bucket_sizing") {
    const pct = formatScanRatioPct(rec.scan_ratio);
    extras.push(
      pct
        ? `Triggered by bucket sizing (scan ratio ${pct}). Existing bucket count no longer matches table volume.`
        : "Triggered by bucket sizing: existing bucket count no longer matches table volume."
    );
  }
  if (rec.reason.trim()) extras.push(rec.reason.trim());
  if (!extras.length) return why;
  return `${why} ${extras.join(" ")}`;
};
import { EmptyState } from "../common";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { ChartDataTable } from "../ui/chart-data-table";
import { HealthScoreBreakdown } from "../HealthScoreBreakdown";
import { ChartCard } from "./ChartCard";
import type { ScoreFactor as HealthScoreFactor } from "../HealthScoreBreakdown";
import { ActNowSection } from "./ActNowSection";
import { HealthGauge } from "./HealthGauge";
import { buildDiagnosisVerdict, type DiagnosisVerdict } from "../TableDiagnosisPanel";
import { chartTooltipProps } from "../../lib/theme";
import type { PointDeduction } from "../HealthScoreBreakdown";
import { formatBytesGb, formatDuration, formatNumber, formatTime } from "../../lib/format";
import { formatPartitionColumnLabel } from "./helpers";

export function measuredNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const GRADE_POINT: Record<string, string> = {
  A: "var(--gradeA)",
  B: "var(--gradeB)",
  C: "var(--gradeC)",
  D: "var(--gradeD)",
  F: "var(--gradeF)"
};

export function gradePointColor(grade: string | null | undefined): string {
  if (!grade) return "var(--muted, #5c667a)";
  return GRADE_POINT[grade] ?? "var(--muted, #5c667a)";
}

export interface ScoreTrendPoint {
  timestamp: number;
  score: number | null;
  grade?: string | null;
}

const GradeDot = (props: { cx?: number; cy?: number; payload?: ScoreTrendPoint }) => {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.score == null) return <g />;
  return <circle cx={cx} cy={cy} r={3} fill={gradePointColor(payload.grade)} />;
};

export const HealthScoreTrend = ({ points }: { points: ScoreTrendPoint[] }) => {
  const latest = points[points.length - 1]?.score;
  return (
    <>
      <p className="mb-2 text-xs text-muted-foreground">Gaps are not measured.</p>
      <div
        className="chart-frame"
        style={{ height: 260 }}
        role="img"
        aria-label={`Health score over ${points.length} scans. Latest score ${latest == null ? "Not measured" : latest.toFixed(0)}.`}
      >
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
            <XAxis
              dataKey="timestamp"
              tickFormatter={(ts) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              tick={{ fontSize: 11 }}
              interval="preserveStartEnd"
              stroke="var(--muted, #5c667a)"
            />
            <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" tickLine={false} />
            <RechartsTooltip
              {...chartTooltipProps}
              labelFormatter={(ts) => new Date(Number(ts)).toLocaleString()}
              formatter={(value) => {
                const score = measuredNumber(value);
                return [score == null ? "Not measured" : score.toFixed(1), "Health score"];
              }}
            />
            <Line
              type="stepAfter"
              dataKey="score"
              stroke="var(--muted, #5c667a)"
              strokeWidth={2}
              connectNulls={false}
              dot={<GradeDot />}
              name="score"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="Persisted scan scores. Gaps are not measured."
        rows={points}
        getRowKey={(row, index) => `${row.timestamp}-${index}`}
        columns={[
          {
            key: "time",
            header: "Time",
            render: (row) => new Date(row.timestamp).toLocaleString()
          },
          {
            key: "score",
            header: "Health score",
            align: "right",
            render: (row) => (row.score == null ? "Not measured" : row.score.toFixed(1))
          },
          {
            key: "grade",
            header: "Grade",
            align: "right",
            render: (row) => row.grade || "Not measured"
          }
        ]}
      />
    </>
  );
};

interface OverviewTabProps {
  score: number;
  scoreFactors: HealthScoreFactor[];
  rowExists: boolean;
  verdict: DiagnosisVerdict;
  healthTrends?: TableHealthTrends;
  trendDirection: "up" | "down" | "flat";
  trendLabel: string | null;
  tableSignals?: TableSignalsResponse;
  tableSignalsLoading: boolean;
  tableSignalsError: unknown;
  costView?: TableCostRead;
  benefitScores?: BenefitScoreResponse;
  stats: TableStatsRead | null | undefined;
  statsRaw: Record<string, unknown>;
  reclaimableBytes: number | null;
  savingsUsd: number | null;
  effectivePresetId?: string;
  onRun: () => void;
  runPending: boolean;
  queuedMaintenanceRunId?: string | null;
  lastRunSkipNotes?: string[];
  onOpenRun: () => void;
  onPreview: () => void;
  onSchedule: (scheduledAtIso: string) => void;
  schedulePending: boolean;
  tableLabel: string;
  partitionEvolution?: PartitionEvolutionRecommendationRead;
  tableQueries?: QueryPerformanceData;
  tableQueriesLoading: boolean;
  pointDeductions: PointDeduction[];
}

export const OverviewTab = (props: OverviewTabProps) => {
  const {
    score,
    scoreFactors,
    rowExists,
    verdict,
    healthTrends,
    trendDirection,
    trendLabel,
    tableSignals,
    tableSignalsLoading,
    tableSignalsError,
    costView,
    benefitScores,
    stats,
    statsRaw,
    reclaimableBytes,
    savingsUsd,
    effectivePresetId,
    onRun,
    runPending,
    queuedMaintenanceRunId,
    lastRunSkipNotes,
    onOpenRun,
    onPreview,
    onSchedule,
    schedulePending,
    tableLabel,
    partitionEvolution,
    tableQueries,
    tableQueriesLoading,
    pointDeductions
  } = props;

  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

  const alertVariant: "error" | "warning" | "success" =
    verdict.level === "critical" ? "error" : verdict.level === "warning" ? "warning" : "success";

  const trendData: ScoreTrendPoint[] = (healthTrends?.trend_points ?? []).map((p) => ({
    timestamp: new Date(p.timestamp).getTime(),
    score: measuredNumber(p.score),
    grade: p.grade
  }));
  const hasPersistedDeductions = Array.isArray(statsRaw.health_deductions);

  const degradationRate = healthTrends?.degradation_rate;
  const degradationBadge =
    degradationRate != null && degradationRate < -0.5
      ? `${Math.abs(degradationRate).toFixed(1)} pts/day`
      : null;

  const checkPartitionColumn = (): {
    status: "optimal" | "suboptimal";
    tagText: string;
    tagColor: "success" | "warning" | "error";
    what: string;
    why: string;
    current: string;
    ideal: string;
  } => {
    const currentCol = stats?.partition_col;
    const hasRec = !!partitionEvolution;
    const currentSpec =
      partitionEvolution?.current_partition_spec ||
      (currentCol ? `identity(${currentCol})` : "unpartitioned");
    const recommendedSpec = partitionEvolution?.recommended_partition_spec;

    if (!currentCol || currentCol === "unpartitioned" || currentSpec.toLowerCase() === "unpartitioned") {
      if (!hasRec || !recommendedSpec || recommendedSpec.toLowerCase() === "unpartitioned") {
        return {
          status: "optimal",
          tagText: "Optimal (Unpartitioned)",
          tagColor: "success",
          what: "Table is optimally unpartitioned",
          why: "Table scale or range filters do not warrant partitioning overhead. Keeping the table unpartitioned avoids planning overhead and file fragmentation.",
          current: "Unpartitioned",
          ideal: "Unpartitioned"
        };
      }
      return {
        status: "suboptimal",
        tagText: "Partition Recommended",
        tagColor: "warning",
        what: "Table is unpartitioned but needs partition keys",
        why: appendPartitionEvolutionWhy(
          `High-volume queries frequently filter on columns aligned with "${recommendedSpec}". Partitioning on these columns would reduce plan-time overhead and accelerate searches.`,
          partitionEvolution
        ),
        current: "Unpartitioned",
        ideal: recommendedSpec
      };
    }

    if (!hasRec || !recommendedSpec) {
      return {
        status: "optimal",
        tagText: "Aligned Column",
        tagColor: "success",
        what: `Partition column "${currentCol}" is optimal`,
        why: "Queries targeting this table align with the existing partition key, achieving successful pruning boundaries. No column changes are suggested.",
        current: currentSpec,
        ideal: currentSpec
      };
    }

    const normCurrent = currentSpec.toLowerCase().replace(/\s+/g, "");
    const normRec = recommendedSpec.toLowerCase().replace(/\s+/g, "");
    if (normCurrent === normRec) {
      return {
        status: "optimal",
        tagText: "Optimal (Aligned)",
        tagColor: "success",
        what: `Partition column "${currentCol}" is optimal`,
        why: appendPartitionEvolutionWhy(
          `Historical access patterns successfully utilize "${currentCol}" as the pruning index. No partition column evolution required.`,
          partitionEvolution
        ),
        current: currentSpec,
        ideal: recommendedSpec
      };
    }

    const getColsFromSpec = (spec: string): string[] =>
      spec.split(",").map((p) => p.trim()).map((part) => {
        const match = part.match(/\((.*?)\)/);
        return match ? match[1] : part;
      });
    const currentCols = getColsFromSpec(currentSpec).map((c) => c.toLowerCase());
    const recCols = getColsFromSpec(recommendedSpec).map((c) => c.toLowerCase());
    const missingCols = recCols.filter((c) => !currentCols.includes(c));
    if (missingCols.length > 0) {
      return {
        status: "suboptimal",
        tagText: "Evolution Suggested",
        tagColor: "warning",
        what: "Additional partition keys recommended",
        why: appendPartitionEvolutionWhy(
          `Queries frequently filter on "${missingCols.join(", ")}" alongside your current partition key. Evolving the partition spec to "${recommendedSpec}" will reduce raw bytes scanned.`,
          partitionEvolution
        ),
        current: currentSpec,
        ideal: recommendedSpec
      };
    }
    return {
      status: "suboptimal",
      tagText: "Column Mismatch",
      tagColor: "error",
      what: "Suboptimal partition column layout",
      why: appendPartitionEvolutionWhy(
        `Access patterns show query filters target "${recommendedSpec}" instead of current key "${currentSpec}". This layout forces the execution engine to perform costly full table scans.`,
        partitionEvolution
      ),
      current: currentSpec,
      ideal: recommendedSpec
    };
  };

  const checkPartitionGranularity = (): {
    status: "optimal" | "suboptimal" | "critical";
    tagText: string;
    tagColor: "success" | "warning" | "error";
    what: string;
    why: string;
    current: string;
    ideal: string;
  } => {
    const currentCol = stats?.partition_col;
    const hasRec = !!partitionEvolution;
    const currentSpec =
      partitionEvolution?.current_partition_spec ||
      (currentCol ? `identity(${currentCol})` : "unpartitioned");
    const recommendedSpec = partitionEvolution?.recommended_partition_spec;
    const totalPartitions = stats?.total_partitions ?? 0;
    const avgFilesPerPartition = stats?.avg_files_per_partition ?? 0;

    if (!currentCol || currentCol === "unpartitioned" || currentSpec.toLowerCase() === "unpartitioned") {
      return {
        status: "optimal",
        tagText: "Optimal (No Skew)",
        tagColor: "success",
        what: "No partition granularity overhead",
        why: "Being unpartitioned prevents directory-level metadata fragmentation and limits metadata catalog checks.",
        current: "None (Unpartitioned)",
        ideal: "None"
      };
    }
    if (totalPartitions > 10000) {
      return {
        status: "critical",
        tagText: "Granularity Too Fine",
        tagColor: "error",
        what: "Over-partitioning complexity triggered",
        why: appendPartitionEvolutionWhy(
          `The table contains ${totalPartitions.toLocaleString()} partitions, causing partition bloating. This high directory complexity degrades query compilation and catalog list performance.`,
          partitionEvolution
        ),
        current: `${totalPartitions.toLocaleString()} partitions (${stats?.partition_col_type || "identity"})`,
        ideal: "< 5,000 partitions (e.g. Month transform)"
      };
    }
    if (avgFilesPerPartition > 0 && avgFilesPerPartition < 2 && totalPartitions > 500) {
      return {
        status: "suboptimal",
        tagText: "Suboptimal (Too Granular)",
        tagColor: "warning",
        what: "Oversized partition catalog metadata",
        why: appendPartitionEvolutionWhy(
          `Average files per partition is too low (${avgFilesPerPartition.toFixed(1)}). The current transform creates too many sparse directories. Consolidate to daily or monthly.`,
          partitionEvolution
        ),
        current: `${avgFilesPerPartition.toFixed(1)} files/partition`,
        ideal: "10 to 100 files/partition"
      };
    }
    if (hasRec && recommendedSpec) {
      const getGranularitiesFromSpec = (spec: string): string[] =>
        spec.split(",").map((p) => p.trim()).map((part) => {
          const match = part.match(/^(.*?)\(/);
          return match ? match[1] : "identity";
        });
      const currentTrans = getGranularitiesFromSpec(currentSpec).join(", ");
      const recTrans = getGranularitiesFromSpec(recommendedSpec).join(", ");
      if (currentTrans.toLowerCase() !== recTrans.toLowerCase()) {
        const isFiner =
          (currentTrans.includes("hour") && !recTrans.includes("hour")) ||
          (currentTrans.includes("day") && (recTrans.includes("month") || recTrans.includes("year"))) ||
          (currentTrans.includes("month") && recTrans.includes("year"));
        return {
          status: "suboptimal",
          tagText: isFiner ? "Suboptimal (Too Fine)" : "Suboptimal (Too Coarse)",
          tagColor: "warning",
          what: isFiner ? "Partition transform is too granular" : "Partition transform is too coarse",
          why: appendPartitionEvolutionWhy(
            isFiner
              ? `Current transform "${currentTrans}" creates sparse directories. Evolving to "${recTrans}" will combine small write commits and speed up snapshot metadata planning.`
              : `Current transform "${currentTrans}" is too broad. Query engines must scan large ranges. Evolving to "${recTrans}" restricts bounds and speeds up execution.`,
            partitionEvolution
          ),
          current: currentTrans || "identity",
          ideal: recTrans || "identity"
        };
      }
    }
    return {
      status: "optimal",
      tagText: "Optimal Granularity",
      tagColor: "success",
      what: "Partition granularity is correct",
      why: appendPartitionEvolutionWhy(
        `The average file count (${avgFilesPerPartition.toFixed(1)} files/partition) and partition count (${totalPartitions}) represent a balanced data-to-metadata distribution.`,
        partitionEvolution
      ),
      current: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced",
      ideal: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced"
    };
  };

  const colCheck = checkPartitionColumn();
  const granCheck = checkPartitionGranularity();

  const triggered = (tableSignals?.signals ?? []).filter((s) => s.triggered);

  return (
    <div className="td-tab-content">
      <div className="td-overview-summary">
        <section className="td-overview-priority" aria-label="Priority actions">
          <ActNowSection
            signals={tableSignals}
            signalsLoading={tableSignalsLoading}
            signalsError={tableSignalsError}
            costView={costView}
            benefitScores={benefitScores}
            stats={stats ?? undefined}
            statsRaw={statsRaw}
            reclaimableBytes={reclaimableBytes}
            savingsUsd={savingsUsd}
            effectivePresetId={effectivePresetId}
            onRun={onRun}
            runPending={runPending}
            queuedMaintenanceRunId={queuedMaintenanceRunId}
            lastRunSkipNotes={lastRunSkipNotes}
            onOpenRun={onOpenRun}
            onPreview={onPreview}
            onSchedule={onSchedule}
            schedulePending={schedulePending}
            tableLabel={tableLabel}
          />
        </section>
        <aside className="td-health-summary" aria-label="Current health">
          <div>
            <h2 className="td-chart-card-title">Current health</h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground">Latest scan and 30-day direction</p>
          </div>
          <HealthGauge
            score={score}
            trendDirection={trendDirection}
            trendLabel={trendLabel}
            daysToCritical={healthTrends?.days_to_critical}
          />
          {rowExists ? <Alert variant={alertVariant} title={verdict.message} className="w-full text-xs" /> : null}
        </aside>
      </div>

      <div style={{ marginBottom: 16 }} />

      <div>
        <button
          type="button"
          className="td-diagnostics-toggle"
          onClick={() => setDiagnosticsOpen((v) => !v)}
          aria-expanded={diagnosticsOpen}
          aria-controls="table-diagnostics"
        >
          {diagnosticsOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          <span className="td-chart-card-title">Diagnostics</span>
          <span className="td-chart-card-subtitle">Health trend, partitioning, query load, score breakdown</span>
          {triggered.length > 0 ? (
            <Badge variant="warning" className="ml-2">
              {triggered.length} triggered
            </Badge>
          ) : null}
        </button>
        {diagnosticsOpen ? (
          <div id="table-diagnostics" className="mt-3 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,14fr)_minmax(0,10fr)]">
            <div className="flex flex-col gap-4">
              <ChartCard
                title="Health Trends"
                subtitle="30 days"
                actions={
                  degradationBadge ? (
                    <Badge variant="danger">{degradationBadge}</Badge>
                  ) : undefined
                }
                quiet={!trendData.length}
              >
                {trendData.length ? <HealthScoreTrend points={trendData} /> : (
                  <EmptyState
                    title="No trends yet"
                    description="Health trends appear after multiple scans over time."
                  />
                )}
              </ChartCard>

              <ChartCard
                title={
                  <span className="inline-flex items-center gap-2">
                    <GitBranch className="size-4" style={{ color: "var(--primary, #3d6fd4)" }} />
                    Partition Columns Analyzer
                  </span>
                }
                subtitle="Analyzing schema keys, access patterns, and pruning granularity"
                contentClassName="flex flex-col gap-4"
              >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                      gap: 12,
                      padding: "12px 16px",
                      borderRadius: 8,
                      backgroundColor: "rgba(92, 102, 122, 0.03)",
                      border: "1px solid var(--line, #e1e6ed)"
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 600,
                          color: "var(--muted, #5c667a)",
                          textTransform: "uppercase",
                          letterSpacing: "0.5px"
                        }}
                      >
                        Active Partition Column
                      </span>
                      <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>
                        {formatPartitionColumnLabel(stats?.partition_col)}
                      </strong>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 600,
                          color: "var(--muted, #5c667a)",
                          textTransform: "uppercase",
                          letterSpacing: "0.5px"
                        }}
                      >
                        Total Partitions
                      </span>
                      <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>
                        {(stats?.total_partitions ?? 0).toLocaleString()}
                      </strong>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 600,
                          color: "var(--muted, #5c667a)",
                          textTransform: "uppercase",
                          letterSpacing: "0.5px"
                        }}
                      >
                        Avg Files / Partition
                      </span>
                      <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>
                        {(stats?.avg_files_per_partition ?? 0).toFixed(1)}
                      </strong>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 600,
                          color: "var(--muted, #5c667a)",
                          textTransform: "uppercase",
                          letterSpacing: "0.5px"
                        }}
                      >
                        Volume Skew Ratio
                      </span>
                      <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>
                        {(stats?.partition_skew_ratio ?? 0).toFixed(1)}x
                      </strong>
                    </div>
                  </div>

                  <PartitionChecklist
                    title="Check 1: Target Column Alignment"
                    status={colCheck.status}
                    tagText={colCheck.tagText}
                    tagColor={colCheck.tagColor}
                    what={colCheck.what}
                    why={colCheck.why}
                    current={colCheck.current}
                    ideal={colCheck.ideal}
                  />
                  <PartitionChecklist
                    title="Check 2: Partition Granularity Validation"
                    status={granCheck.status}
                    tagText={granCheck.tagText}
                    tagColor={granCheck.tagColor}
                    what={granCheck.what}
                    why={granCheck.why}
                    current={granCheck.current}
                    ideal={granCheck.ideal}
                  />
              </ChartCard>

              <ChartCard
                title="Query load"
                subtitle="Last 7 days"
                actions={
                  tableQueries && tableQueries.failed_count > 0 ? (
                    <Badge variant="danger" className="td-quality-anomaly-tag">
                      {tableQueries.failed_count} failed
                    </Badge>
                  ) : undefined
                }
              >
                {tableQueriesLoading ? (
                  <span className="text-xs text-muted-foreground">Loading query activity…</span>
                ) : tableQueries && tableQueries.total > 0 ? (
                  <>
                    <div className="td-query-load-stats">
                      <div className="td-query-load-stat">
                        <span className="td-query-load-stat-label">Queries</span>
                        <span className="td-query-load-stat-value">{formatNumber(tableQueries.total)}</span>
                      </div>
                      <div className="td-query-load-stat">
                        <span className="td-query-load-stat-label">P50 latency</span>
                        <span className="td-query-load-stat-value">{formatDuration(tableQueries.p50_latency_ms)}</span>
                      </div>
                      <div className="td-query-load-stat">
                        <span className="td-query-load-stat-label">P95 latency</span>
                        <span className="td-query-load-stat-value">{formatDuration(tableQueries.p95_latency_ms)}</span>
                      </div>
                      <div className="td-query-load-stat">
                        <span className="td-query-load-stat-label">P99 latency</span>
                        <span className="td-query-load-stat-value">{formatDuration(tableQueries.p99_latency_ms)}</span>
                      </div>
                      <div className="td-query-load-stat">
                        <span className="td-query-load-stat-label">Avg bytes scanned</span>
                        <span className="td-query-load-stat-value">{formatBytesGb(tableQueries.avg_bytes_scanned)}</span>
                      </div>
                    </div>
                    <div className="mt-3 overflow-x-auto">
                      <table className="td-schema-table">
                        <thead>
                          <tr>
                            <th style={{ width: 90 }}>Engine</th>
                            <th>User</th>
                            <th style={{ width: 110, textAlign: "right" }}>Duration</th>
                            <th style={{ width: 100 }}>Status</th>
                            <th style={{ textAlign: "right" }}>Started</th>
                          </tr>
                        </thead>
                        <tbody>
                          {tableQueries.queries.slice(0, 5).map((q) => (
                            <tr key={q.query_id}>
                              <td>{q.engine}</td>
                              <td className="max-w-[120px] truncate" title={q.user ?? undefined}>
                                {q.user ?? "—"}
                              </td>
                              <td style={{ textAlign: "right" }}>{formatDuration(q.duration_ms)}</td>
                              <td>
                                <Badge
                                  variant={q.status === "failed" ? "danger" : "success"}
                                  className="td-schema-status-tag"
                                >
                                  {q.status === "failed" ? "Failed" : "OK"}
                                </Badge>
                              </td>
                              <td style={{ textAlign: "right" }}>{formatTime(q.started_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : (
                  <EmptyState
                    title="No query activity"
                    description="No queries against this table were recorded in the last 7 days."
                  />
                )}
              </ChartCard>
            </div>
            <ChartCard title="Why this grade" className="h-full">
              {rowExists ? (
                <HealthScoreBreakdown
                  factors={scoreFactors}
                  deductions={hasPersistedDeductions ? pointDeductions : undefined}
                />
              ) : (
                <EmptyState title="No health details" description="Fetch or scan table to load diagnostics." />
              )}
            </ChartCard>
          </div>
        ) : null}
      </div>
    </div>
  );
};

interface PartitionChecklistProps {
  title: string;
  status: "optimal" | "suboptimal" | "critical";
  tagText: string;
  tagColor: "success" | "warning" | "error";
  what: string;
  why: string;
  current: string;
  ideal: string;
}

const tagColorToVariant = (color: "success" | "warning" | "error"): "success" | "warning" | "danger" =>
  color === "success" ? "success" : color === "warning" ? "warning" : "danger";

const PartitionChecklist = ({
  title,
  status,
  tagText,
  tagColor,
  what,
  why,
  current,
  ideal
}: PartitionChecklistProps) => {
  const accentColor =
    status === "optimal"
      ? "var(--success, #28a745)"
      : status === "critical"
        ? "var(--error, #dc3545)"
        : "var(--warning, #f0a020)";
  return (
    <div
      style={{
        border: "1px solid var(--line, #e1e6ed)",
        backgroundColor: "var(--background, #ffffff)",
        borderRadius: "8px",
        display: "flex",
        flexDirection: "row",
        overflow: "hidden",
        boxShadow: "0 2px 8px rgba(0,0,0,0.02)"
      }}
    >
      <div
        aria-hidden
        style={{
          width: 3,
          flexShrink: 0,
          backgroundColor: accentColor
        }}
      />
      <div
        style={{
          flex: 1,
          padding: "16px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 14
        }}
      >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor:
                status === "optimal"
                  ? "rgba(40, 167, 69, 0.08)"
                  : status === "critical"
                    ? "rgba(220, 53, 69, 0.08)"
                    : "rgba(240, 160, 32, 0.08)"
            }}
          >
            {status === "optimal" ? (
              <CheckCircle2 className="size-3.5" style={{ color: "var(--success, #28a745)" }} />
            ) : status === "critical" ? (
              <XCircle className="size-3.5" style={{ color: "var(--error, #dc3545)" }} />
            ) : (
              <AlertTriangle className="size-3.5" style={{ color: "var(--warning, #f0a020)" }} />
            )}
          </div>
          <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 13 }}>{title}</span>
        </div>
        <Badge
          variant={tagColorToVariant(tagColor)}
          className="m-0 text-[10px] font-bold"
          style={{ borderRadius: 4, padding: "2px 8px" }}
        >
          {tagText}
        </Badge>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          padding: "14px 16px",
          borderRadius: 6,
          backgroundColor: "rgba(92, 102, 122, 0.02)",
          border: "1px solid rgba(92, 102, 122, 0.06)",
          fontSize: 11
        }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span
              style={{
                fontWeight: 600,
                color: "var(--muted, #5c667a)",
                textTransform: "uppercase",
                fontSize: 9,
                letterSpacing: "0.5px"
              }}
            >
              Metric Name
            </span>
            <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 11.5 }}>{what}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span
              style={{
                fontWeight: 600,
                color: "var(--muted, #5c667a)",
                textTransform: "uppercase",
                fontSize: 9,
                letterSpacing: "0.5px"
              }}
            >
              Impact Description
            </span>
            <span style={{ color: "var(--muted, #5c667a)", fontSize: 11, lineHeight: "15px" }}>{why}</span>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            borderTop: "1px solid var(--line, #e1e6ed)",
            paddingTop: 12,
            marginTop: 4
          }}
        >
          {current === ideal ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
              <span
                style={{
                  fontWeight: 600,
                  color: "var(--muted, #5c667a)",
                  textTransform: "uppercase",
                  fontSize: 9,
                  letterSpacing: "0.5px"
                }}
              >
                Current (already optimal)
              </span>
              <div>
                <Badge variant="success" className="m-0 px-2 py-[3px] font-medium">
                  {current}
                </Badge>
              </div>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                <span
                  style={{
                    fontWeight: 600,
                    color: "var(--muted, #5c667a)",
                    textTransform: "uppercase",
                    fontSize: 9,
                    letterSpacing: "0.5px"
                  }}
                >
                  Current
                </span>
                <div>
                  <Badge variant="secondary" className="m-0 px-2 py-[3px] font-medium">
                    {current}
                  </Badge>
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  alignSelf: "flex-end",
                  height: 26,
                  paddingBottom: 2
                }}
              >
                <ArrowRight className="size-3 opacity-50" style={{ color: "var(--muted, #5c667a)" }} />
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                <span
                  style={{
                    fontWeight: 600,
                    color: "var(--muted, #5c667a)",
                    textTransform: "uppercase",
                    fontSize: 9,
                    letterSpacing: "0.5px"
                  }}
                >
                  Target
                </span>
                <div>
                  <Badge variant="default" className="m-0 px-2 py-[3px] font-medium">
                    {ideal}
                  </Badge>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      </div>
    </div>
  );
};