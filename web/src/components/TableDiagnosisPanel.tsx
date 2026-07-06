import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Tag, Typography } from "antd";
import { getTableSignals } from "../api/client";
import type { TableStatsRead, UUID } from "../api/types";
import { QueryErrorAlert } from "./common";
import { HealthScoreBreakdown, type ScoreFactor } from "./HealthScoreBreakdown";
import { healthScoreColor } from "./status";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";

export type DiagnosisLevel = "critical" | "warning" | "ok";

export interface DiagnosisVerdict {
  level: DiagnosisLevel;
  message: string;
}

const PRIORITY_PHRASE: Record<string, string> = {
  HIGH: "high priority",
  MEDIUM: "medium priority",
  LOW: "low priority"
};

/** Pure verdict logic over fields already fetched by the page's own `table` query -- no new
 * backend calls. This is the single "why is this table graded what it is" narrative that was
 * previously scattered across a metric-tile hint, a metadata-card tag, and a table buried at the
 * bottom of the page. */
export const buildDiagnosisVerdict = (stats?: TableStatsRead | null): DiagnosisVerdict => {
  const grade = stats?.grade ?? "-";
  const needsCompaction = Boolean(stats?.needs_compaction);
  const priority = stats?.compaction_priority;
  const gradePoor = grade === "D" || grade === "F";

  if (needsCompaction || gradePoor) {
    const level: DiagnosisLevel = grade === "F" || priority === "HIGH" ? "critical" : "warning";
    const phrase = priority ? PRIORITY_PHRASE[priority] ?? `${priority.toLowerCase()} priority` : null;
    return {
      level,
      message: `Grade ${grade}${phrase ? ` — ${phrase}` : ""}: this table needs compaction.`
    };
  }
  return { level: "ok", message: `Grade ${grade} — healthy, no action needed.` };
};

const ALERT_TYPE: Record<DiagnosisLevel, "error" | "warning" | "success"> = {
  critical: "error",
  warning: "warning",
  ok: "success"
};

export interface TableDiagnosisPanelProps {
  tableId: UUID;
  stats?: TableStatsRead | null;
  factors: ScoreFactor[];
}

export const TableDiagnosisPanel = ({ tableId, stats, factors }: TableDiagnosisPanelProps) => {
  const { api } = useAuth();
  const signals = useQuery({
    queryKey: ["table-signals", tableId],
    queryFn: () => getTableSignals(api, tableId),
    retry: false
  });

  const triggered = signals.data?.signals.filter((signal) => signal.triggered) ?? [];
  const verdict = buildDiagnosisVerdict(stats);
  const accentColor = healthScoreColor(stats?.overall_score ?? 0);

  return (
    <Card
      className="td-chart-card td-diagnosis-card"
      classNames={{ body: "td-chart-card-body" }}
      style={{ "--tile-accent": accentColor } as React.CSSProperties}
    >
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">Diagnosis</span>
        {signals.data ? (
          <span className="td-chart-card-subtitle">Checked {formatTime(signals.data.checked_at)}</span>
        ) : null}
      </div>
      <div className="td-chart-card-content">
        <Alert
          type={ALERT_TYPE[verdict.level]}
          showIcon
          message={<strong>{verdict.message}</strong>}
          style={{ marginBottom: 12 }}
        />

        <div className="td-diagnosis-section-title">Score factors</div>
        <div className="td-diagnosis-score-bars">
          <HealthScoreBreakdown factors={factors} />
        </div>

        <div className="td-diagnosis-section-title">Active signals</div>
        <QueryErrorAlert error={signals.error} title="Failed to load table signals" />
        {signals.isLoading ? (
          <Typography.Text type="secondary">Checking maintenance signals…</Typography.Text>
        ) : triggered.length ? (
          <div className="td-diagnosis-signals">
            {triggered.map((signal) => (
              <div
                key={signal.signal_name}
                className={`td-diagnosis-signal-row td-diagnosis-signal-${signal.severity}`}
              >
                <Tag color={signal.severity === "critical" ? "red" : signal.severity === "warning" ? "gold" : "default"}>
                  {signal.severity}
                </Tag>
                <span className="td-diagnosis-signal-reason">{signal.reason}</span>
              </div>
            ))}
          </div>
        ) : signals.data ? (
          <Typography.Text type="secondary">
            {signals.data.signals.length} signals checked, none triggered
          </Typography.Text>
        ) : null}
      </div>
    </Card>
  );
};
