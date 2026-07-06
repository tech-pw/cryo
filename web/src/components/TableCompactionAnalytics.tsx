import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Space, Table, Tag, Typography, message } from "antd";
import {
  getTableBenefitScores,
  getTableEffectiveness,
  getTableSignals,
  updateTableBenefitScores,
  type BenefitScoreCandidate,
  type TableSignalRead
} from "../api/client";
import type { UUID } from "../api/types";
import { EmptyState, QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";
import { formatGb, formatNumber, formatTime } from "../lib/format";

interface Props {
  tableId: UUID;
  embedded?: boolean;
}

export const TableCompactionAnalytics = ({ tableId, embedded = false }: Props) => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const canUpdate = role === "admin" || role === "operator";

  const benefitScores = useQuery({
    queryKey: ["table-benefit-scores", tableId],
    queryFn: () => getTableBenefitScores(api, tableId),
    retry: false
  });
  const effectiveness = useQuery({
    queryKey: ["table-effectiveness", tableId],
    queryFn: () => getTableEffectiveness(api, tableId),
    retry: false
  });
  const signals = useQuery({
    queryKey: ["table-signals", tableId],
    queryFn: () => getTableSignals(api, tableId),
    retry: false
  });

  const refreshScores = useMutation({
    mutationFn: () => updateTableBenefitScores(api, tableId),
    onSuccess: () => {
      message.success("Benefit scores updated");
      void queryClient.invalidateQueries({ queryKey: ["table-benefit-scores", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const benefitColumns = [
    {
      title: "Partition",
      dataIndex: "partition_value",
      render: (value: string | null | undefined) => value ?? "(table)"
    },
    {
      title: "Benefit score",
      dataIndex: "benefit_score",
      align: "right" as const,
      render: (value: number | undefined) => (value != null ? value.toFixed(3) : "—")
    },
    {
      title: "Small files",
      dataIndex: "small_files",
      align: "right" as const,
      render: (value: number | undefined) => formatNumber(value)
    },
    {
      title: "Size",
      dataIndex: "size_gb",
      align: "right" as const,
      render: (value: number | undefined) => formatGb(value)
    },
    {
      title: "Query freq.",
      dataIndex: "query_frequency",
      align: "right" as const,
      render: (value: number | undefined) => formatNumber(value)
    }
  ];

  const content = (
    <>
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12}>
          <Typography.Title level={5}>Top compaction candidates</Typography.Title>
          <QueryErrorAlert error={benefitScores.error} title="Failed to load benefit scores" />
          <Table<BenefitScoreCandidate>
            size="small"
            rowKey={(row, index) => `${row.partition_value ?? "all"}-${index}`}
            loading={benefitScores.isLoading}
            dataSource={benefitScores.data?.top_candidates ?? []}
            columns={benefitColumns}
            pagination={{ pageSize: 5, hideOnSinglePage: true }}
            locale={{
              emptyText: (
                <EmptyState
                  title="No scored partitions"
                  description="Run a scan and refresh benefit scores to rank compaction targets."
                />
              )
            }}
          />
          {benefitScores.data ? (
            <Typography.Text type="secondary">
              {benefitScores.data.partitions_scored.toLocaleString()} partitions scored
            </Typography.Text>
          ) : null}
        </Col>
        <Col xs={24} lg={12}>
          <Typography.Title level={5}>Compaction effectiveness</Typography.Title>
          <QueryErrorAlert error={effectiveness.error} title="Failed to load effectiveness stats" />
          {effectiveness.data ? (
            <Space direction="vertical" size={8} className="full-width">
              <Space wrap>
                <Tag color={effectiveness.data.effectiveness_ratio >= 10 ? "success" : "gold"}>
                  Ratio {effectiveness.data.effectiveness_ratio.toFixed(1)}×
                </Tag>
                <Tag>{effectiveness.data.effectiveness}</Tag>
                <Tag>{effectiveness.data.run_count} runs</Tag>
              </Space>
              <Typography.Text>
                {formatNumber(effectiveness.data.total_input_files)} input →{" "}
                {formatNumber(effectiveness.data.total_output_files)} output files
              </Typography.Text>
              <Typography.Text type="secondary">{effectiveness.data.recommendation}</Typography.Text>
            </Space>
          ) : effectiveness.isLoading ? (
            <Typography.Text type="secondary">Loading…</Typography.Text>
          ) : (
            <EmptyState title="No compaction history" description="Effectiveness appears after optimize runs." />
          )}
        </Col>
        <Col xs={24}>
          <Typography.Title level={5}>Maintenance trigger signals</Typography.Title>
          <QueryErrorAlert error={signals.error} title="Failed to load table signals" />
          {signals.data?.any_triggered ? (
            <Alert
              type="warning"
              showIcon
              message="One or more maintenance signals are active"
              style={{ marginBottom: 8 }}
            />
          ) : null}
          <Table<TableSignalRead>
            size="small"
            rowKey={(row) => row.signal_name}
            loading={signals.isLoading}
            dataSource={signals.data?.signals ?? []}
            pagination={false}
            columns={[
              {
                title: "Signal",
                dataIndex: "signal_name",
                width: 160
              },
              {
                title: "Status",
                dataIndex: "triggered",
                width: 100,
                render: (value: boolean) => (
                  <Tag color={value ? "orange" : "default"}>{value ? "Triggered" : "OK"}</Tag>
                )
              },
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
              { title: "Reason", dataIndex: "reason", ellipsis: true }
            ]}
            locale={{
              emptyText: <EmptyState title="No signals evaluated" description="Signal checks run on demand." />
            }}
          />
          {signals.data ? (
            <Typography.Text type="secondary">Checked {formatTime(signals.data.checked_at)}</Typography.Text>
          ) : null}
        </Col>
      </Row>
    </>
  );

  if (embedded) {
    return (
      <div className="td-embedded-content">
        {canUpdate ? (
          <div className="td-embedded-toolbar">
            <Button size="small" loading={refreshScores.isPending} onClick={() => refreshScores.mutate()}>
              Refresh benefit scores
            </Button>
          </div>
        ) : null}
        {content}
      </div>
    );
  }

  return (
    <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">Compaction analytics & signals</span>
        {canUpdate ? (
          <div className="td-chart-card-actions">
            <Button size="small" loading={refreshScores.isPending} onClick={() => refreshScores.mutate()}>
              Refresh benefit scores
            </Button>
          </div>
        ) : null}
      </div>
      <div className="td-chart-card-content">
        {content}
      </div>
    </Card>
  );
};
