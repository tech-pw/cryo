import type { SkippedTableOps } from "../api/types";

/** One-line summary of what a run left out because an active job already covers it, or null. */
export const describeSkippedOps = (skipped: SkippedTableOps[] | undefined): string | null => {
  if (!skipped?.length) return null;
  const steps = skipped.reduce((total, entry) => total + entry.operations.length, 0);
  const tables = skipped.length;
  return `Skipped ${steps} step${steps === 1 ? "" : "s"} on ${tables} table${tables === 1 ? "" : "s"} already queued or running. Enable "Run even if already queued" to include them.`;
};
