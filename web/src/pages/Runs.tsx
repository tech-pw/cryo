import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Col, Row, Space, Table, Typography, message } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import { cancelRun, listRuns } from "../api/client";
import type { RunRead } from "../api/types";
import { EmptyState, PageHeader } from "../components/common";
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
  const runs = useQuery({
    queryKey: ["runs", token],
    queryFn: () => listRuns(api),
    refetchInterval: 5000
  });
  const cancel = useMutation({
    mutationFn: (id: string) => cancelRun(api, id),
    onSuccess: () => {
      message.success("Run cancelled");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <div>
      <PageHeader
        title="Runs"
        subtitle="Queued, active, and completed maintenance runs with live job state. Realized savings will surface here once workers record audit_log.actual_bytes_freed."
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={runId ? 11 : 24}>
          <Card title="Recent runs">
            <Table<RunRead>
              className="runs-table"
              size="small"
              rowKey="id"
              loading={runs.isLoading}
              dataSource={runs.data ?? []}
              pagination={{ pageSize: 12 }}
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
                  render: (status) => <StatusTag status={status} />
                },
                { title: "Kind", dataIndex: "kind", width: 96 },
                { title: "Trigger", dataIndex: "trigger", width: 104, render: (value) => value ?? "-" },
                {
                  title: "Jobs",
                  width: 84,
                  render: (_, row) => formatNumber(Number(row.summary?.jobs_total ?? row.summary?.job_count ?? 0))
                },
                { title: "Created", dataIndex: "created_at", width: 148, render: formatTime },
                {
                  title: "",
                  width: 84,
                  render: (_, row) => (
                    <Button
                      size="small"
                      danger
                      disabled={["succeeded", "failed", "cancelled"].includes(row.status)}
                      loading={cancel.isPending}
                      onClick={(event) => {
                        event.stopPropagation();
                        cancel.mutate(row.id);
                      }}
                    >
                      Cancel
                    </Button>
                  )
                }
              ]}
              scroll={{ x: runId ? 980 : 1160 }}
              locale={{ emptyText: <EmptyState title="No runs yet" description="Ad-hoc and scheduled runs appear here." /> }}
            />
          </Card>
        </Col>
        {runId ? (
          <Col xs={24} xl={13}>
            <Card
              title={
                <Space>
                  <span>Live console</span>
                  <Typography.Text type="secondary">{runId.slice(0, 8)}</Typography.Text>
                </Space>
              }
            >
              <RunConsole runId={runId} />
            </Card>
          </Col>
        ) : null}
      </Row>
    </div>
  );
};
