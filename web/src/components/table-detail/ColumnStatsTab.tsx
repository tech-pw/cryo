import { useQuery } from "@tanstack/react-query";
import { Card, Empty, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ApiOptions } from "../../api/client";
import { getColumnStats } from "../../api/client";
import type { ColumnSketchRead } from "../../api/types";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const formatNum = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());

export const ColumnStatsTab = ({ api, tableId }: Props) => {
  const query = useQuery({
    queryKey: ["column-stats", tableId],
    queryFn: () => getColumnStats(api, tableId),
    enabled: Boolean(tableId)
  });

  if (query.isLoading) return <Skeleton active />;
  if (query.isError) {
    return (
      <Card>
        <Typography.Text type="danger">Failed to load column stats: {(query.error as Error).message}</Typography.Text>
      </Card>
    );
  }
  const data = query.data;
  const columns: Array<{ name: string; stats: ColumnSketchRead }> = Object.entries(data?.columns ?? {}).map(
    ([name, stats]) => ({ name, stats: stats as ColumnSketchRead })
  );

  if (columns.length === 0) {
    return (
      <Card
        title="Per-Column Statistics (Puffin Sketches)"
        extra={<Tooltip title="Theta (NDV) + KLL (quantiles) sketches stored in Puffin sidecar files — FORMATS '26"><Tag color="blue">Puffin</Tag></Tooltip>}
      >
        <Empty description="No column sketches have been written yet. Run a scan to generate them." />
      </Card>
    );
  }

  return (
    <Card
      title="Per-Column Statistics (Puffin Sketches)"
      extra={
        <Space size={4}>
          <Tooltip title="Theta (NDV) + KLL (quantiles) sketches stored in Puffin sidecar files — FORMATS '26">
            <Tag color="blue">Puffin</Tag>
          </Tooltip>
          <Tooltip title="Read these values directly from the table's metadata — no data file is scanned">
            <Tag color="purple">Zero-scan</Tag>
          </Tooltip>
        </Space>
      }
    >
      <Table
        dataSource={columns}
        rowKey={(c) => c.name}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={[
          {
            title: "Column",
            dataIndex: "name",
            key: "name",
            render: (v) => <Typography.Text code>{v}</Typography.Text>
          },
          {
            title: "Type",
            dataIndex: "stats",
            key: "type",
            render: (_, c) => c.stats.field_type ?? "—"
          },
          {
            title: "Distinct (NDV)",
            dataIndex: "stats",
            key: "ndv",
            render: (_, c) => formatNum(c.stats.ndv_estimate)
          },
          {
            title: "p50",
            dataIndex: "stats",
            key: "p50",
            render: (_, c) => formatNum(c.stats.quantiles?.p50)
          },
          {
            title: "p95",
            dataIndex: "stats",
            key: "p95",
            render: (_, c) => formatNum(c.stats.quantiles?.p95)
          },
          {
            title: "p99",
            dataIndex: "stats",
            key: "p99",
            render: (_, c) => formatNum(c.stats.quantiles?.p99)
          },
          {
            title: "Sketches",
            dataIndex: "stats",
            key: "sketches",
            render: (_, c) => (
              <Tooltip title={`${c.stats.puffin_blobs?.length ?? 0} Puffin blob(s)`}>
                <Tag>{c.stats.sketch_count}</Tag>
              </Tooltip>
            )
          }
        ]}
      />
    </Card>
  );
};
