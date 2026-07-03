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
              size="small"
              rowKey="id"
              loading={runs.isLoading}
              dataSource={runs.data ?? []}
              pagination={{ pageSize: 12 }}
              onRow={(row) => ({ onClick: () => navigate(`/runs/${row.id}`) })}
              columns={[
                {
                  title: "Run",
                  render: (_, row) => (
                    <Link to={`/runs/${row.id}`}>
                      <Typography.Text strong>{row.id.slice(0, 8)}</Typography.Text>
                    </Link>
                  )
                },
                { title: "Status", dataIndex: "status", render: (status) => <StatusTag status={status} /> },
                { title: "Kind", dataIndex: "kind" },
                { title: "Trigger", dataIndex: "trigger", render: (value) => value ?? "-" },
                {
                  title: "Jobs",
                  render: (_, row) => formatNumber(Number(row.summary?.jobs_total ?? row.summary?.job_count ?? 0))
                },
                { title: "Created", dataIndex: "created_at", render: formatTime },
                {
                  title: "",
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
