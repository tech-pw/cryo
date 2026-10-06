import type { AuditEntry, TableStatsRead } from "../../../api/types";
import { MAINTENANCE_OPERATION_ORDER } from "../../../lib/operationOrder";

/** The four operations an operator can launch one at a time from the table page. */
export type RunnableOperation = "optimize" | "optimize_manifests" | "expire_snapshots" | "remove_orphan_files";

export interface OperationInfo {
  op: RunnableOperation;
  title: string;
  verb: string;
  summary: string;
  /** Deletes files; the confirmation asks for an explicit acknowledgement. */
  destructive: boolean;
  consequence: string;
}

export const OPERATION_CATALOG: Record<RunnableOperation, OperationInfo> = {
  optimize: {
    op: "optimize",
    title: "Compact files",
    verb: "Compact",
    summary: "Rewrites small data files into target-sized files, partition by partition.",
    destructive: false,
    consequence:
      "Commits a new snapshot. Old files stay referenced by history until snapshots expire, so storage drops only after expiry."
  },
  optimize_manifests: {
    op: "optimize_manifests",
    title: "Rewrite manifests",
    verb: "Rewrite",
    summary: "Merges metadata manifests so query planning opens fewer files.",
    destructive: false,
    consequence: "Metadata-only. Data files are not touched."
  },
  expire_snapshots: {
    op: "expire_snapshots",
    title: "Expire snapshots",
    verb: "Expire",
    summary: "Drops snapshots older than retention and deletes files only they reference.",
    destructive: true,
    consequence:
      "Time travel to expired snapshots stops working. The retain-last floor is kept. Spark also keeps ids recorded on the Snapshots tab; Trino cannot skip one id."
  },
  remove_orphan_files: {
    op: "remove_orphan_files",
    title: "Remove orphan files",
    verb: "Clean up",
    summary: "Deletes files under the table location that no snapshot references.",
    destructive: true,
    consequence:
      "Permanently deletes unreferenced files older than the orphan age threshold. Skipped automatically when the table shares its storage path with another table."
  }
};

/** Canonical execution order, restricted to operations the page can launch. */
export const RUNNABLE_OPERATIONS: RunnableOperation[] = MAINTENANCE_OPERATION_ORDER.filter(
  (op): op is RunnableOperation => op in OPERATION_CATALOG
);

export interface Deduction {
  label: string;
  reason: string;
  severity: "critical" | "warning" | string;
  deduction: number;
}

export type DeductionRemedy =
  | { kind: "operation"; op: RunnableOperation }
  | { kind: "settings"; href: string; label: string }
  | { kind: "none"; note: string };

/**
 * Which action addresses a persisted health deduction. Labels come from
 * cryo/scanner/health.py::compute_health_score, so matching is on their stable wording.
 */
export const remedyForDeduction = (label: string, tableId: string): DeductionRemedy => {
  const text = label.toLowerCase();
  if (text.includes("snapshot")) return { kind: "operation", op: "expire_snapshots" };
  if (text.includes("metadata") || text.includes("manifest")) return { kind: "operation", op: "optimize_manifests" };
  if (text.includes("orphan")) return { kind: "operation", op: "remove_orphan_files" };
  if (text.includes("small-file") || text.includes("small file") || text.includes("fragment")) {
    return { kind: "operation", op: "optimize" };
  }
  if (text.includes("delete")) return { kind: "operation", op: "optimize" };
  if (text.includes("skew")) return { kind: "operation", op: "optimize" };
  if (text.includes("propert")) {
    return { kind: "settings", href: `/tables/${tableId}?tab=files#files-properties`, label: "Review properties" };
  }
  if (text.includes("stream") || text.includes("velocity")) {
    return { kind: "none", note: "Write pattern. Frequent maintenance keeps it in check." };
  }
  return { kind: "none", note: "No direct maintenance action." };
};

export const readDeductionList = (raw: unknown): Deduction[] => {
  const list = (raw as { health_deductions?: unknown } | null | undefined)?.health_deductions;
  if (!Array.isArray(list)) return [];
  return list
    .filter((item): item is Deduction => {
      if (!item || typeof item !== "object") return false;
      const row = item as Record<string, unknown>;
      return typeof row.label === "string" && typeof row.deduction === "number" && Number.isFinite(row.deduction);
    })
    .sort((a, b) => b.deduction - a.deduction);
};

