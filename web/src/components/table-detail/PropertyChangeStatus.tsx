import { Loader2 } from "lucide-react";
import { Link } from "react-router-dom";

import type { PropertyChangeJob } from "../../hooks/usePropertyChangeJob";

const STATUS_TEXT: Record<string, string> = {
  queued: "waiting for an engine slot",
  deferred: "waiting for the table or an engine slot",
  admitted: "starting",
  running: "running"
};

/** One line while a queued property change (#560) runs: what, where it is, and its run. */
export const PropertyChangeStatus = ({ job, status }: { job: PropertyChangeJob | null; status: string | null }) => {
  if (!job) return null;
  const state = STATUS_TEXT[(status ?? "queued").toLowerCase()] ?? status ?? "queued";
  return (
    <div
      role="status"
      data-testid="property-change-status"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-[var(--radius-default)] border border-border bg-muted/40 p-3 text-sm"
    >
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      <span className="font-medium">Applying…</span>
      <span className="text-muted-foreground">
        {job.label} ({state}). The result lands in the audit log and this table's activity.
      </span>
      {job.runId ? (
        <Link to={`/runs/${job.runId}`} className="text-primary underline-offset-2 hover:underline">
          View run
        </Link>
      ) : null}
    </div>
  );
};
