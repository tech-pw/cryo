import type { AuditEntry, TableRunRead } from "../../../api/types";

export type CellTone = "success" | "failed" | "attention" | "running" | "queued" | "skipped" | "none";

/** Grid rows, in execution order. Delete rewrites and stats only appear when a run used them. */
export const GRID_OPERATIONS = [
  "convert_deletes",
  "rewrite_position_deletes",
  "optimize",
  "compute_statistics",
  "optimize_manifests",
  "expire_snapshots",
  "remove_orphan_files"
] as const;

export const CORE_GRID_OPERATIONS = new Set(["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]);

export const OPERATION_SHORT: Record<string, string> = {
  convert_deletes: "Convert deletes",
  rewrite_position_deletes: "Rewrite deletes",
  optimize: "Compact",
  compute_statistics: "Statistics",
  optimize_manifests: "Manifests",
  expire_snapshots: "Expire snapshots",
  remove_orphan_files: "Orphan files"
};

export const toneFor = (status: string | null | undefined): CellTone => {
  const key = (status ?? "").trim().toLowerCase();
  if (!key) return "none";
  if (["succeeded", "success", "ok", "completed", "done"].includes(key)) return "success";
  if (key === "partial" || key === "partial_failed") return "attention";
  if (key.includes("fail") || key.includes("error") || key === "timeout") return "failed";
  // "admitted"/"waiting" mean waiting for engine capacity in Cryo, not executing.
  if (["running", "dispatched", "in_progress"].includes(key)) return "running";
  if (["queued", "scheduled", "waiting", "admitted", "deferred", "pending", "accepted"].includes(key)) return "queued";
  if (["skipped", "cancelled", "canceled", "noop", "not_needed"].includes(key)) return "skipped";
  return "none";
};

export const isActiveTone = (tone: CellTone) => tone === "running" || tone === "queued";

export interface OpCell {
  op: string;
  tone: CellTone;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  durationMs: number | null;
  /** The statement or message the engine recorded (audit detail). */
  detail: string | null;
  engine: string | null;
}

export interface RunColumn {
  key: string;
  run: TableRunRead;
  tone: CellTone;
  manual: boolean;
  startedAt: string | null;
  durationMs: number | null;
  cells: Map<string, OpCell>;
  receipts: AuditEntry[];
}

const ms = (start?: string | null, end?: string | null): number | null => {
  if (!start || !end) return null;
  const value = Date.parse(end) - Date.parse(start);
  return Number.isFinite(value) && value >= 0 ? value : null;
};

const detailText = (detail: AuditEntry["detail"]): string | null => {
  if (detail == null || detail === "") return null;
  return typeof detail === "string" ? detail : JSON.stringify(detail);
};

/** Scheduled runs come from cron/scheduler triggers; everything else was started by a person or API. */
export const isManualTrigger = (trigger: string | null | undefined): boolean => {
  const key = (trigger ?? "").toLowerCase();
  return !(key === "cron" || key.startsWith("schedul") || key.startsWith("nightly") || key.startsWith("system"));
};

/**
 * One column per job on this table, oldest → newest (Airflow grid order). Per-operation state
 * comes from the run's recorded steps; older runs recorded none, so their audit receipts
 * (same job_id) fill the cells instead. Nothing is invented for operations with neither.
 */
export const buildRunColumns = (runs: TableRunRead[], audit: AuditEntry[]): RunColumn[] => {
  const receiptsByJob = new Map<string, AuditEntry[]>();
  for (const entry of audit) {
    const jobId = (entry as AuditEntry & { job_id?: string | null }).job_id;
    if (!jobId) continue;
    receiptsByJob.set(jobId, [...(receiptsByJob.get(jobId) ?? []), entry]);
  }
  return [...runs]
    .sort((a, b) => Date.parse(a.started_at ?? "") - Date.parse(b.started_at ?? "") || 0)
    .map((run) => {
      const receipts = (receiptsByJob.get(run.job_id) ?? []).sort(
        (a, b) => Date.parse(a.ts ?? a.created_at ?? "") - Date.parse(b.ts ?? b.created_at ?? "")
      );
      const cells = new Map<string, OpCell>();
      for (const step of run.steps ?? []) {
        cells.set(step.operation_type, {
          op: step.operation_type,
          tone: toneFor(step.status),
          status: step.status,
          startedAt: step.started_at ?? null,
          finishedAt: step.finished_at ?? null,
          durationMs: ms(step.started_at, step.finished_at),
          detail: null,
          engine: null
        });
      }
      for (const receipt of receipts) {
        const op = receipt.action ?? "";
        if (!op) continue;
        const existing = cells.get(op);
        cells.set(op, {
          op,
          tone: existing?.tone ?? toneFor(receipt.status),
          status: existing?.status ?? receipt.status ?? "unknown",
          startedAt: existing?.startedAt ?? null,
          finishedAt: existing?.finishedAt ?? receipt.ts ?? receipt.created_at ?? null,
          durationMs: existing?.durationMs ?? (typeof receipt.duration_ms === "number" ? receipt.duration_ms : null),
          detail: detailText(receipt.detail),
          engine: receipt.engine ?? null
        });
      }
      const tone = toneFor(run.status);
      return {
        key: run.job_id,
        run,
        tone,
        manual: isManualTrigger(run.trigger),
        startedAt: run.started_at ?? null,
        durationMs:
          typeof run.duration_seconds === "number" && Number.isFinite(run.duration_seconds)
            ? run.duration_seconds * 1000
            : ms(run.started_at, run.finished_at),
        cells,
        receipts
      };
    });
};

/** Grid rows: the four core operations always, extras only if some shown run used them. */
export const gridRows = (columns: RunColumn[]): string[] =>
  GRID_OPERATIONS.filter((op) => CORE_GRID_OPERATIONS.has(op) || columns.some((column) => column.cells.has(op)));

export interface StatChange {
  label: string;
  before: number | null;
  after: number | null;
  /** True when lower is better (files, small files). */
  lowerIsBetter: boolean;
  unit?: string;
}

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/**
 * Before-stats show whenever the run captured them. After-stats come from the next scan, so a
 * running, queued, or not-yet-rescanned run has none; those rows keep `after: null` rather than
 * hiding the before snapshot. Rows the before snapshot didn't record at all are dropped.
 */
export const statChanges = (run: TableRunRead): StatChange[] => {
  const before = run.before_stats;
  const after = run.after_stats ?? null;
  if (!before) return [];
  const rows: StatChange[] = [
    { label: "Health score", before: num(before.overall_score), after: num(after?.overall_score), lowerIsBetter: false },
    { label: "Data files", before: num(before.total_files), after: num(after?.total_files), lowerIsBetter: true },
    { label: "Small files", before: num(before.small_files_count), after: num(after?.small_files_count), lowerIsBetter: true },
    { label: "Delete files", before: num(before.total_delete_files), after: num(after?.total_delete_files), lowerIsBetter: true },
    { label: "Size", before: num(before.total_size_gb), after: num(after?.total_size_gb), lowerIsBetter: true, unit: "GB" }
  ];
  return rows.filter((row) => row.before != null || row.after != null);
};

export const gradeChange = (run: TableRunRead): { before: string | null; after: string | null } | null => {
  const before = run.before_stats?.grade ?? null;
  const after = run.after_stats?.grade ?? null;
  return before || after ? { before, after } : null;
};

export const formatMs = (value: number | null): string => {
  if (value == null) return "—";
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
};
