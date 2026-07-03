import { useQuery } from "@tanstack/react-query";
import { Alert, Descriptions, List, Progress, Space, Typography } from "antd";
import { getRun } from "../api/client";
import type { JsonObject, RunRead, UUID } from "../api/types";
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
  const run = useQuery({
    queryKey: ["scan-run", runId, token],
    queryFn: () => getRun(api, runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !ACTIVE_STATUSES.has(status) ? false : 5000;
    }
  });

  const summary = run.data?.summary;
  const assigned = numberValue(summary, "assigned");
  const completed =
    numberValue(summary, "scanned") + numberValue(summary, "skipped") + numberValue(summary, "failed");
  const percent = assigned > 0 ? Math.min(100, Math.round((completed / assigned) * 100)) : 0;
  const queueError = stringValue(summary, "queue_error");
  const lastError = stringValue(summary, "last_error");
  const failures = failedTables(run.data);

  return (
    <Space direction="vertical" size={12} className="full-width">
      {run.data?.status === "accepted" || run.data?.status === "queued" ? (
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
        <StatusTag status={run.data?.status} />
        <Typography.Text type="secondary">Created {formatTime(run.data?.created_at)}</Typography.Text>
      </div>

      <Progress percent={percent} status={run.data?.status === "failed" ? "exception" : "active"} />
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
        <Descriptions.Item label="Trigger">{run.data?.trigger ?? "-"}</Descriptions.Item>
        <Descriptions.Item label="Current table">
          {stringValue(summary, "current_table") ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="Finished">{formatTime(run.data?.finished_at)}</Descriptions.Item>
      </Descriptions>

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
