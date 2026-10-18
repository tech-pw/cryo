/** Same cut-offs as the health check's delete-file deduction (cryo/scanner/health.py HEALTH_THRESHOLDS). */
export const DELETE_THRESHOLDS = { pctWarn: 5, pctCrit: 20, countWarn: 100, countCrit: 1_000 } as const;

export type DeleteDebtLevel = "healthy" | "minor" | "major";

export interface DeleteDebt {
  level: DeleteDebtLevel;
  title: string;
  action: string;
}

const DEBT: Record<DeleteDebtLevel, Omit<DeleteDebt, "level">> = {
  healthy: {
    title: "Healthy",
    action: "Readers apply few enough delete files that compaction isn't needed for deletes."
  },
  minor: {
    title: "Minor delete compaction recommended",
    action:
      "On Spark, rewrite position deletes merges the delete files without touching data. Trino has no such procedure; convert deletes rewrites the affected data files instead."
  },
  major: {
    title: "Major rewrite recommended",
    action:
      "Convert deletes rewrites the affected data files with the deletes applied, on Trino and Spark. Readers stop paying the merge cost once it commits."
  }
};

export const deleteDebt = (pct: number | null, files: number | null): DeleteDebt | null => {
  if (pct == null && files == null) return null;
  const share = pct ?? 0;
  const count = files ?? 0;
  const level: DeleteDebtLevel =
    share >= DELETE_THRESHOLDS.pctCrit || count >= DELETE_THRESHOLDS.countCrit
      ? "major"
      : share >= DELETE_THRESHOLDS.pctWarn || count >= DELETE_THRESHOLDS.countWarn
        ? "minor"
        : "healthy";
  return { level, ...DEBT[level] };
};

export interface DeleteScan {
  scanAt: string;
  deleteFiles: number | null;
  deletePct: number | null;
}

const measured = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const deleteSeries = (items: Array<Record<string, unknown>> | undefined): DeleteScan[] =>
  (items ?? [])
    .map((item) =>
      typeof item.scan_at === "string"
        ? { scanAt: item.scan_at, deleteFiles: measured(item.total_delete_files), deletePct: measured(item.delete_file_pct) }
        : null
    )
    .filter((item): item is DeleteScan => item != null)
    .sort((a, b) => a.scanAt.localeCompare(b.scanAt));
