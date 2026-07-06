import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  approvalAction,
  createApprovalRequest,
  createTierPolicy,
  deleteTierPolicy,
  getCostForecast,
  getS3TieringRecommendations,
  getTableLineage,
  getTierCandidates,
  listFleetAnomalies,
  listMaintenanceSignals,
  listPendingApprovals,
  listSources,
  listTables,
  listTierPolicies,
  patchTierPolicy,
  type TierPolicyRead,
  type TierPolicyWrite
} from "../api/client";
import type { TableRead } from "../api/types";
import { ConfirmAction } from "../components/confirm";
import { EmptyState, PageHeader, QueryErrorAlert } from "../components/common";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatCurrency, formatTime } from "../lib/format";
import { qualifiedTableName, schemaTableName } from "../lib/tableLabels";

type TierPolicyFormValues = TierPolicyWrite;

// A table failing once is often just a transient blip (network hiccup) -- this only flags
// tables that have failed to scan at least this many times *in a row*, e.g. a stale dbt
// __dbt_tmp pointer whose metadata.json was deleted and will never come back on its own.
const BROKEN_TABLE_FAILURE_THRESHOLD = 2;

export const Governance = () => {
  const { api, subject, role } = useAuth();
  const queryClient = useQueryClient();
  const canManage = role === "admin" || role === "operator";
  const canApprove = canManage;
  const [lineageTableId, setLineageTableId] = useState("");
  const [tierTableId, setTierTableId] = useState("");
  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [policyModal, setPolicyModal] = useState<TierPolicyRead | "new" | null>(null);
  const [policyForm] = Form.useForm<TierPolicyFormValues>();
  const [approvalForm] = Form.useForm<{
    table_id: string;
    operation_type: string;
    reason?: string;
  }>();

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
  const maintenanceSignals = useQuery({
    queryKey: ["maintenance-signals", api.token],
    queryFn: () => listMaintenanceSignals(api),
    refetchInterval: 60_000
  });
  const brokenTables = useQuery({
    queryKey: ["broken-tables", api.token],
    queryFn: () =>
      listTables(api, {
        predicate: leaf("consecutive_scan_failures", ">=", BROKEN_TABLE_FAILURE_THRESHOLD),
        sort: "-consecutive_scan_failures",
        limit: 50
      }),
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
  const s3Tiering = useQuery({
    queryKey: ["s3-tiering-recommendations", api.token],
    queryFn: () => getS3TieringRecommendations(api, { min_size_gb: 50 }),
    retry: false
  });
  const tierCandidates = useQuery({
    queryKey: ["tier-candidates", api.token, tierTableId],
    queryFn: () => getTierCandidates(api, tierTableId),
    enabled: tierTableId.length > 8
  });
  const sources = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api)
  });
  const tableOptions = useQuery({
    queryKey: ["governance-table-picker", api.token],
    queryFn: () => listTables(api, { limit: 200, offset: 0 })
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

  const saveTierPolicy = useMutation({
    mutationFn: (values: TierPolicyFormValues) =>
      policyModal && policyModal !== "new"
        ? patchTierPolicy(api, policyModal.id, values)
        : createTierPolicy(api, values),
    onSuccess: () => {
      message.success("Tier policy saved");
      setPolicyModal(null);
      void queryClient.invalidateQueries({ queryKey: ["tier-policies"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const removeTierPolicy = useMutation({
    mutationFn: (id: string) => deleteTierPolicy(api, id),
    onSuccess: () => {
      message.success("Tier policy deleted");
      void queryClient.invalidateQueries({ queryKey: ["tier-policies"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const requestApproval = useMutation({
    mutationFn: (values: { table_id: string; operation_type: string; reason?: string }) =>
      createApprovalRequest(api, {
        table_id: values.table_id,
        operation_type: values.operation_type,
        requested_by: subject || "ui-user",
        reason: values.reason
      }),
    onSuccess: () => {
      message.success("Approval request submitted");
      setApprovalModalOpen(false);
      approvalForm.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["pending-approvals"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const openPolicyModal = (policy: TierPolicyRead | "new") => {
    setPolicyModal(policy);
    if (policy === "new") {
      policyForm.setFieldsValue({
        name: "",
        source_id: sources.data?.[0]?.id ?? "",
        storage_class: "STANDARD_IA",
        min_age_days: 30,
        enabled: true
      });
      return;
    }
    policyForm.setFieldsValue({
      name: policy.name,
      source_id: policy.source_id,
      storage_class: policy.storage_class,
      min_age_days: policy.min_age_days,
      enabled: policy.enabled
    });
  };

  return (
    <div>
      <PageHeader
        title="Governance"
        subtitle="Approvals, anomalies, FinOps forecast, tier policies, and table lineage surfaced from the advanced API."
      />
      <Alert
        className="page-alert"
        type="warning"
        showIcon
        message="Start with urgent signals first: pending approvals, broken tables, and active anomalies."
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}>
          <Card
            title="Pending approvals"
            extra={
              <Space>
                <Tag>{approvals.data?.length ?? 0}</Tag>
                {canManage ? (
                  <Button size="small" icon={<PlusOutlined />} onClick={() => setApprovalModalOpen(true)}>
                    Request
                  </Button>
                ) : null}
              </Space>
            }
          >
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

        <Col xs={24}>
          <Card
            title="Broken tables"
            extra={<Tag color={brokenTables.data?.items.length ? "red" : undefined}>{brokenTables.data?.items.length ?? 0}</Tag>}
          >
            <QueryErrorAlert error={brokenTables.error} title="Failed to load broken tables" />
            <Table<TableRead>
              size="small"
              rowKey="id"
              loading={brokenTables.isLoading}
              dataSource={brokenTables.data?.items ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: (
                  <EmptyState
                    title="No broken tables"
                    description={`Tables that fail to scan ${BROKEN_TABLE_FAILURE_THRESHOLD}+ times in a row (e.g. a stale metadata pointer) show up here.`}
                  />
                )
              }}
              columns={[
                {
                  title: "Table",
                  render: (_, row) => (
                    <Link to={`/tables/${row.id}`}>{qualifiedTableName(row)}</Link>
                  )
                },
                {
                  title: "Consecutive failures",
                  dataIndex: "consecutive_scan_failures",
                  width: 160,
                  align: "right" as const,
                  render: (value: number | undefined) => <Tag color="red">{value ?? 0}</Tag>
                },
                {
                  title: "Last error",
                  dataIndex: "last_scan_error",
                  ellipsis: { showTitle: false },
                  render: (value: string | null) => (
                    <Typography.Text type="secondary" ellipsis={{ tooltip: value }}>
                      {value ?? "-"}
                    </Typography.Text>
                  )
                },
                {
                  title: "Last failed",
                  dataIndex: "last_scan_error_at",
                  width: 160,
                  render: formatTime
                }
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} xl={12}>
          <Card title="Maintenance signals">
            <QueryErrorAlert error={maintenanceSignals.error} title="Failed to load maintenance signals" />
            <Table
              size="small"
              rowKey={(row) => `${row.table_id}-${row.signal_type}-${row.detected_at}`}
              loading={maintenanceSignals.isLoading}
              dataSource={maintenanceSignals.data ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: (
                  <EmptyState
                    title="No maintenance signals"
                    description="File degradation, latency spikes, and cost spikes appear here when detected."
                  />
                )
              }}
              columns={[
                {
                  title: "Table",
                  render: (_, row) => (
                    <Link to={`/tables/${row.table_id}`}>{row.table_name}</Link>
                  )
                },
                { title: "Type", dataIndex: "signal_type", width: 140 },
                {
                  title: "Severity",
                  dataIndex: "severity",
                  width: 100,
                  render: (value: string) => (
                    <Tag color={value === "critical" ? "red" : value === "warning" ? "gold" : "default"}>
                      {value}
                    </Tag>
                  )
                },
                {
                  title: "Current",
                  dataIndex: "current_value",
                  width: 80,
                  align: "right" as const,
                  render: (value: number) => value.toFixed(2)
                },
                { title: "Description", dataIndex: "description", ellipsis: true }
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
          <Card
            title="Tier policies"
            extra={
              canManage ? (
                <Button size="small" icon={<PlusOutlined />} onClick={() => openPolicyModal("new")}>
                  Add policy
                </Button>
              ) : null
            }
          >
            <QueryErrorAlert error={tierPolicies.error} title="Failed to load tier policies" />
            <Table
              size="small"
              rowKey="id"
              loading={tierPolicies.isLoading}
              dataSource={tierPolicies.data ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: (
                  <EmptyState
                    title="No tier policies"
                    description="Create policies here or seed them via Helm for automated S3 tiering."
                  />
                )
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
                },
                ...(canManage
                  ? [
                      {
                        title: "Actions",
                        width: 140,
                        render: (_: unknown, row: TierPolicyRead) => (
                          <Space size={4}>
                            <Button size="small" onClick={() => openPolicyModal(row)}>
                              Edit
                            </Button>
                            <ConfirmAction
                              title="Delete this tier policy?"
                              description="Tables will no longer be evaluated against this policy."
                              onConfirm={() => removeTierPolicy.mutate(row.id)}
                            >
                              <Button size="small" danger loading={removeTierPolicy.isPending}>
                                Delete
                              </Button>
                            </ConfirmAction>
                          </Space>
                        )
                      }
                    ]
                  : [])
              ]}
            />
          </Card>
        </Col>

        <Col xs={24}>
          <Card
            title="S3 Intelligent-Tiering recommendations"
            extra={
              s3Tiering.data ? (
                <Tag color="green">
                  {formatCurrency(s3Tiering.data.total_potential_savings_usd)}/yr potential
                </Tag>
              ) : null
            }
          >
            <QueryErrorAlert error={s3Tiering.error} title="Failed to load S3 tiering recommendations" />
            <Table
              size="small"
              rowKey="table_id"
              loading={s3Tiering.isLoading}
              dataSource={s3Tiering.data?.recommendations ?? []}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              locale={{
                emptyText: (
                  <EmptyState
                    title="No tiering candidates"
                    description="Large tables with cold access patterns appear here after cost scans."
                  />
                )
              }}
              columns={[
                {
                  title: "Table",
                  render: (_, row) => <Link to={`/tables/${row.table_id}`}>{row.table_name}</Link>
                },
                {
                  title: "Size",
                  dataIndex: "current_size_gb",
                  width: 90,
                  align: "right" as const,
                  render: (value: number) => `${value.toFixed(1)} GB`
                },
                {
                  title: "Days idle",
                  dataIndex: "days_since_last_access",
                  width: 90,
                  align: "right" as const
                },
                {
                  title: "Annual savings",
                  dataIndex: "estimated_annual_savings_usd",
                  width: 120,
                  align: "right" as const,
                  render: (value: number) => formatCurrency(value)
                },
                {
                  title: "Recommendation",
                  dataIndex: "recommendation",
                  width: 140,
                  render: (value: string) => <Tag>{value}</Tag>
                }
              ]}
            />
          </Card>
        </Col>

        <Col xs={24}>
          <Card title="Tier candidates by policy">
            <Space direction="vertical" size={12} className="full-width">
              <Select
                showSearch
                allowClear
                placeholder="Select a table to evaluate tier policies"
                optionFilterProp="label"
                style={{ maxWidth: 480, width: "100%" }}
                loading={tableOptions.isLoading}
                options={(tableOptions.data?.items ?? []).map((table) => ({
                  value: table.id,
                  label: qualifiedTableName(table)
                }))}
                value={tierTableId || undefined}
                onChange={(value) => setTierTableId(value ?? "")}
              />
              <QueryErrorAlert error={tierCandidates.error} title="Failed to load tier candidates" />
              {tierCandidates.data ? (
                <>
                  <Space wrap>
                    <Tag>Candidates: {tierCandidates.data.candidates.length}</Tag>
                    {typeof tierCandidates.data.summary.estimated_monthly_savings_usd === "number" ? (
                      <Tag color="green">
                        Est. savings {formatCurrency(tierCandidates.data.summary.estimated_monthly_savings_usd)}/mo
                      </Tag>
                    ) : null}
                  </Space>
                  <Table
                    size="small"
                    rowKey={(row, index) => `${row.partition_value ?? "all"}-${index}`}
                    pagination={{ pageSize: 8, hideOnSinglePage: true }}
                    dataSource={tierCandidates.data.candidates}
                    columns={[
                      { title: "Partition", dataIndex: "partition_value", render: (v) => v ?? "(table)" },
                      { title: "Age (days)", dataIndex: "age_days", width: 100, align: "right" as const },
                      { title: "Target class", dataIndex: "target_storage_class", width: 140 }
                    ]}
                  />
                </>
              ) : (
                <EmptyState
                  title="Select a table"
                  description="Tier candidates are computed from scan partitions and enabled tier policies."
                />
              )}
            </Space>
          </Card>
        </Col>

        <Col xs={24}>
          <Card title="Table lineage">
            <Space direction="vertical" size={12} className="full-width">
              <Select
                showSearch
                allowClear
                placeholder="Select a table for lineage"
                optionFilterProp="label"
                style={{ maxWidth: 480, width: "100%" }}
                loading={tableOptions.isLoading}
                options={(tableOptions.data?.items ?? []).map((table) => ({
                  value: table.id,
                  label: qualifiedTableName(table)
                }))}
                value={lineageTableId || undefined}
                onChange={(value) => setLineageTableId(value ?? "")}
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

      <Modal
        title="Request approval"
        open={approvalModalOpen}
        onCancel={() => setApprovalModalOpen(false)}
        onOk={() => approvalForm.submit()}
        confirmLoading={requestApproval.isPending}
        destroyOnHidden
      >
        <Form
          form={approvalForm}
          layout="vertical"
          onFinish={(values) => requestApproval.mutate(values)}
        >
          <Form.Item name="table_id" label="Table" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={tableOptions.isLoading}
              options={(tableOptions.data?.items ?? []).map((table) => ({
                value: table.id,
                label: qualifiedTableName(table)
              }))}
            />
          </Form.Item>
          <Form.Item name="operation_type" label="Operation" rules={[{ required: true }]}>
            <Select
              options={[
                { label: "Expire snapshots", value: "expire_snapshots" },
                { label: "Remove orphan files", value: "remove_orphan_files" },
                { label: "Optimize", value: "optimize" },
                { label: "Optimize manifests", value: "optimize_manifests" }
              ]}
            />
          </Form.Item>
          <Form.Item name="reason" label="Reason">
            <Input.TextArea rows={3} placeholder="Why this destructive operation is needed" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={policyModal === "new" ? "Create tier policy" : "Edit tier policy"}
        open={policyModal !== null}
        onCancel={() => setPolicyModal(null)}
        onOk={() => policyForm.submit()}
        confirmLoading={saveTierPolicy.isPending}
        destroyOnHidden
      >
        <Form<TierPolicyFormValues>
          form={policyForm}
          layout="vertical"
          onFinish={(values) => saveTierPolicy.mutate(values)}
        >
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="source_id" label="Source" rules={[{ required: true }]}>
            <Select
              loading={sources.isLoading}
              options={(sources.data ?? []).map((source) => ({
                value: source.id,
                label: source.name
              }))}
            />
          </Form.Item>
          <Form.Item name="storage_class" label="Storage class" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "STANDARD_IA", label: "STANDARD_IA" },
                { value: "GLACIER", label: "GLACIER" },
                { value: "DEEP_ARCHIVE", label: "DEEP_ARCHIVE" },
                { value: "INTELLIGENT_TIERING", label: "INTELLIGENT_TIERING" }
              ]}
            />
          </Form.Item>
          <Form.Item name="min_age_days" label="Minimum age (days)" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};
