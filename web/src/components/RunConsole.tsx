import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Drawer, List, Space, Table, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getRun, getRunJobs, wsRunUrl } from "../api/client";
import type { JobRead, UUID } from "../api/types";
import { StatStrip } from "./common";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";
import { StatusTag } from "./status";

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const tableRef = (job: JobRead) =>
  job.schema_name && job.table_name ? `${job.schema_name}.${job.table_name}` : job.table_id;

interface LiveMessage {
  ts?: string;
  level?: string;
  message?: string;
  job_id?: string;
  [key: string]: unknown;
}

export const RunConsole = ({ runId }: { runId: UUID }) => {
  const { api, token } = useAuth();
  const [messages, setMessages] = useState<LiveMessage[]>([]);
  const [wsOpen, setWsOpen] = useState(false);
  const [logJob, setLogJob] = useState<JobRead | null>(null);

  const run = useQuery({
    queryKey: ["run", runId, token],
    queryFn: () => getRun(api, runId),
    refetchInterval: 5000
  });
  const jobs = useQuery({
    queryKey: ["run-jobs", runId, token],
    queryFn: () => getRunJobs(api, runId),
    retry: 1,
    refetchInterval: wsOpen ? false : 5000
  });

  useEffect(() => {
    const socket = new WebSocket(wsRunUrl(runId, token));
    socket.onopen = () => setWsOpen(true);
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as LiveMessage;
        setMessages((current) => [payload, ...current].slice(0, 200));
      } catch {
        setMessages((current) => [{ message: event.data, ts: new Date().toISOString() }, ...current].slice(0, 200));
      }
    };
    socket.onerror = () => setWsOpen(false);
    socket.onclose = () => setWsOpen(false);
    return () => socket.close();
  }, [runId, token]);

  const jobRows = useMemo(() => jobs.data ?? [], [jobs.data]);
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

  return (
    <Space direction="vertical" size={12} className="full-width">
      {!wsOpen ? (
        <Alert
          type="info"
          showIcon
          message="Live socket unavailable; polling run state every 5 seconds."
        />
      ) : null}
      <div className="console-summary">
        <Typography.Text strong>{run.data?.kind ?? "Run"}</Typography.Text>
        <StatusTag status={run.data?.status} />
        <Typography.Text type="secondary">Created {formatTime(run.data?.created_at)}</Typography.Text>
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
        loading={jobs.isLoading}
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
      <List
        className="log-list"
        size="small"
        dataSource={messages}
        locale={{ emptyText: "No live events yet." }}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text code>{formatTime(item.ts)}</Typography.Text>
            <Typography.Text>{item.message ?? JSON.stringify(item)}</Typography.Text>
          </List.Item>
        )}
      />
      <Drawer title="Job detail" open={Boolean(logJob)} onClose={() => setLogJob(null)} width={560}>
        <pre className="json-block">{JSON.stringify(logJob, null, 2)}</pre>
      </Drawer>
    </Space>
  );
};
