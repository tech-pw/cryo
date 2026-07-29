import type { RunRead } from "../api/types";

export const shortRunId = (value?: string | null) => value?.slice(0, 8) ?? "-";

export const isScanRun = (run?: RunRead | null) =>
  run?.kind === "scan" || run?.trigger?.includes("scan") === true;

export const summaryText = (summary: RunRead["summary"], key: string): string | undefined => {
  const value = summary?.[key];
  return typeof value === "string" && value ? value : undefined;
};

export const summaryNumber = (summary: RunRead["summary"], key: string): number => {
  const value = summary?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
};

export const targetLabel = (run: RunRead): string =>
  summaryText(run.summary, "target_label") ?? (isScanRun(run) ? "Scan run" : `${run.kind} run`);

export const targetTableId = (run: RunRead): string | undefined => summaryText(run.summary, "target_table_id");

export const runDurationMs = (run?: RunRead | null): number | null => {
  if (!run?.created_at) return null;
  const start = Date.parse(run.created_at);
  const end = run.finished_at ? Date.parse(run.finished_at) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, end - start);
};

export const formatDuration = (ms?: number | null): string => {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return rem ? `${minutes}m ${rem}s` : `${minutes}m`;
};

export const ACTIVE_RUN_STATUSES = new Set(["accepted", "queued", "running", "admitted"]);

export const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "cancelled", "partial_failed"]);

export type RunKindCategory = "scan" | "maintenance" | "other";

/** Canonical run kind labels per `lld.md` §RunKind taxonomy. */
export const formatRunKind = (kind?: string | null): { label: string; category: RunKindCategory } => {
  const normalized = (kind ?? "").trim().toLowerCase();
  if (normalized === "scan") return { label: "Scan", category: "scan" };
  if (normalized === "adhoc") return { label: "Ad hoc", category: "maintenance" };
  if (normalized === "scheduled") return { label: "Scheduled", category: "maintenance" };
  if (!normalized) return { label: "Other", category: "other" };
  return { label: kind ?? "Other", category: "other" };
};

export const isMaintenanceRunKind = (kind?: string | null): boolean =>
  formatRunKind(kind).category === "maintenance";
