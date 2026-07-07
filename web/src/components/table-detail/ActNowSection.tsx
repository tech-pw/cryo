/**
 * "Act Now" — the deduplicated, severity-sorted list of real problems on this table.
 *
 * Replaces two things that used to render the *same* triggered-signals data twice on one
 * screen: the top RecommendedActions strip and the "Detected Health Issues & Remediation"
 * cards further down. This component builds one merged list instead, from three genuinely
 * distinct sources (triggered diagnostic signals, positive-savings cost operations, and the
 * top benefit-scored compaction candidate) and renders each issue exactly once.
 *
 * "Act Now" collapses to a single shared banner while a run is in flight, not per-card —
 * there is only one `run.mutate()` for the whole table today (no per-issue remediation
 * endpoint exists), so per-card collapse would misrepresent what actually happened.
 */
import {
  AppstoreOutlined,
  CheckCircleOutlined,
  ClearOutlined,
  CloseCircleOutlined,
  FireOutlined,
  HistoryOutlined,
  RadarChartOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  WarningOutlined
} from "@ant-design/icons";
import { Button, Card, Select, Space, Tag, Typography } from "antd";
import { useState, type ReactNode } from "react";
import type { BenefitScoreResponse, TableSignalRead, TableSignalsResponse } from "../../api/client";
import type { TableCostRead } from "../../api/types";
import { formatBytesGb, formatCurrency, formatNumber } from "../../lib/format";
import { operationLabels } from "../../lib/theme";
import { ConfirmAction } from "../confirm";
import { QueryErrorAlert } from "../common";
import { getSignalMatrix, getSignalPointerDetail } from "./helpers";

type Severity = "critical" | "warning" | "info";

interface HealthIssue {
  id: string;
  severity: Severity;
  icon: ReactNode;
  title: string;
  impactText: string;
  current?: string;
  ideal?: string;
  why?: string;
  recommendation?: string;
}

const operationIcon = (operation: string): ReactNode => {
  if (operation === "optimize") return <FireOutlined />;
  if (operation === "remove_orphan_files") return <ClearOutlined />;
  if (operation === "expire_snapshots") return <HistoryOutlined />;
  if (operation === "optimize_manifests") return <AppstoreOutlined />;
  return <RadarChartOutlined />;
};

const severityIcon = (severity: Severity): ReactNode => {
  if (severity === "critical") return <CloseCircleOutlined style={{ color: "var(--error, #dc3545)" }} />;
  if (severity === "warning") return <WarningOutlined style={{ color: "var(--warning, #f0a020)" }} />;
  return <RadarChartOutlined style={{ color: "var(--muted, #5c667a)" }} />;
};

const severityTagColor = (severity: Severity): string =>
  severity === "critical" ? "red" : severity === "warning" ? "orange" : "default";

const severityRank: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };

function buildIssues(params: {
  signals?: TableSignalsResponse;
  costView?: TableCostRead;
  benefitScores?: BenefitScoreResponse;
  stats: { total_files?: number; small_files_count?: number } | null | undefined;
  statsRaw: Record<string, unknown>;
  reclaimableBytes: number;
  savingsUsd: number;
}): HealthIssue[] {
  const { signals, costView, benefitScores, stats, statsRaw, reclaimableBytes, savingsUsd } = params;
  const issues: HealthIssue[] = [];

  const totalFiles = stats?.total_files ?? 0;
  const smallFiles = stats?.small_files_count ?? 0;
  const smallPct = totalFiles > 0 ? (smallFiles / totalFiles) * 100 : 0;

  const triggered: TableSignalRead[] = (signals?.signals ?? []).filter(
    (s) => s.triggered && s.severity !== "info"
  );
  for (const signal of triggered) {
    const details = getSignalPointerDetail(signal.signal_name, signal.severity);
    const matrix = getSignalMatrix(signal.signal_name, stats, statsRaw, reclaimableBytes, savingsUsd, smallPct);
    const severity: Severity = signal.severity === "critical" ? "critical" : "warning";
    issues.push({
      id: `signal-${signal.signal_name}`,
      severity,
      icon: severityIcon(severity),
      title: details.title,
      impactText: details.impactText,
      current: matrix.current,
      ideal: matrix.ideal,
      why: matrix.why,
      recommendation: details.recommendation
    });
  }

  for (const op of costView?.by_operation ?? []) {
    if (op.savings_usd_month <= 0) continue;
    issues.push({
      id: `op-${op.operation}`,
      severity: op.savings_usd_month >= 100 ? "warning" : "info",
      icon: operationIcon(op.operation),
      title: operationLabels[op.operation] ?? op.operation,
      impactText: `Save ${formatCurrency(op.savings_usd_month)}/mo`,
      current: `${formatBytesGb(op.reclaimable_bytes)} reclaimable`,
      recommendation: `Run maintenance to reclaim ${formatBytesGb(op.reclaimable_bytes)} and save ${formatCurrency(op.savings_usd_month)}/month.`
    });
  }

  const topPartition = benefitScores?.top_candidates?.[0];
  if (topPartition?.benefit_score && topPartition.benefit_score > 0.05) {
    issues.push({
      id: "hot-partition",
      severity: "info",
      icon: <FireOutlined />,
      title: "Hot partition needs compaction",
      impactText: `${formatNumber(topPartition.small_files)} small files`,
      current: `${topPartition.partition_value ?? "(table)"} — benefit score ${topPartition.benefit_score.toFixed(3)}`,
      recommendation: "This specific partition is the fastest win — compact it first."
    });
  }

  return issues.sort((a, b) => severityRank[a.severity] - severityRank[b.severity]);
}

