import { useQuery } from "@tanstack/react-query";
import { getTableSignals } from "../api/client";
import type { TableStatsRead, UUID } from "../api/types";
import { QueryErrorAlert } from "./common";
import { HealthScoreBreakdown, type ScoreFactor } from "./HealthScoreBreakdown";
import { healthScoreColor } from "./status";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Card, CardContent } from "./ui/card";
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

const ALERT_VARIANT: Record<DiagnosisLevel, "error" | "warning" | "success"> = {
  critical: "error",
  warning: "warning",
  ok: "success"
};

const severityBadge = (severity: string) => {
  if (severity === "critical") return "danger" as const;
  if (severity === "warning") return "warning" as const;
  return "secondary" as const;
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
      style={{ "--tile-accent": accentColor } as React.CSSProperties}
    >
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">Diagnosis</span>
        {signals.data ? (
          <span className="td-chart-card-subtitle">Checked {formatTime(signals.data.checked_at)}</span>
        ) : null}
      </div>
      <CardContent className="td-chart-card-content p-0 pt-0">
        <Alert
          variant={ALERT_VARIANT[verdict.level]}
          title={verdict.message}
          className="mb-3"
        />

        <div className="td-diagnosis-section-title">Score factors</div>
        <div className="td-diagnosis-score-bars">
          <HealthScoreBreakdown factors={factors} />
        </div>

        <div className="td-diagnosis-section-title">Active signals</div>
        <QueryErrorAlert error={signals.error} title="Failed to load table signals" />
        {signals.isLoading ? (
          <p className="text-sm text-muted-foreground">Checking maintenance signals…</p>
        ) : triggered.length ? (
          <div className="td-diagnosis-signals">
            {triggered.map((signal, idx) => (
              <div
                key={signal.signal_name ?? `signal-${idx}`}
                className={`td-diagnosis-signal-row td-diagnosis-signal-${signal.severity}`}
              >
                <Badge variant={severityBadge(signal.severity)}>{signal.severity}</Badge>
                <span className="td-diagnosis-signal-reason">{signal.reason}</span>
              </div>
            ))}
          </div>
        ) : signals.data ? (
          <p className="text-sm text-muted-foreground">
            {signals.data.signals.length} signals checked, none triggered
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
};
