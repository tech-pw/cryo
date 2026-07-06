import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Drawer, Space, Table, Typography } from "antd";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getRun, getRunJobs } from "../api/client";
import type { JobRead, UUID } from "../api/types";
import { useRunLiveEvents } from "../hooks/useRunLiveEvents";
import { StatStrip } from "./common";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";
import { StatusTag } from "./status";

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const tableRef = (job: JobRead) =>
  job.schema_name && job.table_name ? `${job.schema_name}.${job.table_name}` : job.table_id;

export const RunConsole = ({ runId }: { runId: UUID }) => {
  const { api, token } = useAuth();
  const [logJob, setLogJob] = useState<JobRead | null>(null);
  const { messages, snapshot, wsOpen, wsExhausted, reconnectAttempt, manualReconnect } =
    useRunLiveEvents(runId, token);

  const run = useQuery({
    queryKey: ["run", runId, token],
    queryFn: () => getRun(api, runId),
    refetchInterval: wsOpen ? false : 5000
  });
  const jobs = useQuery({
    queryKey: ["run-jobs", runId, token],
    queryFn: () => getRunJobs(api, runId),
    retry: 1,
    refetchInterval: wsOpen ? false : 5000
  });

  const runData = snapshot?.run ?? run.data;
  const jobRows = useMemo(
    () => snapshot?.jobs ?? jobs.data ?? [],
    [snapshot?.jobs, jobs.data]
  );
  const concurrency = useMemo(() => {
    const active = jobRows.filter((job) => ["admitted", "running"].includes(job.status));
    const queued = jobRows.filter((job) => job.status === "queued").length;
    const done = jobRows.filter((job) => job.status === "succeeded").length;
    const failed = jobRows.filter((job) => job.status === "failed").length;
    const byEngine = active.reduce<Record<string, number>>((acc, job) => {
      const key = job.destination_engine ?? job.destination_name ?? "engine";
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    }, {});
    return {
      queued,
      done,
      failed,
      engines: Object.entries(byEngine)
    };
  }, [jobRows]);

  const jobLogMessages = useMemo(() => {
    if (!logJob) {
      return [];
    }
    return messages.filter((item) => item.job_id === logJob.id);
  }, [logJob, messages]);

  return (
    <Space orientation="vertical" size={12} className="full-width">
      {!wsOpen ? (
        <Alert
          type={wsExhausted ? "warning" : "info"}
          showIcon
          title={
            wsExhausted
              ? "Live socket disconnected after multiple retries; polling run state every 5 seconds."
              : reconnectAttempt > 0
                ? `Live socket unavailable; reconnecting (attempt ${reconnectAttempt}/${8})…`
                : "Live socket unavailable; polling run state every 5 seconds."
          }
          action={
            <Button size="small" onClick={manualReconnect}>
              Reconnect
            </Button>
          }
        />
      ) : null}
      <div className="console-summary">
        <Typography.Text strong>{runData?.kind ?? "Run"}</Typography.Text>
        <StatusTag status={runData?.status} />
        <Typography.Text type="secondary">Created {formatTime(runData?.created_at)}</Typography.Text>
      </div>
      <StatStrip
        items={[
          ...(concurrency.engines.length
            ? concurrency.engines.map(([engine, count]) => ({ label: `Active ${engine}`, value: count }))
            : [{ label: "Active jobs", value: 0 }]),
          { label: "Queued", value: concurrency.queued },
          { label: "Succeeded", value: concurrency.done },
          { label: "Failed", value: concurrency.failed }
        ]}
      />
      <Table<JobRead>
        className="runs-table"
        size="small"
        rowKey="id"
        dataSource={jobRows}
        loading={jobs.isLoading && !snapshot?.jobs}
        pagination={false}
        columns={[
          {
            title: "Table",
            width: 280,
            render: (_, row) => (
              <Link to={`/tables/${row.table_id}`} className="identity-stack">
                <Typography.Text strong ellipsis={{ tooltip: tableRef(row) }}>
                  {tableRef(row)}
                </Typography.Text>
                <Typography.Text type="secondary" className="secondary-line">
                  <Link to={`/jobs/${row.id}`}>View job</Link>
                  {" · "}
                  job <span className="mono-id">{shortId(row.id)}</span> · table{" "}
                  <span className="mono-id">{shortId(row.table_id)}</span>
                </Typography.Text>
              </Link>
            )
          },
          {
            title: "Status",
            dataIndex: "status",
            width: 112,
            render: (status) => <StatusTag status={status} />
          },
          { title: "Mode", dataIndex: "mode", width: 90 },
          {
            title: "Engine",
            width: 120,
            render: (_, row) => row.destination_name ?? row.destination_engine ?? "-"
          },
          { title: "Replica", dataIndex: "replica", width: 96, render: (value) => value ?? "-" },
          { title: "Started", dataIndex: "started_at", width: 148, render: formatTime },
          {
            title: "",
            key: "log",
            width: 72,
            render: (_, row) => (
              <Button size="small" onClick={() => setLogJob(row)}>
                Logs
              </Button>
            )
          }
        ]}
        scroll={{ x: 920 }}
      />
      <div className="log-list">
        <div className="log-list-header">Live log</div>
        {messages.length === 0 ? (
          <div className="log-list-empty">No live events yet.</div>
        ) : (
          <ul className="log-list-ul">
            {messages.map((item, idx) => (
              <li key={idx} className="log-list-item">
                <Typography.Text code>{formatTime(item.ts)}</Typography.Text>
                <Typography.Text type={item.level === "error" ? "danger" : undefined}>
                  {item.message ?? item.status ?? item.event}
                </Typography.Text>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Drawer
        title={logJob ? `Job ${shortId(logJob.id)}` : "Job logs"}
        open={Boolean(logJob)}
        onClose={() => setLogJob(null)}
        size={560}
        extra={
          logJob ? (
            <Link to={`/jobs/${logJob.id}`}>
              <Button size="small">Full job detail</Button>
            </Link>
          ) : null
        }
      >
        {logJob ? (
          <Space orientation="vertical" size={12} className="full-width">
            <Space wrap>
              <StatusTag status={logJob.status} />
              {logJob.error ? (
                <Typography.Text type="danger">{logJob.error}</Typography.Text>
              ) : null}
            </Space>
            <Typography.Text type="secondary">
              Engine {logJob.destination_name ?? logJob.destination_engine ?? "—"} · started{" "}
              {formatTime(logJob.started_at)}
            </Typography.Text>
            {jobLogMessages.length === 0 ? (
              <Typography.Text type="secondary">No live events for this job yet.</Typography.Text>
            ) : (
              <ul className="log-list-ul">
                {jobLogMessages.map((item, idx) => (
                  <li key={idx} className="log-list-item">
                    <Typography.Text code>{formatTime(item.ts)}</Typography.Text>
                    <Typography.Text type={item.level === "error" ? "danger" : undefined}>
                      {item.message ?? item.status ?? item.event}
                    </Typography.Text>
                  </li>
                ))}
              </ul>
            )}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
};
