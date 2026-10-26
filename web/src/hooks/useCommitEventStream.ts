import { useEffect, useRef } from "react";

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
 *
 * `onTick` is read through a ref, so callers may pass an inline callback: a new
 * callback identity on every render must not restart the heartbeat (that turned the
 * 15 s poll into back-to-back requests). The effect depends only on `api`,
 * `enabled` and `pollMs`. Polling pauses while the tab is hidden and resumes with
 * one immediate tick when it becomes visible again.
 */
export function useCommitEventStream(options: CommitEventStreamOptions = {}) {
  const { api } = useAuth();
  const { enabled = true, pollMs = 15_000, onTick } = options;
  const onTickRef = useRef(onTick);

  useEffect(() => {
    onTickRef.current = onTick;
  }, [onTick]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
    const run = async () => {
      if (hidden()) return;
      try {
        await getSmartMaintenanceOverview(api);
        if (!cancelled) onTickRef.current?.();
      } catch {
        // no-op: heartbeat polling should never break host pages
      }
    };
    const onVisibility = () => {
      if (!hidden()) void run();
    };
    void run();
    const id = window.setInterval(() => void run(), Math.max(5_000, pollMs));
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [api, enabled, pollMs]);
}
