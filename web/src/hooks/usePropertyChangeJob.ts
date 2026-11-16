import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { getJob } from "../api/client";
import { useAuth } from "../lib/auth";

/**
 * Follows a queued table-property change (#560). On an Iceberg v3 table the API returns 202
 * with a job id instead of applying inline, because the change runs as engine DDL (a Spark
 * driver pod takes a minute or two). This polls the job until it finishes.
 */

export interface PropertyChangeJob {
  jobId: string;
  runId?: string | null;
  /** What is being applied, for the "Applying…" line and the result toast. */
  label: string;
  /** Lets a caller tie the job to one row (e.g. a property key). */
  key?: string;
}

export type PropertyChangeOutcome = "succeeded" | "failed";

const TERMINAL = new Set(["succeeded", "failed", "partial", "skipped", "cancelled"]);
const POLL_MS = 3000;

export const isTerminalJobStatus = (status: string | null | undefined) =>
  TERMINAL.has((status ?? "").toLowerCase());

export const propertyChangeOutcome = (status: string): PropertyChangeOutcome =>
  status.toLowerCase() === "succeeded" ? "succeeded" : "failed";

export const usePropertyChangeJob = (
  onDone: (job: PropertyChangeJob, outcome: PropertyChangeOutcome, error: string | null) => void
) => {
  const { api } = useAuth();
  const [pending, setPending] = useState<PropertyChangeJob | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const job = useQuery({
    queryKey: ["property-change-job", pending?.jobId],
    queryFn: () => getJob(api, pending?.jobId ?? ""),
    enabled: Boolean(pending?.jobId),
    refetchInterval: (query) => (isTerminalJobStatus(query.state.data?.status) ? false : POLL_MS)
  });

  const status = job.data?.status;
  const error = job.data?.error ?? null;
  useEffect(() => {
    if (!pending || !status || !isTerminalJobStatus(status)) return;
    onDoneRef.current(pending, propertyChangeOutcome(status), error);
    setPending(null);
  }, [pending, status, error]);

  return {
    pending,
    status: pending ? (status ?? "queued") : null,
    follow: setPending
  };
};
