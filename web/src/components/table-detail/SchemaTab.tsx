import { LockOutlined, TableOutlined, WarningOutlined } from "@ant-design/icons";
import { Card, Table, Tag } from "antd";
import type { ReactNode } from "react";
import { EmptyState } from "../common";

export interface SchemaColumn {
  name: string;
  type: string;
  nullRate?: number;
  lowerBound?: string | number | null;
  upperBound?: string | number | null;
  isPartition?: boolean;
  piiCategory?: string;
  dqNullSpike?: boolean;
  dqValueRangeChanged?: boolean;
  dqValueRangeExplanation?: string;
}

interface SchemaTabProps {
  columns: SchemaColumn[];
  loading: boolean;
}

const typeClass = (type: string | undefined): string => {
  const lower = String(type || "unknown").toLowerCase();
  if (
    ["int", "integer", "bigint", "double", "float", "decimal", "long", "short"].some((t) =>
      lower.includes(t)
    )
  ) {
    return "td-type-numeric";
  }
  if (["string", "varchar", "char", "text"].some((t) => lower.includes(t))) {
    return "td-type-textual";
  }
  if (["timestamp", "date", "time", "datetime"].some((t) => lower.includes(t))) {
    return "td-type-temporal";
  }
  if (lower.includes("bool")) return "td-type-boolean";
  return "td-type-other";
};

export const SchemaTabLabel = (): ReactNode => (
  <span>
    <TableOutlined />
    Schema
  </span>
);

export const SchemaTab = ({ columns, loading }: SchemaTabProps) => (
  <div className="td-tab-content">
    <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">Columns &amp; Schema Metrics</span>
        <span className="td-chart-card-subtitle">{columns.length} columns detected</span>
      </div>
      <div className="td-chart-card-content">
        <Table
          size="small"
          rowKey="name"
          dataSource={columns}
          loading={loading}
          pagination={{ pageSize: 12, size: "small" }}
          className="td-schema-table"
          columns={[
            {
              title: "Name",
              dataIndex: "name",
              width: 220,
              sorter: (a: SchemaColumn, b: SchemaColumn) => a.name.localeCompare(b.name),
              render: (name: string, item: SchemaColumn) => (
                <div className="td-schema-col-name-wrap">
                  <span className="td-schema-col-name">{name}</span>
                  {item.isPartition ? (
                    <Tag color="blue" className="td-schema-badge td-schema-badge-partition">
                      Partition Key
                    </Tag>
                  ) : null}
                  {item.piiCategory && item.piiCategory !== "NONE" ? (
                    <Tag color="warning" className="td-schema-badge td-schema-badge-pii">
                      <LockOutlined style={{ marginRight: 3 }} />
                      PII: {item.piiCategory}
                    </Tag>
                  ) : null}
                </div>
              )
            },
            {
              title: "Type",
              dataIndex: "type",
              width: 140,
              render: (type: string) => (
                <span className={`td-type-badge ${typeClass(type)}`}>{type || "unknown"}</span>
              )
            },
            {
              title: "Nullability",
              dataIndex: "nullRate",
              align: "left",
              width: 200,
              sorter: (a: SchemaColumn, b: SchemaColumn) => (a.nullRate ?? 0) - (b.nullRate ?? 0),
              render: (nullRate: number | undefined, item: SchemaColumn) => {
                const rate = nullRate != null ? nullRate * 100 : 0;
                const popRate = 100 - rate;
                return (
                  <div className="td-schema-null-wrap">
                    <div className="td-schema-null-bar-container">
                      <div className="td-schema-null-bar-pop" style={{ width: `${popRate}%` }} />
                      <div className="td-schema-null-bar-null" style={{ width: `${rate}%` }} />
                    </div>
                    <span className="td-schema-null-text">{rate.toFixed(1)}% null</span>
                    {item.dqNullSpike ? (
                      <Tag color="error" className="td-schema-badge-spike animate-pulse">
                        <WarningOutlined style={{ marginRight: 3 }} />
                        Spike
                      </Tag>
                    ) : null}
                  </div>
                );
              }
            },
            {
              title: "Value Range / Bounds",
              dataIndex: "lowerBound",
              align: "left",
              width: 220,
              render: (_: unknown, item: SchemaColumn) => {
                const hasBounds =
                  item.lowerBound !== undefined &&
                  item.upperBound !== undefined &&
                  item.lowerBound !== null &&
                  item.upperBound !== null;
                if (!hasBounds) return <span className="td-bounds-empty">—</span>;
                return (
                  <div className="td-schema-bounds">
                    <span
                      className="td-bounds-text"
                      title={`${item.lowerBound} to ${item.upperBound}`}
                    >
                      {String(item.lowerBound)}{" "}
                      <span className="td-bounds-sep">to</span> {String(item.upperBound)}
                    </span>
                    {item.dqValueRangeChanged ? (
                      <span
                        className="td-bounds-warning"
                        title={item.dqValueRangeExplanation || "Value range shifted"}
                      >
                        <WarningOutlined
                          style={{ color: "var(--warning, #f0a020)", marginLeft: 6 }}
                        />
                      </span>
                    ) : null}
                  </div>
                );
              }
            },
            {
              title: "Status",
              key: "status",
              align: "center",
              width: 110,
              render: (_: unknown, item: SchemaColumn) => {
                const hasIssues = item.dqNullSpike || item.dqValueRangeChanged;
                return (
                  <Tag color={hasIssues ? "warning" : "success"} className="td-schema-status-tag">
                    {hasIssues ? "Warning" : "Healthy"}
                  </Tag>
                );
              }
            }
          ]}
          locale={{
            emptyText: (
              <EmptyState
                title="No column metadata yet"
                description="Ensure this table has successfully run its initial metadata scan."
              />
            )
          }}
        />
      </div>
    </Card>
  </div>
);

export default SchemaTab;