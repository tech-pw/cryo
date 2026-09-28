import type { RunRead } from "../api/types";
import { formatBytesGb, formatGb, formatNumber } from "./format";

export const UNKNOWN = "Unknown";

export const OVER_BUDGET_TOOLTIP = "Eligible but not chosen: run's compute budget was spent";

type Summary = RunRead["summary"];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const hasKey = (record: Record<string, unknown>, key: string) =>
  Object.prototype.hasOwnProperty.call(record, key);

const finiteNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const isBatchPipeline = (summary: Summary): summary is Record<string, unknown> =>
  isRecord(summary) && summary.pipeline === "batch";

export const optionalNumber = (summary: Summary, key: string): number | null => {
  if (!isRecord(summary) || !hasKey(summary, key)) return null;
  return finiteNumber(summary[key]);
};

const nestedNumber = (value: unknown, key: string): number | null => {
  if (!isRecord(value) || !hasKey(value, key)) return null;
  return finiteNumber(value[key]);
};

const formatCount = (value: number | null): string => (value == null ? UNKNOWN : formatNumber(value));

const formatQuantity = (value: number): string => {
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(3).replace(/\.?0+$/, "");
};

export const percentChange = (before: number | null, after: number | null): string => {
  if (before == null || after == null || before === 0) return UNKNOWN;
  const pct = Math.round(((after - before) / before) * 100);
  return `${pct > 0 ? "+" : ""}${pct}%`;
};

export const formatTransition = (
  before: number | null,
  after: number | null,
  formatValue: (value: number) => string
): string => {
  const beforeText = before == null ? UNKNOWN : formatValue(before);
  const afterText = after == null ? UNKNOWN : formatValue(after);
  return `${beforeText} → ${afterText} (${percentChange(before, after)})`;
};

export interface BatchCounts {
  selected: string;
  succeeded: string;
  failed: string;
  skipped: string;
  overBudget: string;
}

export const batchCounts = (summary: Summary): BatchCounts => ({
  selected: formatCount(optionalNumber(summary, "selected")),
  succeeded: formatCount(optionalNumber(summary, "succeeded")),
  failed: formatCount(optionalNumber(summary, "failed")),
  skipped: formatCount(optionalNumber(summary, "skipped")),
  overBudget: formatCount(optionalNumber(summary, "over_budget"))
});

export const filesTransition = (summary: Summary): string =>
  formatTransition(optionalNumber(summary, "files_before"), optionalNumber(summary, "files_after"), (value) =>
    formatNumber(value)
  );

export const sizeTransition = (summary: Summary): string =>
  formatTransition(optionalNumber(summary, "size_before_gb"), optionalNumber(summary, "size_after_gb"), (value) =>
    formatGb(value)
  );

export const rewrittenBytes = (summary: Summary): string => {
  const bytes = optionalNumber(summary, "bytes_rewritten");
  return bytes == null ? UNKNOWN : formatBytesGb(bytes);
};

const formatVcpu = (cpuMilli: number): string => {
  const vcpu = cpuMilli / 1000;
  return Number.isInteger(vcpu) ? String(vcpu) : vcpu.toFixed(1);
};

const formatMemory = (memMib: number): string => {
  if (memMib >= 1024) {
    const gib = memMib / 1024;
    const text = Number.isInteger(gib) ? String(gib) : gib.toFixed(1);
    return `${text} GiB`;
  }
  return `${formatNumber(memMib)} MiB`;
};

export interface LaneRow {
  id: string;
  lane: string;
  vcpu: string;
  memory: string;
  tables: string;
}

export const laneRows = (summary: Summary): LaneRow[] | null => {
  if (!isRecord(summary) || !hasKey(summary, "lanes") || !Array.isArray(summary.lanes)) return null;
  return summary.lanes.map((lane, index) => {
    if (!isRecord(lane)) {
      return { id: `lane-${index}`, lane: UNKNOWN, vcpu: UNKNOWN, memory: UNKNOWN, tables: UNKNOWN };
    }
    const laneNumber = hasKey(lane, "lane") ? finiteNumber(lane.lane) : null;
    const cpu = hasKey(lane, "cpu_milli") ? finiteNumber(lane.cpu_milli) : null;
    const memory = hasKey(lane, "mem_mib") ? finiteNumber(lane.mem_mib) : null;
    const tables = hasKey(lane, "tables") ? finiteNumber(lane.tables) : null;
    return {
      id: `lane-${laneNumber ?? index}-${index}`,
      lane: laneNumber == null ? UNKNOWN : formatNumber(laneNumber),
      vcpu: cpu == null ? UNKNOWN : formatVcpu(cpu),
      memory: memory == null ? UNKNOWN : formatMemory(memory),
      tables: tables == null ? UNKNOWN : formatNumber(tables)
    };
  });
};

export interface FailureRow {
  id: string;
  table: string;
  error: string;
}

export const failureRows = (summary: Summary): FailureRow[] | null => {
  if (!isRecord(summary) || !hasKey(summary, "failures") || !Array.isArray(summary.failures)) return null;
  return summary.failures.map((failure, index) => {
    if (!isRecord(failure)) {
      return { id: `failure-${index}`, table: UNKNOWN, error: UNKNOWN };
    }
    const table = typeof failure.table === "string" && failure.table ? failure.table : UNKNOWN;
    const error = typeof failure.error === "string" && failure.error ? failure.error : UNKNOWN;
    return { id: `failure-${index}`, table, error };
  });
};

export const budgetLine = (summary: Summary): string => {
  const autocomp = isRecord(summary) && hasKey(summary, "autocomp") ? summary.autocomp : undefined;
  const chosen = nestedNumber(autocomp, "chosen_gbhr");
  const budget = nestedNumber(autocomp, "budget_gbhr");
  const chosenText = chosen == null ? UNKNOWN : formatQuantity(chosen);
  const budgetText = budget == null ? UNKNOWN : formatQuantity(budget);
  return `${chosenText} of ${budgetText} GB·h`;
};
