export type MeasurementState = "complete" | "partial" | "unmeasured" | "no_actions";

export interface ImpactDay {
  report_date: string;
  measurement_state: string;
  bytes_freed: number;
  actions_succeeded: number;
  actions_with_bytes_evidence: number;
  actions_without_bytes_evidence: number;
  tables_touched: number;
  optimize_files_in: number;
  optimize_files_out: number;
  jobs_succeeded: number;
  jobs_failed: number;
  infra_cost_usd?: number | null;
  raw?: {
    bytes_freed_by_engine?: Record<string, number | { bytes?: number | null }> | null;
  } | null;
}

export const impactBytesLabel = (day: ImpactDay): string => {
  if (day.measurement_state === "unmeasured") return "Not measured";
  if (day.measurement_state === "no_actions") return "No actions";
  if (day.measurement_state === "partial") {
    return `Partial ${day.actions_with_bytes_evidence} of ${day.actions_succeeded}`;
  }
  if (day.measurement_state !== "complete") return "Unknown";
  return `${(day.bytes_freed / 1024 ** 3).toFixed(2)} GB`;
};

/** Chart value. Unmeasured and no-action days are gaps, never zero bars. */
export const impactChartGb = (day: ImpactDay): number | null => {
  if (day.measurement_state === "unmeasured" || day.measurement_state === "no_actions") return null;
  if (day.measurement_state === "partial" || day.measurement_state === "complete") {
    return day.bytes_freed / 1024 ** 3;
  }
  return null;
};

export const fullyMeasuredSummary = (days: ImpactDay[]): string => {
  const measured = days.filter((day) => day.measurement_state === "complete").length;
  return `${measured} of ${days.length} days fully measured`;
};

export const engineBytesLabel = (raw: ImpactDay["raw"]): string => {
  const byEngine = raw?.bytes_freed_by_engine;
  if (!byEngine || Object.keys(byEngine).length === 0) return "Unknown";
  return Object.entries(byEngine)
    .map(([engine, entry]) => {
      const bytes = typeof entry === "number" ? entry : Number(entry?.bytes);
      if (!Number.isFinite(bytes)) return `${engine} Unknown`;
      return `${engine} ${(bytes / 1024 ** 3).toFixed(2)} GB`;
    })
    .join(", ");
};