/** Points recoverable per operation, summed from the deductions it addresses. */
export const pointsByOperation = (deductions: Deduction[], tableId: string): Map<RunnableOperation, number> => {
  const totals = new Map<RunnableOperation, number>();
  for (const item of deductions) {
    const remedy = remedyForDeduction(item.label, tableId);
    if (remedy.kind !== "operation") continue;
    totals.set(remedy.op, (totals.get(remedy.op) ?? 0) + item.deduction);
  }
  return totals;
};

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

export interface OperationEvidence {
  value: string;
  label: string;
  detail: string | null;
}

const count = (value: number) => value.toLocaleString("en-US");

/** The one number that tells an operator whether this operation is worth running now. */
export const operationEvidence = (
  op: RunnableOperation,
  stats: TableStatsRead | null | undefined,
  orphanBytes: number | null | undefined
): OperationEvidence => {
  const raw = (stats?.raw ?? {}) as Record<string, unknown>;
  if (op === "optimize") {
    const small = finite(stats?.small_files_count);
    const total = finite(stats?.total_files);
    const avg = finite(stats?.avg_file_size_mb);
    const target = finite(raw.reduction_target_bytes) ?? finite((stats as Record<string, unknown> | undefined)?.target_file_size_bytes);
    return {
      value: small == null ? "—" : count(small),
      label: "small-file candidates",
      detail:
        total != null && total > 0 && small != null
          ? `${Math.round((small / total) * 100)}% of ${count(total)} files${avg != null ? ` · avg ${avg.toFixed(0)} MiB${target ? ` vs ${Math.round(target / 2 ** 20)} MiB target` : ""}` : ""}`
          : null
    };
  }
  if (op === "optimize_manifests") {
    const manifests = finite(stats?.manifest_count);
    const files = finite(stats?.total_files);
    return {
      value: manifests == null ? "—" : count(manifests),
      label: "manifests",
      detail:
        manifests != null && manifests > 0 && files != null
          ? `~${count(Math.max(1, Math.round(files / manifests)))} data files per manifest`
          : null
    };
  }
  if (op === "expire_snapshots") {
    // raw.snapshot_overhead_gb is a size heuristic (cryo/scanner/cost.py), not a measurement,
    // so the card shows history span and write rate instead of a byte figure.
    const snapshots = finite(stats?.total_snapshots);
    const perDay = finite(raw.commits_per_day);
    const first = typeof raw.first_snapshot_at === "string" ? Date.parse(raw.first_snapshot_at) : Number.NaN;
    const spanDays = Number.isFinite(first) ? Math.max(0, Math.round((Date.now() - first) / 86_400_000)) : null;
    const parts = [
      spanDays != null ? `history spans ${spanDays === 0 ? "<1" : spanDays} day${spanDays === 1 ? "" : "s"}` : null,
      perDay != null && perDay > 0 ? `${perDay >= 10 ? Math.round(perDay) : perDay.toFixed(1)} commits/day` : null
    ].filter(Boolean);
    return {
      value: snapshots == null ? "—" : count(snapshots),
      label: "snapshots retained",
      detail: parts.length ? parts.join(" · ").replace(/^h/, "H") : null
    };
  }
  const orphans = finite(orphanBytes);
  return {
    value: orphans == null ? "—" : orphans === 0 ? "0" : `${(orphans / 2 ** 30).toFixed(orphans >= 2 ** 30 * 10 ? 0 : 2)} GB`,
    label: "known orphan data",
    detail: orphans == null ? "Orphans are found by listing storage during the run" : null
  };
};

export interface LastRun {
  at: string;
  status: string;
  durationMs: number | null;
  scheduled: boolean;
  engine: string | null;
}

export const lastRunByOperation = (entries: AuditEntry[]): Map<string, LastRun> => {
  const latest = new Map<string, LastRun>();
  for (const entry of entries) {
    const action = entry.action?.trim();
    const at = entry.ts ?? entry.created_at;
    if (!action || !at) continue;
    const previous = latest.get(action);
    if (previous && Date.parse(previous.at) >= Date.parse(at)) continue;
    latest.set(action, {
      at,
      status: entry.status ?? "unknown",
      durationMs: finite(entry.duration_ms),
      scheduled: (entry.actor ?? "").startsWith("system:"),
      engine: entry.engine ?? null
    });
  }
  return latest;
};

export const isFailureStatus = (status: string): boolean => /fail|error|cancel/i.test(status);

export const relativeTime = (iso: string, now = Date.now()): string => {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const minutes = Math.round((now - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
};