interface ActNowSectionProps {
  signals?: TableSignalsResponse;
  signalsLoading: boolean;
  signalsError: unknown;
  costView?: TableCostRead;
  benefitScores?: BenefitScoreResponse;
  stats: { total_files?: number; small_files_count?: number } | null | undefined;
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
  onOpenRun: () => void;
  onPreview: () => void;
  tableLabel: string;
}

export const ActNowSection = ({
  signals,
  signalsLoading,
  signalsError,
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
  onOpenRun,
  onPreview,
  tableLabel
}: ActNowSectionProps) => {
  const [acting, setActing] = useState(false);

  const handleRun = () => {
    setActing(true);
    onRun();
  };

  if (signalsError) {
    return <QueryErrorAlert error={signalsError} title="Failed to load table signals" />;
  }

  if (signalsLoading) {
    return (
      <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Checking diagnostic signals…
        </Typography.Text>
      </Card>
    );
  }

  const inFlight = acting || runPending || Boolean(queuedMaintenanceRunId);
  if (inFlight) {
    return (
      <Card className="td-chart-card td-act-now-banner" classNames={{ body: "td-chart-card-body" }}>
        <Space align="center" size={10}>
          <SyncOutlined spin style={{ color: "var(--primary, #3d6fd4)" }} />
          <Typography.Text strong style={{ fontSize: 13 }}>
            {runPending ? "Queueing maintenance run…" : "Maintenance run queued for this table"}
          </Typography.Text>
        </Space>
        {queuedMaintenanceRunId ? (
          <Button size="small" type="primary" onClick={onOpenRun} style={{ marginLeft: 16 }}>
            Open run
          </Button>
        ) : null}
      </Card>
    );
  }

  const issues = buildIssues({ signals, costView, benefitScores, stats, statsRaw, reclaimableBytes, savingsUsd });

  if (!issues.length) {
    return (
      <Card className="td-chart-card td-act-now-healthy" classNames={{ body: "td-chart-card-body" }}>
        <Space align="center" size={8}>
          <CheckCircleOutlined style={{ color: "var(--success, #28a745)" }} />
          <Typography.Text strong style={{ fontSize: 12, color: "var(--success, #28a745)" }}>
            No issues detected
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            All {signals?.signals.length ?? 0} background diagnostic checks passed.
          </Typography.Text>
        </Space>
      </Card>
    );
  }

  return (
    <div className="td-act-now-section">
      <div className="td-act-now-header">
        <span className="td-chart-card-title">Act now</span>
        <Tag color="error" style={{ margin: 0 }}>{issues.length}</Tag>
        <div style={{ flex: 1 }} />
        <Space size={6}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>Engine preset:</Typography.Text>
          <Select
            placeholder="Assign preset"
            value={effectivePresetId}
            options={presetOptions}
            onChange={onAssignPreset}
            loading={assignPending || presetsLoading}
            size="small"
            style={{ width: 160 }}
            popupMatchSelectWidth={false}
          />
        </Space>
      </div>
      <div className="td-act-now-list">
        {issues.map((issue) => (
          <Card key={issue.id} className={`td-act-now-card td-act-now-${issue.severity}`}>
            <div className="td-act-now-card-head">
              <Space size={8} align="center">
                {issue.icon}
                <Typography.Text strong style={{ fontSize: 13 }}>{issue.title}</Typography.Text>
              </Space>
              <Tag color={severityTagColor(issue.severity)} style={{ margin: 0 }}>{issue.impactText}</Tag>
            </div>
            {issue.why ? (
              <Typography.Text type="secondary" className="td-act-now-why">{issue.why}</Typography.Text>
            ) : null}
            {issue.current && issue.ideal && issue.current !== issue.ideal ? (
              <div className="td-act-now-transition">
                <Tag style={{ margin: 0 }}>{issue.current}</Tag>
                <span className="td-act-now-arrow">&rarr;</span>
                <Tag color="blue" style={{ margin: 0 }}>{issue.ideal}</Tag>
              </div>
            ) : issue.current ? (
              <Tag style={{ margin: 0, alignSelf: "flex-start" }}>{issue.current}</Tag>
            ) : null}
            {issue.recommendation ? (
              <Typography.Text className="td-act-now-recommendation">
                <ThunderboltOutlined style={{ marginRight: 6, color: "var(--primary, #3d6fd4)" }} />
                {issue.recommendation}
              </Typography.Text>
            ) : null}
            <Space size={8}>
              <Button size="small" onClick={onPreview}>Preview</Button>
              <ConfirmAction
                title="Run maintenance now?"
                description={`This queues maintenance for ${tableLabel} using the assigned engine preset.`}
                onConfirm={handleRun}
              >
                <Button size="small" type="primary" disabled={!effectivePresetId}>
                  Act now
                </Button>
              </ConfirmAction>
            </Space>
          </Card>
        ))}
      </div>
    </div>
  );
};
