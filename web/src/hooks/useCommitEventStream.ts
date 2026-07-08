import { useEffect } from "react";

import { getSmartMaintenanceOverview } from "../api/client";
import { useAuth } from "../lib/auth";

type CommitEventStreamOptions = {
  enabled?: boolean;
  pollMs?: number;
  onTick?: () => void;
};

/**
 * Best-effort commit-event stream hook.
 *
 * The backend stream is Redis-based and does not expose a dedicated websocket yet;
 * this hook polls smart-maintenance overview as a lightweight heartbeat so screens
 * refresh quickly after commit-triggered evaluations.
 */
export function useCommitEventStream(options: CommitEventStreamOptions = {}) {
  const { api } = useAuth();
  const { enabled = true, pollMs = 15_000, onTick } = options;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const run = async () => {
      try {
        await getSmartMaintenanceOverview(api);
        if (!cancelled) onTick?.();
      } catch {
        // no-op: heartbeat polling should never break host pages
      }
    };
    void run();
    const id = window.setInterval(() => void run(), Math.max(5_000, pollMs));
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [api, enabled, onTick, pollMs]);
}
