import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Space, Table, Typography, message } from "antd";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { cancelRun, getRunJobs, listRuns } from "../api/client";
import type { JobRead, RunRead } from "../api/types";
import { ConfirmAction } from "../components/confirm";
import { EmptyState } from "../components/common";
import { OperationsConsolePage } from "../components/layout/OperationsConsolePage";
import { RunConsole } from "../components/RunConsole";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatNumber, formatTime } from "../lib/format";

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const summaryText = (summary: RunRead["summary"], key: string): string | undefined => {
  const value = summary?.[key];
  return typeof value === "string" && value ? value : undefined;
};

const targetLabel = (run: RunRead): string => summaryText(run.summary, "target_label") ?? `${run.kind} run`;

const targetTableId = (run: RunRead): string | undefined => summaryText(run.summary, "target_table_id");

export const Runs = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token } = useAuth();
  const [page, setPage] = useState(1);
  const pageSize = 12;
  const runs = useQuery({
    queryKey: ["runs", token, page],
    queryFn: () => listRuns(api, { limit: pageSize, offset: (page - 1) * pageSize }),
    refetchInterval: 5000
  });
  const selectedRunJobs = useQuery({
    queryKey: ["run-jobs", runId, token],
    queryFn: () => getRunJobs(api, runId!),
    enabled: Boolean(runId),
    retry: 1
  });
  const pageRows = runs.data ?? [];
  const mayHaveMore = pageRows.length === pageSize;
  const cancel = useMutation({
    mutationFn: (id: string) => cancelRun(api, id),
    onSuccess: () => {
      message.success("Run cancelled");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const listPane = (
    <Card title="Recent runs">
            <Table<RunRead>
              className="runs-table"
              size="small"
              rowKey={(row) => row.id ?? `${row.kind}-${row.status}-${row.created_at ?? "run"}`}
              loading={runs.isLoading}
              dataSource={pageRows}
              pagination={{
                current: page,
                pageSize,
                showSizeChanger: false,
                total: mayHaveMore ? page * pageSize + 1 : (page - 1) * pageSize + pageRows.length,
                onChange: setPage
              }}
              onRow={(row) => ({ onClick: () => navigate(`/runs/${row.id}`) })}
              columns={[
                {
                  title: "Run",
                  width: 104,
                  render: (_, row) => (
                    <Link to={`/runs/${row.id}`}>
                      <Typography.Text code className="mono-id">
                        {shortId(row.id)}
                      </Typography.Text>
                    </Link>
                  )
                },
                {
                  title: "Target",
                  width: 260,
                  render: (_, row) => {
                    const tableId = targetTableId(row);
                    const label = targetLabel(row);
                    const content = (
                      <span className="identity-stack">
                        <Typography.Text strong ellipsis={{ tooltip: label }}>
                          {label}
                        </Typography.Text>
                        <Typography.Text type="secondary" className="secondary-line">
                          {tableId ? `table ${shortId(tableId)}` : row.kind}
                        </Typography.Text>
                      </span>
                    );
                    return tableId ? (
                      <Link to={`/tables/${tableId}`} onClick={(event) => event.stopPropagation()}>
                        {content}
                      </Link>
                    ) : (
                      content
                    );
                  }
                },
                {
                  title: "Status",
                  dataIndex: "status",
                  width: 112,
                  render: (status, row) => (
                    <Space orientation="vertical" size={2}>
                      <StatusTag status={status} />
                      {status === "failed" && targetTableId(row) ? (
                        <Link to={`/tables/${targetTableId(row)}`}>Review table</Link>
                      ) : null}
                    </Space>
                  )
                },
                { title: "Kind", dataIndex: "kind", width: 96 },
                { title: "Trigger", dataIndex: "trigger", width: 104, render: (value) => value ?? "-" },
                {
                  title: "Jobs",
                  width: 120,
                  render: (_, row) => {
                    const count = formatNumber(Number(row.summary?.jobs_total ?? row.summary?.job_count ?? 0));
                    const firstJobId =
                      typeof row.summary?.first_job_id === "string" ? row.summary.first_job_id : undefined;
                    return (
                      <Space orientation="vertical" size={0} onClick={(event) => event.stopPropagation()}>
                        <Typography.Text>{count}</Typography.Text>
                        {runId === row.id && selectedRunJobs.data?.length ? (
                          <Link to={`/jobs/${selectedRunJobs.data[0].id}`}>Open job detail</Link>
                        ) : firstJobId ? (
                          <Link to={`/jobs/${firstJobId}`}>Open job detail</Link>
                        ) : runId === row.id ? null : (
                          <Typography.Text type="secondary">Select run</Typography.Text>
                        )}
                      </Space>
                    );
                  }
                },
                { title: "Created", dataIndex: "created_at", width: 148, render: formatTime },
                {
                  title: "",
                  width: 84,
                  render: (_, row) => (
                    <ConfirmAction
                      title="Cancel this run?"
                      description="Queued jobs will be stopped; jobs already running may finish."
                      onConfirm={() => cancel.mutate(row.id)}
                    >
                      <Button
                        size="small"
                        danger
                        disabled={["succeeded", "failed", "cancelled"].includes(row.status)}
                        loading={cancel.isPending}
                        onClick={(event) => event.stopPropagation()}
                      >
                        Cancel
                      </Button>
                    </ConfirmAction>
                  )
                }
              ]}
              scroll={{ x: runId ? 980 : 1160 }}
              locale={{ emptyText: <EmptyState title="No runs yet" description="Ad-hoc and scheduled runs appear here." /> }}
            />
          </Card>
  );
  const detailPane = runId ? (
    <Card
      title={
        <Space>
          <span>Live console</span>
          <Typography.Text type="secondary">{runId.slice(0, 8)}</Typography.Text>
        </Space>
      }
      extra={
        selectedRunJobs.data?.length ? (
          <Space wrap size={4}>
            {selectedRunJobs.data.slice(0, 4).map((job: JobRead) => (
              <Link key={job.id} to={`/jobs/${job.id}`}>
                Job {shortId(job.id)}
              </Link>
            ))}
            {selectedRunJobs.data.length > 4 ? (
              <Typography.Text type="secondary">
                +{selectedRunJobs.data.length - 4} more in table
              </Typography.Text>
            ) : null}
          </Space>
        ) : null
      }
    >
      <RunConsole runId={runId} />
    </Card>
  ) : null;

  return (
    <OperationsConsolePage
      title="Runs"
      subtitle="Queued, active, and completed maintenance runs with live job state. Realized savings will surface here once workers record audit_log.actual_bytes_freed."
      listPane={listPane}
      detailPane={detailPane}
    />
  );
};
