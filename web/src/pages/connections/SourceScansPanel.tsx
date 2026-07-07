import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Space, Typography, message } from "antd";
import { Link } from "react-router-dom";
import { listRuns, triggerScan } from "../../api/client";
import type { RunRead } from "../../api/types";
import { ConfirmAction } from "../../components/confirm";
import { StatusTag } from "../../components/status";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
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
      message.success(result.message || `Scan queued: ${result.run_id}`);
      void queryClient.invalidateQueries({ queryKey: runsQueryKey });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const activeRun = (runs.data ?? []).find((run) => SCAN_ACTIVE_STATUSES.has(run.status));

  return (
    <Space orientation="vertical" size={10} className="full-width">
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
        <Space orientation="vertical" size={6} className="full-width">
          {(runs.data as RunRead[]).map((run) => (
            <Space
              key={run.id ?? `${run.status}-${run.kind}-${run.created_at ?? "unknown"}`}
              size={8}
              style={{ width: "100%", justifyContent: "space-between" }}
            >
              <Space size={8}>
                <StatusTag status={run.status} />
                <Link to={`/scans/${run.id}`}>
                  <Typography.Text code className="mono-id">
                    {run.id.slice(0, 8)}
                  </Typography.Text>
                </Link>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {formatTime(run.created_at)}
              </Typography.Text>
            </Space>
          ))}
        </Space>
      ) : (
        <Typography.Text type="secondary">No scans yet for this lakehouse.</Typography.Text>
      )}
    </Space>
  );
};

export default SourceScansPanel;