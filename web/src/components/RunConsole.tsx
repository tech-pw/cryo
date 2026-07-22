import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Drawer, Space, Table, Tag, Typography, message } from "antd";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { forceAdmitJob, getRun, getRunJobs } from "../api/client";
import type { JobRead, UUID } from "../api/types";
import { useRunLiveEvents, isTerminalRunStatus } from "../hooks/useRunLiveEvents";
import { ConfirmAction } from "./confirm";
import { StatStrip } from "./common";
import { useAuth } from "../lib/auth";
import { formatEngineLabel } from "../lib/engines";
import { formatTime } from "../lib/format";
import { LogList } from "./LogList";
import { StatusTag } from "./status";

const STUCK_STATUSES = new Set(["queued", "deferred"]);

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const tableRef = (job: JobRead) => {
  const planRef = job.plan?.table_ref;
  if (typeof planRef === "string" && planRef) return planRef;
  return job.schema_name && job.table_name ? `${job.schema_name}.${job.table_name}` : job.table_id;
};

export const RunConsole = ({ runId }: { runId: UUID }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const [logJob, setLogJob] = useState<JobRead | null>(null);
  const { messages, snapshot, wsOpen, wsExhausted, runTerminal, reconnectAttempt, manualReconnect } =
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
  const forceAdmit = useMutation({
    mutationFn: (jobId: string) => forceAdmitJob(api, jobId),
    onSuccess: () => {
      message.success("Bypassed scheduling gates; job re-queued for the next admission wave.");
      void queryClient.invalidateQueries({ queryKey: ["run-jobs", runId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const runData = snapshot?.run ?? run.data;
  const runFinished = runTerminal || isTerminalRunStatus(runData?.status);
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
      {!wsOpen && !runFinished ? (
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
      <div className="console-summary" role="status" aria-live="polite" aria-atomic="true">
        <Typography.Text strong>{runData?.kind ?? "Run"}</Typography.Text>
        <StatusTag status={runData?.status} />
        <Typography.Text type="secondary">Created {formatTime(runData?.created_at)}</Typography.Text>
      </div>
      <StatStrip
        items={[
          ...(concurrency.engines.length
            ? concurrency.engines.map(([engine, count]) => ({
                label: `Active ${formatEngineLabel(engine)}`,
                value: count
              }))
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
            width: 160,
            render: (_, row) => (
              <Space orientation="vertical" size={2}>
                <Space size={4}>
                  <StatusTag status={row.status} />
                  {row.force_admit ? <Tag color="purple">Forced</Tag> : null}
                </Space>
                {row.error && STUCK_STATUSES.has(row.status) ? (
                  <Typography.Text type="secondary" className="secondary-line" ellipsis={{ tooltip: row.error }}>
                    {row.error}
                  </Typography.Text>
                ) : null}
              </Space>
            )
          },
          { title: "Mode", dataIndex: "mode", width: 90 },
          {
            title: "Engine",
            width: 120,
            render: (_, row) => {
              if (row.destination_name) {
                return row.destination_engine
                  ? `${row.destination_name} · ${formatEngineLabel(row.destination_engine)}`
                  : row.destination_name;
              }
              return formatEngineLabel(row.destination_engine) || "-";
            }
          },
          { title: "Replica", dataIndex: "replica", width: 96, render: (value) => value ?? "-" },
          { title: "Started", dataIndex: "started_at", width: 148, render: formatTime },
          {
            title: "",
            key: "actions",
            width: 168,
            render: (_, row) => (
              <Space size={4}>
                {STUCK_STATUSES.has(row.status) && !row.force_admit ? (
                  <ConfirmAction
                    title="Bypass scheduling gates?"
                    description="Skips cooldown, benefit/moop score, and offpeak checks for this job. Table locks and PII/ABAC safety gates still apply."
                    okText="Force run"
                    okButtonProps={{ danger: false }}
                    onConfirm={() => forceAdmit.mutate(row.id)}
                  >
                    <Button size="small" loading={forceAdmit.isPending}>
                      Force run
                    </Button>
                  </ConfirmAction>
                ) : null}
                <Button size="small" onClick={() => setLogJob(row)}>
                  Logs
                </Button>
              </Space>
            )
          }
        ]}
        scroll={{ x: 1080 }}
      />
      <LogList
        title="Live log"
        items={messages}
        emptyText="No live events yet."
        wsOpen={wsOpen}
        wsExhausted={wsExhausted}
        runTerminal={runFinished}
        reconnectAttempt={reconnectAttempt}
        onManualReconnect={manualReconnect}
        formatTime={formatTime}
      />
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
              Engine{" "}
              {logJob.destination_name
                ? logJob.destination_engine
                  ? `${logJob.destination_name} · ${formatEngineLabel(logJob.destination_engine)}`
                  : logJob.destination_name
                : formatEngineLabel(logJob.destination_engine) || "—"}{" "}
              · started {formatTime(logJob.started_at)}
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
