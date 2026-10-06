import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { listRuns, triggerScan } from "../../api/client";
import type { RunRead } from "../../api/types";
import { ConfirmAction } from "../../components/confirm";
import { StatusTag } from "../../components/status";
import { Button } from "../../components/ui/button";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { toastScanResult } from "../../lib/operations";
import { SCAN_ACTIVE_STATUSES } from "./constants";

interface SourceScansPanelProps {
  sourceId: string;
}

/** Per-lakehouse scan trigger + recent history, surfaced right on the connection itself --
 * mirrors how OpenMetadata attaches ingestion runs to a service instead of a separate page. */
export const SourceScansPanel = ({ sourceId }: SourceScansPanelProps) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const runsQueryKey = ["source-scan-runs", sourceId];

  const runs = useQuery({
    queryKey: runsQueryKey,
    queryFn: () => listRuns(api, { kind: "scan", source_id: sourceId, limit: 5 }),
    refetchInterval: (query) => {
      const active = (query.state.data ?? []).some((run) => SCAN_ACTIVE_STATUSES.has(run.status));
      return active ? 5000 : 15000;
    }
  });

  const scan = useMutation({
    mutationFn: () =>
      triggerScan(api, {
        scope: "source",
        source_id: sourceId,
        full_scan: true,
        trigger: "web-connections"
      }),
    onSuccess: (result) => {
      toastScanResult(result);
      void queryClient.invalidateQueries({ queryKey: runsQueryKey });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const activeRun = (runs.data ?? []).find((run) => SCAN_ACTIVE_STATUSES.has(run.status));

  return (
    <div className="flex w-full flex-col gap-2.5">
      <ConfirmAction
        title="Scan this lakehouse now?"
        description="Queues a scan of just this lakehouse -- other connected lakehouses aren't touched."
        onConfirm={() => scan.mutate()}
      >
        <Button loading={scan.isPending} disabled={Boolean(activeRun)}>
          {activeRun ? "Scan in progress..." : "Scan this lakehouse now"}
        </Button>
      </ConfirmAction>
      {runs.isLoading ? null : (runs.data ?? []).length ? (
        <div className="flex w-full flex-col gap-1.5">
          {(runs.data as RunRead[]).map((run) => (
            <div
              key={run.id ?? `${run.status}-${run.kind}-${run.created_at ?? "unknown"}`}
              className="flex w-full items-center justify-between gap-2"
            >
              <div className="flex items-center gap-2">
                <StatusTag status={run.status} />
                <Link to={`/scans/${run.id}`}>
                  <code className="mono-id text-xs">{run.id.slice(0, 8)}</code>
                </Link>
              </div>
              <span className="text-xs text-muted-foreground">{formatTime(run.created_at)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No scans yet for this lakehouse.</p>
      )}
    </div>
  );
};

export default SourceScansPanel;
