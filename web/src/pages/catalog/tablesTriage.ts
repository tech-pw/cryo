import { useQueries, useQuery } from "@tanstack/react-query";
import { getTablesMaintenanceFunnel, listTables } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { all, leaf } from "../../lib/predicate";
import {
  GRADE_DROP_POINTS,
  SCAN_STALE_HOURS,
  TRIAGE_VIEW_IDS,
  scanStaleCutoff,
  triageViewPredicate,
  type TriageViewId
} from "../../lib/tableFilters";

export interface TriageViewMeta {
  id: TriageViewId;
  label: string;
  /** One line on the card: what the number means and what to do about it. */
  hint: string;
  tone: "danger" | "warning" | "info" | "neutral";
}

export const TRIAGE_VIEWS: Record<TriageViewId, TriageViewMeta> = {
  "needs-work": {
    id: "needs-work",
    label: "Needs work",
    hint: "Small files or deletes past the workflow's thresholds.",
    tone: "neutral"
  },
  failed: {
    id: "failed",
    label: "Failed last run",
    hint: "Latest maintenance job failed or partly failed.",
    tone: "danger"
  },
  "in-progress": {
    id: "in-progress",
    label: "Queued or running",
    hint: "Jobs waiting for engine capacity or running now.",
    tone: "info"
  },
  skipped: {
    id: "skipped",
    label: "Skipped with work",
    hint: "Needed work but the nightly run held it back.",
    tone: "warning"
  },
  "grade-dropped": {
    id: "grade-dropped",
    label: "Health dropped",
    hint: `Score fell ${GRADE_DROP_POINTS}+ points in 7 days.`,
    tone: "warning"
  },
  "scan-issues": {
    id: "scan-issues",
    label: "Scan issues",
    hint: `Failing, never scanned, or stale for ${SCAN_STALE_HOURS}h+.`,
    tone: "danger"
  },
  savings: {
    id: "savings",
    label: "Savings",
    hint: "Tables with an estimated monthly saving.",
    tone: "neutral"
  }
};

export const TRIAGE_CARD_IDS: TriageViewId[] = [
  "failed",
  "in-progress",
  "skipped",
  "grade-dropped",
  "scan-issues"
];

const activeOnly = leaf("is_active", "=", true);

/** Exact fleet-wide counts (limit:1, read `.total`) for every quick view, shared by cards and chips. */
export const useTriageCounts = (): {
  counts: Partial<Record<TriageViewId, number>>;
  activeTotal: number | null;
  loading: boolean;
} => {
  const { api, token } = useAuth();
  const cutoff = scanStaleCutoff();
  const results = useQueries({
    queries: [null, ...TRIAGE_VIEW_IDS].map((view) => ({
      queryKey: ["tables-triage-count", token, view, view === "scan-issues" ? cutoff : null],
      queryFn: () =>
        listTables(api, {
          limit: 1,
          offset: 0,
          predicate: all([activeOnly, view ? triageViewPredicate(view, cutoff) : null])
        }),
      staleTime: 60_000,
      refetchInterval: 60_000
    }))
  });
  const counts: Partial<Record<TriageViewId, number>> = {};
  TRIAGE_VIEW_IDS.forEach((view, index) => {
    const total = results[index + 1]?.data?.total;
    if (typeof total === "number") counts[view] = total;
  });
  return {
    counts,
    activeTotal: results[0]?.data?.total ?? null,
    loading: results.some((result) => result.isLoading)
  };
};

/** Count of active tables whose stats are older than the stale cutoff (scanner lag, not failures). */
export const useStaleScanCount = () => {
  const { api, token } = useAuth();
  const cutoff = scanStaleCutoff();
  return useQuery({
    queryKey: ["tables-stale-scan-count", token, cutoff],
    queryFn: () =>
      listTables(api, {
        limit: 1,
        offset: 0,
        predicate: all([activeOnly, leaf("scan_at", "<", cutoff)])
      }),
    select: (page) => page.total,
    staleTime: 60_000
  });
};

export const useTablesFunnel = () => {
  const { api, token } = useAuth();
  return useQuery({
    queryKey: ["tables-funnel", token],
    queryFn: ({ signal }) => getTablesMaintenanceFunnel(api, signal),
    staleTime: 60_000,
    refetchInterval: 120_000
  });
};
