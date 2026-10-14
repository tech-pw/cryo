import { useCallback, useEffect, useRef, useState } from "react";
import { discoverSourceStorage } from "../../api/client";
import type { ApiOptions, DiscoveredBucket } from "../../api/client";
import type { UUID } from "../../api/types";

export type StorageDiscoveryStatus = "idle" | "running" | "done" | "error";

export interface StorageDiscoveryState {
  status: StorageDiscoveryStatus;
  /** Accumulated across slices, biggest first -- see mergeBuckets. */
  buckets: DiscoveredBucket[];
  scanned: number;
  total: number;
  /** Non-fatal problems from slices that were skipped, so a partial walk can say so. */
  warnings: string[];
  error: string | null;
}

/**
 * Fold one slice's buckets into the running totals.
 *
 * Counts are additive because each slice covers a distinct set of databases, so the same
 * bucket legitimately reappears with more tables. Access status is per-bucket rather than
 * per-slice, so a later slice must not downgrade a known answer back to "unknown".
 */
export const mergeBuckets = (
  current: Map<string, DiscoveredBucket>,
  incoming: DiscoveredBucket[]
): Map<string, DiscoveredBucket> => {
  const next = new Map(current);
  for (const bucket of incoming) {
    const existing = next.get(bucket.name);
    if (!existing) {
      next.set(bucket.name, { ...bucket });
      continue;
    }
    next.set(bucket.name, {
      ...existing,
      tables: existing.tables + bucket.tables,
      schemas: existing.schemas + bucket.schemas,
      access: existing.access === "unknown" ? bucket.access : existing.access,
      access_detail: existing.access_detail ?? bucket.access_detail
    });
  }
  return next;
};

const rank = (buckets: Map<string, DiscoveredBucket>): DiscoveredBucket[] =>
  [...buckets.values()].sort((a, b) => b.tables - a.tables || a.name.localeCompare(b.name));

const INITIAL: StorageDiscoveryState = {
  status: "idle",
  buckets: [],
  scanned: 0,
  total: 0,
  warnings: [],
  error: null
};

/**
 * Walks the catalog's databases a slice at a time, accumulating distinct buckets so they
 * render as they're found. The underlying read is table-level (a database's own location
 * is only a default most of its tables don't use), which on a real metastore is ~14k
 * tables and well over a minute -- far too long to hold everything back until the end.
 *
 * Cancels cleanly: `runIdRef` invalidates in-flight slices when the walk is restarted or
 * the drawer closes, so a stale response can't resurrect a previous catalog's buckets.
 */
export const useStorageDiscovery = (api: ApiOptions) => {
  const [state, setState] = useState<StorageDiscoveryState>(INITIAL);
  const runIdRef = useRef(0);

  const reset = useCallback(() => {
    runIdRef.current += 1;
    setState(INITIAL);
  }, []);

  // Abandon any in-flight walk when the component goes away.
  useEffect(() => () => void (runIdRef.current += 1), []);

  const start = useCallback(
    async (body: { type: string; config: Record<string, unknown>; source_id?: UUID }) => {
      const runId = ++runIdRef.current;
      setState({ ...INITIAL, status: "running" });

      let found = new Map<string, DiscoveredBucket>();
      const warnings: string[] = [];
      let offset = 0;

      for (;;) {
        let page;
        try {
          page = await discoverSourceStorage(api, { ...body, offset, limit: 25 });
        } catch (err) {
          if (runId !== runIdRef.current) return;
          setState((prev) => ({
            ...prev,
            status: prev.buckets.length > 0 ? "done" : "error",
            error: err instanceof Error ? err.message : "Bucket discovery failed"
          }));
          return;
        }
        if (runId !== runIdRef.current) return;

        if (!page.ok) {
          setState((prev) => ({
            ...prev,
            status: prev.buckets.length > 0 ? "done" : "error",
            error: page.error ?? "Bucket discovery failed"
          }));
          return;
        }

        found = mergeBuckets(found, page.buckets);
        if (page.error) warnings.push(page.error);

        const scanned = page.scanned;
        const total = page.total;
        setState({
          status: scanned >= total ? "done" : "running",
          buckets: rank(found),
          scanned,
          total,
          warnings: [...warnings],
          error: null
        });

        // No forward progress (empty slice at the end, or a server that didn't advance)
        // would otherwise spin forever.
        if (scanned >= total || scanned <= offset) return;
        offset = scanned;
      }
    },
    [api]
  );

  return { ...state, start, reset };
};
