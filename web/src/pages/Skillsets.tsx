import { useQuery } from "@tanstack/react-query";
import { Card, Empty, Skeleton, Space, Table, Tag, Typography } from "antd";
import { listSkillsets } from "../api/client";
import { useAuth } from "../lib/auth";

export const Skillsets = () => {
  const { api } = useAuth();
  const query = useQuery({
    queryKey: ["skillsets"],
    queryFn: () => listSkillsets(api)
  });

  if (query.isLoading) return <Skeleton active />;
  if (query.isError) {
    return (
      <Card>
        <Typography.Text type="danger">Failed to load skillsets: {(query.error as Error).message}</Typography.Text>
      </Card>
    );
  }

  const rows = query.data?.skillsets ?? [];

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>AI Skillsets</Typography.Title>
        <Typography.Text type="secondary">
          Versioned prompt + tool definitions used by the Table Detail AI insights. Outcomes are recorded per recommendation; acceptance rate
          drives A/B rollouts (Schneider et al., 2026).
        </Typography.Text>
      </div>
      <Card>
        {rows.length === 0 ? (
          <Empty description="No skillsets registered. The built-in iceberg_advisor skillset will seed on first use." />
        ) : (
          <Table
            dataSource={rows}
            rowKey={(s) => s.id}
            size="small"
            pagination={false}
            columns={[
              { title: "Name", dataIndex: "name", key: "name", render: (v) => <Typography.Text code>{v}</Typography.Text> },
              { title: "Version", dataIndex: "version", key: "version" },
              {
                title: "Status",
                dataIndex: "is_active",
                key: "active",
                render: (v) => (v ? <Tag color="success">active</Tag> : <Tag>inactive</Tag>)
              },
              {
                title: "Acceptance rate",
                dataIndex: "metrics",
                key: "acceptance",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.acceptance_rate;
                  return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
                }
              },
              {
                title: "Avg score",
                dataIndex: "metrics",
                key: "score",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.avg_score;
                  return typeof v === "number" ? v.toFixed(2) : "—";
                }
              },
              {
                title: "Outcomes",
                dataIndex: "metrics",
                key: "outcomes",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.outcomes_count;
                  return typeof v === "number" ? v : "—";
                }
              },
              {
                title: "Created",
                dataIndex: "created_at",
                key: "created_at",
                render: (v) => (v ? new Date(v).toLocaleString() : "—")
              }
            ]}
          />
        )}
      </Card>
    </Space>
  );
};
