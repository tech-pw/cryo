import { useQuery } from "@tanstack/react-query";
import { Card, Empty, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ApiOptions } from "../../api/client";
import { getUnionable } from "../../api/client";
import type { UnionScoreRead } from "../../api/types";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const formatScore = (v: number) => `${Math.round(v * 100)}%`;

export const UnionableTab = ({ api, tableId }: Props) => {
  const query = useQuery({
    queryKey: ["unionable", tableId],
    queryFn: () => getUnionable(api, tableId, { k: 10, useLlm: false }),
    enabled: Boolean(tableId)
  });

  if (query.isLoading) return <Skeleton active />;
  if (query.isError) {
    return (
      <Card>
        <Typography.Text type="danger">Failed to load union candidates: {(query.error as Error).message}</Typography.Text>
      </Card>
    );
  }
  const results: UnionScoreRead[] = query.data?.results ?? [];

  if (results.length === 0) {
    return (
      <Card
        title="Tables that can union with this one"
        extra={<Tooltip title="LLM-embedded schema similarity (EasyTUS, IEEE BigData 2025)"><Tag>EasyTUS</Tag></Tooltip>}
      >
        <Empty description="No union-compatible tables found." />
      </Card>
    );
  }

  return (
    <Card
      title="Tables that can union with this one"
      extra={<Tooltip title="LLM-embedded schema similarity (EasyTUS, IEEE BigData 2025)"><Tag color="purple">EasyTUS</Tag></Tooltip>}
    >
      <Table
        dataSource={results}
        rowKey={(r) => r.candidate_table_id}
        size="small"
        pagination={false}
        columns={[
          {
            title: "Candidate",
            dataIndex: "candidate_table_id",
            key: "candidate",
            render: (v: string) => <Typography.Text code>{v}</Typography.Text>
          },
          {
            title: "Match",
            dataIndex: "score",
            key: "score",
            sorter: (a, b) => a.score - b.score,
            render: (v: number) => {
              const pct = v;
              const color = pct >= 0.7 ? "success" : pct >= 0.4 ? "warning" : "default";
              return <Tag color={color}>{formatScore(v)}</Tag>;
            }
          },
          {
            title: "Overlap columns",
            dataIndex: "overlap_columns",
            key: "overlap",
            render: (cols: string[]) => (
              <Space size={4} wrap>
                {cols.slice(0, 5).map((c) => (
                  <Tag key={c}>{c}</Tag>
                ))}
                {cols.length > 5 ? <Typography.Text type="secondary">+{cols.length - 5} more</Typography.Text> : null}
              </Space>
            )
          },
          {
            title: "Suggested UNION",
            dataIndex: "union_sql",
            key: "sql",
            render: (v: string) => (
              <Tooltip title={v}>
                <Typography.Text code style={{ fontSize: 11 }}>{v.slice(0, 80)}{v.length > 80 ? "…" : ""}</Typography.Text>
              </Tooltip>
            )
          },
          {
            title: "Rationale",
            dataIndex: "rationale",
            key: "rationale",
            render: (v: string) => <Typography.Text style={{ fontSize: 11 }}>{v}</Typography.Text>
          }
        ]}
      />
    </Card>
  );
};
