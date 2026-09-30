export interface HealthDeduction {
  label: string;
  deduction: number;
  severity: string;
  reason: string;
}

export interface TrendPoint {
  at: string;
  value: number | null;
  grade: string | null;
}

export type TrendAnswer =
  | { kind: "none" }
  | { kind: "single" }
  | { kind: "unchanged"; since: string }
  | { kind: "down" | "up"; points: number; since: string };

export interface MaintenanceMarker {
  id: string;
  at: string;
  kind: "maintenance";
  label: string;
}

const MINUS = "\u2212";

const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  error: 0,
  warning: 1,
  info: 2
};

export function integerScore(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) return null;
  return Math.round(value);
}

export function gradeLetter(grade: unknown, score: number | null): string | null {
  if (typeof grade === "string" && grade.trim()) return grade.trim();
  if (score == null) return null;
  if (score >= 90) return "A";
  if (score >= 75) return "B";
  if (score >= 60) return "C";
  if (score >= 40) return "D";
  return "F";
}

export function isTableScanned(stats: { scan_at?: string | null } | null | undefined): boolean {
  return validTimestamp(stats?.scan_at) != null;
}

export function readDeductions(raw: unknown): HealthDeduction[] | null {
  if (!raw || typeof raw !== "object") return null;
  const value = (raw as { health_deductions?: unknown }).health_deductions;
  if (!Array.isArray(value)) return null;
  if (value.length === 0) return [];
  const parsed = value.flatMap((item) => {
    const deduction = parseDeduction(item);
    return deduction ? [deduction] : [];
  });
  return parsed.length > 0 ? parsed : null;
}

export function sortDeductions(items: readonly HealthDeduction[]): HealthDeduction[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => right.item.deduction - left.item.deduction || left.index - right.index)
    .map(({ item }) => item);
}

export function formatDeductionLine(item: HealthDeduction): string {
  return `${MINUS}${item.deduction} ${item.label}: ${item.reason}`;
}

export function formatScoreSum(deductions: readonly HealthDeduction[], persisted: number | null): string {
  const sum = deductions.reduce((total, item) => total + item.deduction, 0);
  const computed = 100 - sum;
  const arithmetic = `100 ${MINUS} ${sum} = ${computed}`;
  if (persisted == null) return `${arithmetic} (computed); persisted score not recorded`;
  if (persisted !== computed) return `${arithmetic} (computed); persisted score ${persisted}`;
  return arithmetic;
}

export function diagnosticSignals<T extends { triggered: boolean; signal_name: string; severity: string }>(
  signals: readonly T[],
  deductions: readonly HealthDeduction[]
): T[] {
  const labels = new Set(deductions.map((item) => normalizeKey(item.label)));
  return signals
    .filter((signal) => {
      if (!signal.triggered) return false;
      const name = normalizeKey(signal.signal_name);
      const spaced = normalizeKey(signal.signal_name.replace(/_/g, " "));
      return !labels.has(name) && !labels.has(spaced);
    })
    .map((item, index) => ({ item, index }))
    .sort((left, right) => severityRank(left.item.severity) - severityRank(right.item.severity) || left.index - right.index)
    .map(({ item }) => item);
}

export function topBySeverity<T extends { severity: string }>(items: readonly T[]): T | null {
  if (items.length === 0) return null;
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => severityRank(left.item.severity) - severityRank(right.item.severity) || left.index - right.index)[0]
    .item;
}

export function healthTrendPoints(
  points: readonly { timestamp?: unknown; score?: unknown; grade?: unknown }[]
): TrendPoint[] {
  const plotted: TrendPoint[] = [];
  for (const point of points) {
    const at = validTimestamp(point.timestamp);
    if (!at) continue;
    plotted.push({
      at,
      value: typeof point.score === "number" && Number.isFinite(point.score) && point.score >= 0 && point.score <= 100 ? point.score : null,
      grade: typeof point.grade === "string" && point.grade.trim() ? point.grade : null
    });
  }
  return plotted.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

export function scoreTrendAnswer(points: readonly { at: string; value: number | null }[]): TrendAnswer {
  const measured = points.filter(
    (point): point is { at: string; value: number } => typeof point.value === "number" && Number.isFinite(point.value)
  ).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  if (measured.length === 0) return { kind: "none" };
  if (measured.length === 1) return { kind: "single" };
  const first = measured[0];
  const last = measured[measured.length - 1];
  const delta = Math.round(last.value - first.value);
  if (delta === 0) return { kind: "unchanged", since: first.at };
  return { kind: delta < 0 ? "down" : "up", points: Math.abs(delta), since: first.at };
}

export function markersFromAudit(
  rows: readonly { id?: unknown; ts?: unknown; created_at?: unknown; action?: unknown }[]
): MaintenanceMarker[] {
  const events: MaintenanceMarker[] = [];
  for (const [index, row] of rows.entries()) {
    const at = validTimestamp(row.ts) ?? validTimestamp(row.created_at);
    if (!at) continue;
    const id = typeof row.id === "string" || typeof row.id === "number" ? String(row.id) : `${at}-${index}`;
    const label = typeof row.action === "string" && row.action.trim() ? row.action.trim() : "Maintenance";
    events.push({ id, at, kind: "maintenance", label });
  }
  return events;
}

export function receiptRunHref(row: { job_id?: unknown; run_id?: unknown; detail?: unknown }): string | null {
  const detail = row.detail && typeof row.detail === "object" ? (row.detail as Record<string, unknown>) : null;
  const runId = idField(row.run_id) ?? (detail ? idField(detail.run_id) : null);
  const jobId = idField(row.job_id) ?? (detail ? idField(detail.job_id) : null);
  if (runId) return `/runs/${runId}`;
  if (jobId) return `/runs/jobs/${jobId}`;
  return null;
}

function parseDeduction(value: unknown): HealthDeduction | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.label !== "string" || !row.label.trim()) return null;
  if (typeof row.deduction !== "number" || !Number.isFinite(row.deduction)) return null;
  return {
    label: row.label,
    deduction: row.deduction,
    severity: typeof row.severity === "string" ? row.severity : "",
    reason: typeof row.reason === "string" ? row.reason : ""
  };
}

function normalizeKey(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function severityRank(severity: string): number {
  return SEVERITY_RANK[severity.toLowerCase()] ?? 3;
}

function validTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  if (Number.isNaN(new Date(value).getTime())) return null;
  return value;
}

function idField(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}
