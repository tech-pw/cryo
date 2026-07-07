import { useQuery } from "@tanstack/react-query";
import { Card, Empty, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import type { ApiOptions } from "../../api/client";
import { getCompactionCandidates } from "../../api/client";
import type { CompactionCandidateRead } from "../../api/types";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const PRIORITY_COLORS: Record<string, string> = {
  HIGH: "error",
  MED: "warning",
  MEDIUM: "warning",
  LOW: "default"
};

const formatNumber = (n: number | null | undefined): string =>
  n == null ? "—" : n.toLocaleString();

const formatGb = (n: number | null | undefined): string => {
  if (n == null) return "—";
  if (n < 1) return `${(n * 1024).toFixed(0)} MB`;
  return `${n.toFixed(2)} GB`;
};

const formatUsd = (n: number | null | undefined): string =>
  n == null ? "—" : `$${n.toFixed(0)}`;

export const CompactionCandidatesTab = ({ api, tableId }: Props) => {
  const query = useQuery({
    queryKey: ["compaction-candidates", tableId],
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled: Boolean(tableId)
  });

  if (query.isLoading) return <Skeleton active />;
  if (query.isError) {
    return (
      <Card>
        <Typography.Text type="danger">Failed to load compaction candidates: {(query.error as Error).message}</Typography.Text>
      </Card>
    );
  }
  const data = query.data;
  const candidates: CompactionCandidateRead[] = data?.candidates ?? [];
  const totalSavings = candidates.reduce((s, c) => s + (c.savings_estimate_usd ?? 0), 0);
  const highPriority = candidates.filter((c) => c.priority === "HIGH").length;

  if (candidates.length === 0) {
    return (
      <Card title="Top Compaction Candidates" extra={<Typography.Text type="secondary">AutoComp-Style Ranking</Typography.Text>}>
        <Empty description="No Compaction Candidates Found. Either the table has healthy file distribution, or it has not been scanned." />
      </Card>
    );
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space size={32}>
          <Statistic title="Candidates" value={candidates.length} />
          <Statistic title="High Priority" value={highPriority} valueStyle={highPriority > 0 ? { color: "#dc3545" } : undefined} />
          <Statistic title="Projected 12-Month Savings" value={formatUsd(totalSavings)} valueStyle={{ color: "#1e8e5a" }} />
        </Space>
      </Card>
      <Card
        title="Top 10 Candidates by Benefit Score"
        extra={
          <Tooltip title="Score = (small_files × query_freq) / partition_size_gb. Higher = more impact from compacting this partition.">
            <Tag color="blue">AutoComp</Tag>
          </Tooltip>
        }
      >
        <Table
          dataSource={candidates}
          rowKey={(c) => c.partition_path}
          size="small"
          pagination={false}
          columns={[
            {
              title: "Partition",
              dataIndex: "partition_path",
              key: "partition_path",
              render: (v) => <Typography.Text code>{v}</Typography.Text>
            },
            { title: "Score", dataIndex: "score", key: "score", sorter: (a, b) => a.score - b.score, render: (v) => v.toFixed(2) },
            {
              title: "Priority",
              dataIndex: "priority",
              key: "priority",
              render: (v) => <Tag color={PRIORITY_COLORS[v] ?? "default"}>{v}</Tag>
            },
            { title: "Small Files", dataIndex: "small_files", key: "small_files", render: formatNumber },
            { title: "Size", dataIndex: "partition_size_gb", key: "size", render: formatGb },
            { title: "QPS (30 Days)", dataIndex: "query_freq_per_day", key: "qps", render: (v) => v.toFixed(1) },
            { title: "p95 Latency (ms)", dataIndex: "p95_latency_ms", key: "p95", render: formatNumber },
            { title: "Savings (12-Month)", dataIndex: "savings_estimate_usd", key: "savings", render: formatUsd },
            {
              title: "Reasons",
              dataIndex: "reasons",
              key: "reasons",
              render: (reasons: string[]) => (
                <Space orientation="vertical" size={2}>
                  {reasons.map((r, i) => (
                    <Typography.Text key={i} type="secondary" style={{ fontSize: 11 }}>· {r}</Typography.Text>
                  ))}
                </Space>
              )
            }
          ]}
        />
      </Card>
    </Space>
  );
};
