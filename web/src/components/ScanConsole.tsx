import { useQuery } from "@tanstack/react-query";
import { Alert, Descriptions, List, Progress, Space, Typography } from "antd";
import { useMemo } from "react";
import { getRun } from "../api/client";
import type { JsonObject, RunRead, UUID } from "../api/types";
import { useRunLiveEvents } from "../hooks/useRunLiveEvents";
import { useAuth } from "../lib/auth";
import { formatNumber, formatTime } from "../lib/format";
import { JsonHelpButton, StatStrip } from "./common";
import { StatusTag } from "./status";

const ACTIVE_STATUSES = new Set(["accepted", "queued", "running"]);

const numberValue = (summary: JsonObject | null | undefined, key: string): number => {
  const value = summary?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
};

const stringValue = (summary: JsonObject | null | undefined, key: string): string | undefined => {
  const value = summary?.[key];
  return typeof value === "string" && value ? value : undefined;
};

const failedTables = (run?: RunRead): Array<{ table?: string; error?: string; ts?: string }> => {
  const value = run?.summary?.failed_tables;
  return Array.isArray(value) ? (value as Array<{ table?: string; error?: string; ts?: string }>) : [];
};

export const ScanConsole = ({ runId }: { runId: UUID }) => {
  const { api, token } = useAuth();
  const { messages, snapshot, wsOpen } = useRunLiveEvents(runId, token);
  const run = useQuery({
    queryKey: ["scan-run", runId, token],
    queryFn: () => getRun(api, runId),
    refetchInterval: (query) => {
      if (wsOpen) {
        return false;
      }
      const status = query.state.data?.status;
      return status && !ACTIVE_STATUSES.has(status) ? false : 5000;
    }
  });

  const runData = snapshot?.run ?? run.data;
  const summary = runData?.summary;
  const assigned = numberValue(summary, "assigned");
  const completed =
    numberValue(summary, "scanned") + numberValue(summary, "skipped") + numberValue(summary, "failed");
  const percent = assigned > 0 ? Math.min(100, Math.round((completed / assigned) * 100)) : 0;
  const queueError = stringValue(summary, "queue_error");
  const lastError = stringValue(summary, "last_error");
  const failures = failedTables(runData);
  const liveMessages = useMemo(
    () =>
      messages.filter(
        (item) => item.type === "log_line" || item.type === "scan_progress"
      ),
    [messages]
  );

  return (
    <Space direction="vertical" size={12} className="full-width">
      {runData?.status === "accepted" || runData?.status === "queued" ? (
        <Alert
          type="info"
          showIcon
          message="Waiting for a scanner worker"
          description="The scan run exists, but a scanner worker has not started this run yet."
        />
      ) : null}
      {queueError ? <Alert type="error" showIcon message="Queue error" description={queueError} /> : null}
      {lastError ? <Alert type="warning" showIcon message="Latest table error" description={lastError} /> : null}

      <div className="console-summary">
        <Typography.Text strong>{summary?.target_label?.toString() ?? "Scan run"}</Typography.Text>
        <StatusTag status={runData?.status} />
        <Typography.Text type="secondary">Created {formatTime(runData?.created_at)}</Typography.Text>
      </div>

      <Progress percent={percent} status={runData?.status === "failed" ? "exception" : "active"} />
      <StatStrip
        items={[
          { label: "Discovered", value: formatNumber(numberValue(summary, "discovered")) },
          { label: "Assigned", value: formatNumber(assigned) },
          { label: "Scanned", value: formatNumber(numberValue(summary, "scanned")) },
          { label: "Skipped", value: formatNumber(numberValue(summary, "skipped")) },
          { label: "Failed", value: formatNumber(numberValue(summary, "failed")) },
          { label: "Chunks running", value: formatNumber(numberValue(summary, "chunks_running")) }
        ]}
      />

      <Descriptions size="small" column={2}>
        <Descriptions.Item label="Scope">{summary?.scope?.toString() ?? "-"}</Descriptions.Item>
        <Descriptions.Item label="Trigger">{runData?.trigger ?? "-"}</Descriptions.Item>
        <Descriptions.Item label="Current table">
          {stringValue(summary, "current_table") ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="Finished">{formatTime(runData?.finished_at)}</Descriptions.Item>
      </Descriptions>

      <List
        className="log-list"
        size="small"
        header="Live scan log"
        dataSource={liveMessages}
        locale={{ emptyText: "No live scan events yet." }}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text code>{formatTime(item.ts)}</Typography.Text>
            <Typography.Text type={item.level === "error" ? "danger" : undefined}>
              {item.message ?? item.event}
            </Typography.Text>
          </List.Item>
        )}
      />

      <List
        size="small"
        header="Failed tables"
        dataSource={failures}
        locale={{ emptyText: "No table errors recorded." }}
        renderItem={(item) => (
          <List.Item>
            <Space direction="vertical" size={2}>
              <Typography.Text strong>{item.table ?? "unknown table"}</Typography.Text>
              <Typography.Text type="secondary">{item.error ?? "scan failed"}</Typography.Text>
              {item.ts ? <Typography.Text type="secondary">{formatTime(item.ts)}</Typography.Text> : null}
            </Space>
          </List.Item>
        )}
      />

      {summary ? <JsonHelpButton value={summary} /> : null}
    </Space>
  );
};
