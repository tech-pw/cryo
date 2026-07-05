import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Col, Input, Row, Space, Table, Tag, Typography, message } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  approvalAction,
  getCostForecast,
  getTableLineage,
  listFleetAnomalies,
  listPendingApprovals,
  listTierPolicies
} from "../api/client";
import { ConfirmAction } from "../components/confirm";
import { EmptyState, PageHeader, QueryErrorAlert } from "../components/common";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatCurrency, formatTime } from "../lib/format";

export const Governance = () => {
  const { api, subject, role } = useAuth();
  const queryClient = useQueryClient();
  const canApprove = role === "admin" || role === "operator";
  const [lineageTableId, setLineageTableId] = useState("");

  const approvals = useQuery({
    queryKey: ["pending-approvals", api.token],
    queryFn: () => listPendingApprovals(api),
    refetchInterval: 30_000
  });
  const anomalies = useQuery({
    queryKey: ["fleet-anomalies", api.token],
    queryFn: () => listFleetAnomalies(api),
    refetchInterval: 60_000
  });
  const forecast = useQuery({
    queryKey: ["cost-forecast", api.token],
    queryFn: () => getCostForecast(api)
  });
  const tierPolicies = useQuery({
    queryKey: ["tier-policies", api.token],
    queryFn: () => listTierPolicies(api)
  });
  const lineage = useQuery({
    queryKey: ["table-lineage", api.token, lineageTableId],
    queryFn: () => getTableLineage(api, lineageTableId),
    enabled: lineageTableId.length > 8
  });

  const actOnApproval = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) =>
      approvalAction(api, id, { approved, approver: subject || "ui-user" }),
    onSuccess: (_, variables) => {
      message.success(variables.approved ? "Request approved" : "Request rejected");
      void queryClient.invalidateQueries({ queryKey: ["pending-approvals"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <div>
      <PageHeader
        title="Governance"
        subtitle="Approvals, anomalies, FinOps forecast, tier policies, and table lineage surfaced from the advanced API."
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}>
          <Card title="Pending approvals" extra={<Tag>{approvals.data?.length ?? 0}</Tag>}>
            <QueryErrorAlert error={approvals.error} title="Failed to load approvals" />
            <Table
              size="small"
              rowKey="id"
              loading={approvals.isLoading}
              dataSource={approvals.data ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: <EmptyState title="No pending approvals" description="Destructive ops requiring approval appear here." />
              }}
              columns={[
                {
                  title: "Table",
                  render: (_, row) => (
                    <Link to={`/tables/${row.table_id}`}>{row.table_name ?? row.table_id}</Link>
                  )
                },
                { title: "Operation", dataIndex: "operation_type", width: 140 },
                { title: "Requested by", dataIndex: "requested_by", width: 120 },
                { title: "Created", dataIndex: "created_at", width: 140, render: formatTime },
                {
                  title: "Actions",
                  width: 180,
                  render: (_, row) =>
                    canApprove ? (
                      <Space size={4}>
                        <ConfirmAction
                          title="Approve this maintenance request?"
                          description="The approved operation will proceed when scheduled."
                          okText="Approve"
                          okButtonProps={{ danger: false, type: "primary" }}
                          onConfirm={() => actOnApproval.mutate({ id: row.id, approved: true })}
                        >
                          <Button size="small" type="primary" loading={actOnApproval.isPending}>
                            Approve
                          </Button>
                        </ConfirmAction>
                        <ConfirmAction
                          title="Reject this maintenance request?"
                          description="The request will be marked rejected and will not run."
                          onConfirm={() => actOnApproval.mutate({ id: row.id, approved: false })}
                        >
                          <Button size="small" danger loading={actOnApproval.isPending}>
                            Reject
                          </Button>
                        </ConfirmAction>
                      </Space>
                    ) : (
                      <Typography.Text type="secondary">—</Typography.Text>
                    )
                }
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} xl={12}>
          <Card title="Fleet anomalies">
            <QueryErrorAlert error={anomalies.error} title="Failed to load anomalies" />
            <Table
              size="small"
              rowKey={(row) => `${row.table_id}-${row.metric_name}-${row.detected_at}`}
              loading={anomalies.isLoading}
              dataSource={anomalies.data ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: <EmptyState title="No anomalies detected" description="Statistical drift alerts appear here after enough scan history." />
              }}
              columns={[
                {
                  title: "Table",
                  render: (_, row) => (
                    <Link to={`/tables/${row.table_id}`}>{row.table_name}</Link>
                  )
                },
                { title: "Metric", dataIndex: "metric_name", width: 120 },
                {
                  title: "Severity",
                  dataIndex: "severity",
                  width: 100,
                  render: (value: string) => <Tag color={value === "critical" ? "red" : "gold"}>{value}</Tag>
                },
                { title: "σ", dataIndex: "deviation_sigma", width: 64, align: "right" as const }
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title="FinOps forecast">
            <QueryErrorAlert error={forecast.error} title="Failed to load cost forecast" />
            {forecast.data ? (
              <Space direction="vertical" size={8} className="full-width">
                <Typography.Text type="secondary">Forecast month: {forecast.data.forecast_month}</Typography.Text>
                <Typography.Title level={3} style={{ margin: 0 }}>
                  {formatCurrency(forecast.data.total_forecast_usd)}
                </Typography.Title>
                <Space wrap>
                  <Tag>Storage {formatCurrency(forecast.data.forecast_storage_cost_usd)}</Tag>
                  <Tag>Compute {formatCurrency(forecast.data.forecast_compute_cost_usd)}</Tag>
                  <Tag>Maintenance {formatCurrency(forecast.data.forecast_maintenance_cost_usd)}</Tag>
                </Space>
                <StatusTag status={forecast.data.alert_level} />
              </Space>
            ) : forecast.isLoading ? (
              <Typography.Text type="secondary">Loading forecast…</Typography.Text>
            ) : (
              <EmptyState title="No forecast data" />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title="Tier policies">
            <QueryErrorAlert error={tierPolicies.error} title="Failed to load tier policies" />
            <Table
              size="small"
              rowKey="id"
              loading={tierPolicies.isLoading}
              dataSource={tierPolicies.data ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: <EmptyState title="No tier policies" description="Create policies via the phase8 API or Helm seed config." />
              }}
              columns={[
                { title: "Name", dataIndex: "name" },
                { title: "Storage class", dataIndex: "storage_class", width: 140 },
                { title: "Min age (days)", dataIndex: "min_age_days", width: 120, align: "right" as const },
                {
                  title: "Enabled",
                  dataIndex: "enabled",
                  width: 90,
                  render: (enabled: boolean) => (enabled ? "Yes" : "No")
                }
              ]}
            />
          </Card>
        </Col>

        <Col xs={24}>
          <Card title="Table lineage">
            <Space direction="vertical" size={12} className="full-width">
              <Input.Search
                placeholder="Table UUID for lineage graph"
                enterButton="Load lineage"
                value={lineageTableId}
                onChange={(event) => setLineageTableId(event.target.value.trim())}
                onSearch={(value) => setLineageTableId(value.trim())}
                style={{ maxWidth: 480 }}
              />
              <QueryErrorAlert error={lineage.error} title="Failed to load lineage" />
              {lineage.data ? (
                <Row gutter={[12, 12]}>
                  <Col xs={24} md={12}>
                    <Typography.Title level={5}>Upstream</Typography.Title>
                    <Table
                      size="small"
                      rowKey="table_id"
                      pagination={false}
                      dataSource={lineage.data.upstream}
                      columns={[
                        { title: "Table", dataIndex: "table_name" },
                        { title: "Edge", dataIndex: "node_type", width: 100 }
                      ]}
                    />
                  </Col>
                  <Col xs={24} md={12}>
                    <Typography.Title level={5}>Downstream</Typography.Title>
                    <Table
                      size="small"
                      rowKey="table_id"
                      pagination={false}
                      dataSource={lineage.data.downstream}
                      columns={[
                        { title: "Table", dataIndex: "table_name" },
                        { title: "Edge", dataIndex: "node_type", width: 100 }
                      ]}
                    />
                  </Col>
                </Row>
              ) : (
                <EmptyState
                  title="Enter a table ID"
                  description="Lineage is built from query-log ingestion; complex SQL may produce incomplete graphs."
                />
              )}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
};
