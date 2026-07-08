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
  ArrowRightOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DashboardOutlined,
  DownOutlined,
  InfoCircleOutlined,
  PartitionOutlined,
  RightOutlined,
  TableOutlined,
  WarningOutlined
} from "@ant-design/icons";
import {
  Alert,
  Card,
  Col,
  Empty as AntdEmpty,
  Row,
  Space,
  Table,
  Tag,
  Typography
} from "antd";
import { useState } from "react";
import {
  Area,
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
import { EmptyState } from "../common";
import { HealthScoreBreakdown } from "../HealthScoreBreakdown";
import type { ScoreFactor as HealthScoreFactor } from "../HealthScoreBreakdown";
import { ActNowSection } from "./ActNowSection";
import { HealthGauge } from "./HealthGauge";
import { buildDiagnosisVerdict, type DiagnosisVerdict } from "../TableDiagnosisPanel";
import { chartTooltipProps, cryoColors } from "../../lib/theme";
import { formatBytesGb, formatDuration, formatNumber, formatTime } from "../../lib/format";

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
  reclaimableBytes: number;
  savingsUsd: number;
  presetOptions: { label: string; value: string }[];
  presetsLoading: boolean;
  effectivePresetId?: string;
  onAssignPreset: (presetId: string) => void;
  assignPending: boolean;
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
  pointDeductions: Array<{ label: string; deduction: number; severity: "critical" | "warning"; reason: string }>;
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
    presetOptions,
    presetsLoading,
    effectivePresetId,
    onAssignPreset,
    assignPending,
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

  const alertType: "error" | "warning" | "success" =
    verdict.level === "critical" ? "error" : verdict.level === "warning" ? "warning" : "success";

  const trendData = (healthTrends?.trend_points ?? []).map((p) => ({
    ...p,
    timestamp: new Date(p.timestamp).getTime()
  }));

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
        why: `High-volume queries frequently filter on columns aligned with "${recommendedSpec}". Partitioning on these columns would reduce plan-time overhead and accelerate searches.`,
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
        why: `Historical access patterns successfully utilize "${currentCol}" as the pruning index. No partition column evolution required.`,
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
        why: `Queries frequently filter on "${missingCols.join(", ")}" alongside your current partition key. Evolving the partition spec to "${recommendedSpec}" will reduce raw bytes scanned.`,
        current: currentSpec,
        ideal: recommendedSpec
      };
    }
    return {
      status: "suboptimal",
      tagText: "Column Mismatch",
      tagColor: "error",
      what: "Suboptimal partition column layout",
      why: `Access patterns show query filters target "${recommendedSpec}" instead of current key "${currentSpec}". This layout forces the execution engine to perform costly full table scans.`,
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
        why: `The table contains ${totalPartitions.toLocaleString()} partitions, causing partition bloating. This high directory complexity degrades query compilation and catalog list performance.`,
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
        why: `Average files per partition is too low (${avgFilesPerPartition.toFixed(1)}). The current transform creates too many sparse directories. Consolidate to daily or monthly.`,
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
          why: isFiner
            ? `Current transform "${currentTrans}" creates sparse directories. Evolving to "${recTrans}" will combine small write commits and speed up snapshot metadata planning.`
            : `Current transform "${currentTrans}" is too broad. Query engines must scan large ranges. Evolving to "${recTrans}" restricts bounds and speeds up execution.`,
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
      why: `The average file count (${avgFilesPerPartition.toFixed(1)} files/partition) and partition count (${totalPartitions}) represent a balanced data-to-metadata distribution.`,
      current: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced",
      ideal: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced"
    };
  };

  const colCheck = checkPartitionColumn();
  const granCheck = checkPartitionGranularity();

  const triggered = (tableSignals?.signals ?? []).filter((s) => s.triggered);

  return (
    <div className="td-tab-content">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <HealthGauge
          score={score}
          trendDirection={trendDirection}
          trendLabel={trendLabel}
          daysToCritical={healthTrends?.days_to_critical}
        />
        {rowExists ? (
          <Alert
            type={alertType}
            showIcon
            title={<strong>{verdict.message}</strong>}
            style={{ fontSize: 12, flex: 1 }}
          />
        ) : null}
      </div>

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
        presetOptions={presetOptions}
        presetsLoading={presetsLoading}
        effectivePresetId={effectivePresetId}
        onAssignPreset={onAssignPreset}
        assignPending={assignPending}
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

      <div style={{ marginBottom: 16 }} />

      <div>
        <div
          className="td-diagnostics-toggle"
          role="button"
          tabIndex={0}
          onClick={() => setDiagnosticsOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setDiagnosticsOpen((v) => !v);
          }}
        >
          {diagnosticsOpen ? <DownOutlined /> : <RightOutlined />}
          <span className="td-chart-card-title">Diagnostics</span>
          <span className="td-chart-card-subtitle">Health trend, partitioning, query load, score breakdown</span>
          {triggered.length > 0 ? (
            <Tag color="warning" style={{ marginLeft: 8 }}>
              {triggered.length} triggered
            </Tag>
          ) : null}
        </div>
        {diagnosticsOpen ? (
          <Row gutter={[16, 16]} style={{ marginTop: 12 }}>
            <Col xs={24} xl={14}>
              <Card
                className={`td-chart-card${trendData.length ? "" : " td-chart-card-quiet"}`}
                classNames={{ body: "td-chart-card-body" }}
                style={{ marginBottom: 16 }}
              >
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Health Trends</span>
                  <span className="td-chart-card-subtitle">30 days</span>
                  {degradationBadge ? <Tag color="error">{degradationBadge}</Tag> : null}
                </div>
                <div className="td-chart-card-content">
                  {trendData.length ? (
                    <div className="chart-frame" style={{ height: 260 }}>
                      <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={trendData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
                          <XAxis
                            dataKey="timestamp"
                            tickFormatter={(ts) =>
                              new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })
                            }
                            tick={{ fontSize: 11 }}
                            interval="preserveStartEnd"
                            stroke="var(--muted, #5c667a)"
                          />
                          <YAxis
                            yAxisId="left"
                            domain={[0, 100]}
                            tick={{ fontSize: 11 }}
                            stroke="var(--muted, #5c667a)"
                            tickLine={false}
                          />
                          <YAxis
                            yAxisId="right"
                            orientation="right"
                            tick={{ fontSize: 11 }}
                            stroke="var(--muted, #5c667a)"
                            tickLine={false}
                          />
                          <RechartsTooltip
                            {...chartTooltipProps}
                            labelFormatter={(ts) => new Date(ts).toLocaleString()}
                            formatter={(value, name) => {
                              const n = Number(value ?? 0);
                              const label = String(name);
                              if (label === "score") return [n.toFixed(1), "Health Score"];
                              if (label === "small_files") return [n.toLocaleString(), "Small Files"];
                              if (label === "total_size_gb") return [n.toFixed(1) + " GB", "Size"];
                              return [n, label];
                            }}
                          />
                          <defs>
                            <linearGradient id="scoreGrad2" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor={cryoColors.gradeA} stopOpacity={0.15} />
                              <stop offset="100%" stopColor={cryoColors.gradeA} stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <Area
                            yAxisId="left"
                            type="monotone"
                            dataKey="score"
                            fill="url(#scoreGrad2)"
                            stroke="none"
                            tooltipType="none"
                          />
                          <Line
                            yAxisId="left"
                            type="monotone"
                            dataKey="score"
                            stroke={cryoColors.gradeA}
                            strokeWidth={2.5}
                            dot={false}
                            name="score"
                          />
                          <Line
                            yAxisId="right"
                            type="monotone"
                            dataKey="small_files"
                            stroke={cryoColors.chartPrimary}
                            strokeWidth={2}
                            dot={false}
                            name="small_files"
                            strokeDasharray="4 3"
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <EmptyState
                      title="No trends yet"
                      description="Health trends appear after multiple scans over time."
                    />
                  )}
                </div>
              </Card>

              <Card
                className="td-chart-card"
                classNames={{ body: "td-chart-card-body" }}
                style={{ margin: 0 }}
              >
                <div className="td-chart-card-header" style={{ marginBottom: 16 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <BranchesOutlined style={{ color: "var(--primary, #3d6fd4)", fontSize: 16 }} />
                    <span className="td-chart-card-title">Partition Columns Analyzer</span>
                  </div>
                  <span className="td-chart-card-subtitle">
                    Analyzing schema keys, access patterns, and pruning granularity
                  </span>
                </div>

                <div
                  className="td-chart-card-content"
                  style={{ display: "flex", flexDirection: "column", gap: 16 }}
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
                        {stats?.partition_col ? formatPartitionColumnLabel(stats.partition_col) : "None"}
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
                </div>
              </Card>

              <Card
                className="td-chart-card"
                classNames={{ body: "td-chart-card-body" }}
                style={{ marginTop: 16 }}
              >
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Query load</span>
                  <span className="td-chart-card-subtitle">Last 7 days</span>
                  {tableQueries && tableQueries.failed_count > 0 ? (
                    <Tag color="error" className="td-quality-anomaly-tag">
                      {tableQueries.failed_count} failed
                    </Tag>
                  ) : null}
                </div>
                <div className="td-chart-card-content">
                  {tableQueriesLoading ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      Loading query activity…
                    </Typography.Text>
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
                      <Table
                        size="small"
                        rowKey="query_id"
                        dataSource={tableQueries.queries.slice(0, 5)}
                        pagination={false}
                        className="td-schema-table"
                        style={{ marginTop: 12 }}
                        locale={{ emptyText: <AntdEmpty description="No queries" /> }}
                        columns={[
                          { title: "Engine", dataIndex: "engine", width: 90 },
                          { title: "User", dataIndex: "user", ellipsis: true, render: (v) => v ?? "—" },
                          {
                            title: "Duration",
                            dataIndex: "duration_ms",
                            width: 110,
                            align: "right" as const,
                            render: (v) => formatDuration(v)
                          },
                          {
                            title: "Status",
                            dataIndex: "status",
                            width: 100,
                            render: (v: string) => (
                              <Tag
                                color={v === "failed" ? "error" : "success"}
                                className="td-schema-status-tag"
                              >
                                {v === "failed" ? "Failed" : "OK"}
                              </Tag>
                            )
                          },
                          {
                            title: "Started",
                            dataIndex: "started_at",
                            align: "right" as const,
                            render: (v) => formatTime(v)
                          }
                        ]}
                      />
                    </>
                  ) : (
                    <EmptyState
                      title="No query activity"
                      description="No queries against this table were recorded in the last 7 days."
                    />
                  )}
                </div>
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              <Card
                className="td-chart-card"
                classNames={{ body: "td-chart-card-body" }}
                style={{ height: "100%", margin: 0 }}
              >
                <div className="td-chart-card-header" style={{ marginBottom: 16 }}>
                  <span className="td-chart-card-title">Score Factors</span>
                </div>
                {rowExists ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <div className="td-sidebar-score-bars">
                      <HealthScoreBreakdown factors={scoreFactors} />
                    </div>
                    {pointDeductions.length > 0 ? (
                      <div>
                        <div
                          className="td-diagnosis-section-title"
                          style={{
                            fontWeight: 600,
                            fontSize: 11,
                            marginBottom: 8,
                            color: "var(--muted, #5c667a)",
                            textTransform: "uppercase",
                            letterSpacing: "0.5px"
                          }}
                        >
                          Deduction Breakdown
                        </div>
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                            padding: "10px 12px",
                            borderRadius: 6,
                            backgroundColor:
                              score < 60
                                ? "rgba(220, 53, 69, 0.025)"
                                : score < 80
                                  ? "rgba(240, 160, 32, 0.025)"
                                  : "rgba(92, 102, 122, 0.015)",
                            border:
                              score < 60
                                ? "1px solid rgba(220, 53, 69, 0.12)"
                                : score < 80
                                  ? "1px solid rgba(240, 160, 32, 0.15)"
                                  : "1px solid var(--line, #e1e6ed)"
                          }}
                        >
                          {pointDeductions.map((item, idx) => (
                            <div
                              key={idx}
                              style={{
                                display: "flex",
                                justifyContent: "space-between",
                                alignItems: "center",
                                fontSize: 11,
                                gap: 12
                              }}
                            >
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 6,
                                  minWidth: 0,
                                  flex: 1
                                }}
                              >
                                <span
                                  style={{
                                    color:
                                      item.severity === "critical"
                                        ? "var(--error, #dc3545)"
                                        : "var(--warning, #f0a020)",
                                    fontWeight: 700,
                                    flexShrink: 0
                                  }}
                                >
                                  •
                                </span>
                                <span
                                  style={{
                                    color: "var(--foreground, #1a202c)",
                                    fontWeight: 600,
                                    flexShrink: 0
                                  }}
                                >
                                  {item.label}
                                </span>
                                <span
                                  style={{
                                    color: "var(--muted, #5c667a)",
                                    opacity: 0.8,
                                    fontSize: 10,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap"
                                  }}
                                  title={item.reason}
                                >
                                  {item.reason}
                                </span>
                              </div>
                              <span
                                style={{
                                  fontWeight: 700,
                                  color:
                                    item.severity === "critical"
                                      ? "var(--error, #dc3545)"
                                      : "var(--warning, #f0a020)",
                                  flexShrink: 0
                                }}
                              >
                                -{item.deduction} pts
                              </span>
                            </div>
                          ))}
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              borderTop: "1px dashed var(--line, #e1e6ed)",
                              paddingTop: 6,
                              marginTop: 4,
                              fontWeight: 700,
                              fontSize: 11
                            }}
                          >
                            <span style={{ color: "var(--foreground, #1a202c)" }}>Total Deductions:</span>
                            <span style={{ color: "var(--error, #dc3545)" }}>
                              -{pointDeductions.reduce((acc, curr) => acc + curr.deduction, 0)} pts
                            </span>
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <EmptyState title="No health details" description="Fetch or scan table to load diagnostics." />
                )}
              </Card>
            </Col>
          </Row>
        ) : null}
      </div>
    </div>
  );
};

