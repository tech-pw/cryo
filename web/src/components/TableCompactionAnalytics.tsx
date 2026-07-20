import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Space, Table, Tag, Typography, message } from "antd";
import {
  getAutoCompCandidates,
  getTable,
  getTableSignals,
  updateTableBenefitScores,
  type AutoCompCandidateItem,
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

// Okolnychyi et al. (VLDB'24) minor/major delete-compaction policy, mirrored client-side for
// display only -- cryo/planner/planner.py::_delete_compaction_policy is the source of truth;
// this must stay in sync with its default thresholds (delete_minor_file_threshold=50,
// delete_major_pct_threshold=10.0) since presets can override them per-table and this chip
// has no way to know a preset's actual configured params, only the table's current stats.
const DEFAULT_MINOR_FILE_THRESHOLD = 50;
const DEFAULT_MAJOR_PCT_THRESHOLD = 10.0;

function deletePolicyChip(
  formatVersion: number | null | undefined,
  positionDeleteFiles: number | null | undefined,
  deleteFilePct: number | null | undefined
): { label: string; color: string; title?: string } {
  if (formatVersion != null && formatVersion >= 3) {
    return { label: "V3 (deletion vectors)", color: "default" };
  }
  const pct = deleteFilePct ?? 0;
  const files = positionDeleteFiles ?? 0;
  if (pct >= DEFAULT_MAJOR_PCT_THRESHOLD) {
    return {
      label: "Major rewrite recommended",
      color: "error",
      title: `delete_file_pct ${pct.toFixed(1)}% >= ${DEFAULT_MAJOR_PCT_THRESHOLD}%`
    };
  }
  if (files >= DEFAULT_MINOR_FILE_THRESHOLD) {
    return {
      label: "Minor delete compaction recommended",
      color: "warning",
      title: `${files} position-delete files >= ${DEFAULT_MINOR_FILE_THRESHOLD}`
    };
  }
  return { label: "Healthy", color: "success" };
}

export const TableCompactionAnalytics = ({ tableId, embedded = false }: Props) => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const canUpdate = role === "admin" || role === "operator";

  const candidates = useQuery({
    queryKey: ["table-autocomp-candidates", tableId],
    queryFn: () => getAutoCompCandidates(api, tableId),
    retry: false
  });
  const table = useQuery({
    queryKey: ["table-compaction-stats", tableId],
    queryFn: () => getTable(api, tableId),
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
      void queryClient.invalidateQueries({ queryKey: ["table-autocomp-candidates", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const effectiveness = candidates.data?.effectiveness as
    | { effectiveness_ratio?: number; effectiveness?: string; run_count?: number; recommendation?: string }
    | null
    | undefined;
  const compare = candidates.data?.compare;
  const stats = table.data?.stats;
  const deletePolicy = deletePolicyChip(
    (stats?.raw as { format_version?: number } | null | undefined)?.format_version,
    stats?.position_delete_files,
    stats?.delete_file_pct
  );

  const candidateColumns = [
    {
      title: "Partition",
      dataIndex: "partition_value",
      render: (value: string | null | undefined) => value || "(table)"
    },
    {
      title: "Benefit score",
      dataIndex: "benefit_score",
      align: "right" as const,
      render: (value: number | undefined) => (value != null ? value.toFixed(3) : "—")
    },
    {
      title: "MOOP score",
      dataIndex: "moop_score",
      align: "right" as const,
      render: (value: number | null | undefined) => (value != null ? value.toFixed(3) : "—")
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
      title: "Status",
      dataIndex: "eligible",
      render: (eligible: boolean, row: AutoCompCandidateItem) =>
        eligible ? (
          <Tag color="success">Eligible</Tag>
        ) : (
          <Tag color="default" title={row.skip_reason ?? undefined}>
            {row.skip_reason ?? "skipped"}
          </Tag>
        )
    }
  ];

  const content = (
    <>
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={16}>
          <Typography.Title level={5}>Compaction candidates (AutoComp §A2)</Typography.Title>
          <QueryErrorAlert error={candidates.error} title="Failed to load AutoComp candidates" />
          {compare?.autocomp_wins ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8 }}
              title={`AutoComp ranking beats static small-files ordering by ${compare.improvement_ratio.toFixed(2)}×`}
            />
          ) : null}
          <Table<AutoCompCandidateItem>
            size="small"
            rowKey={(row) => `${row.partition_value}-${row.benefit_score}`}
            loading={candidates.isLoading}
            dataSource={candidates.data?.items ?? []}
            columns={candidateColumns}
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
          {candidates.data ? (
            <Typography.Text type="secondary">
              {candidates.data.items.length.toLocaleString()} partitions scored,{" "}
              {candidates.data.skipped.length.toLocaleString()} skipped
            </Typography.Text>
          ) : null}
        </Col>
        <Col xs={24} lg={8}>
          <Typography.Title level={5}>Compaction effectiveness</Typography.Title>
          {effectiveness ? (
            <Space orientation="vertical" size={8} className="full-width">
              <Space wrap>
                <Tag color={(effectiveness.effectiveness_ratio ?? 0) >= 10 ? "success" : "gold"}>
                  Ratio {(effectiveness.effectiveness_ratio ?? 0).toFixed(1)}×
                </Tag>
                <Tag>{effectiveness.effectiveness}</Tag>
                <Tag>{effectiveness.run_count ?? 0} runs</Tag>
              </Space>
              <Typography.Text type="secondary">{effectiveness.recommendation}</Typography.Text>
            </Space>
          ) : candidates.isLoading ? (
            <Typography.Text type="secondary">Loading…</Typography.Text>
          ) : (
            <EmptyState title="No compaction history" description="Effectiveness appears after optimize runs." />
          )}
          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Delete-file health
          </Typography.Title>
          <Tag color={deletePolicy.color} title={deletePolicy.title}>
            {deletePolicy.label}
          </Tag>
        </Col>
        <Col xs={24}>
          <Typography.Title level={5}>Maintenance trigger signals</Typography.Title>
          <QueryErrorAlert error={signals.error} title="Failed to load table signals" />
          {signals.data?.any_triggered ? (
            <Alert
              type="warning"
              showIcon
              title="One or more maintenance signals are active"
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