const formatPartitionColumnLabel = (col: string): string => {
  if (!col || col === "_unpartitioned" || col.toLowerCase() === "unpartitioned") return "Unpartitioned";
  return col;
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
  const borderLeftColor =
    status === "optimal"
      ? "var(--success, #28a745)"
      : status === "critical"
        ? "var(--error, #dc3545)"
        : "var(--warning, #f0a020)";
  return (
    <div
      style={{
        border: "1px solid var(--line, #e1e6ed)",
        borderLeft: `4px solid ${borderLeftColor}`,
        backgroundColor: "var(--background, #ffffff)",
        padding: "16px 20px",
        borderRadius: "8px",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        boxShadow: "0 2px 8px rgba(0,0,0,0.02)"
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
              <CheckCircleOutlined style={{ color: "var(--success, #28a745)", fontSize: 13 }} />
            ) : status === "critical" ? (
              <CloseCircleOutlined style={{ color: "var(--error, #dc3545)", fontSize: 13 }} />
            ) : (
              <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 13 }} />
            )}
          </div>
          <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 13 }}>{title}</span>
        </div>
        <Tag
          color={tagColor}
          style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4, padding: "2px 8px" }}
        >
          {tagText}
        </Tag>
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
                <Tag color="success" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>
                  {current}
                </Tag>
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
                  <Tag style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{current}</Tag>
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
                <ArrowRightOutlined style={{ color: "var(--muted, #5c667a)", opacity: 0.5, fontSize: 12 }} />
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
                  <Tag color="blue" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>
                    {ideal}
                  </Tag>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// Avoid unused-import lints for icons only referenced inside JSX branches above.
void InfoCircleOutlined;
void TableOutlined;
void PartitionOutlined;
void DashboardOutlined;