import { CopyOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Button, Descriptions, Drawer, Empty, Segmented, Select, Space, Tabs, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { previewMaintenance } from "../api/client";
import type { EngineType, JsonObject, Mode, SavingsProjectionRead, TableRead } from "../api/types";
import { CostPrecisionBadge } from "./CostPrecisionBadge";
import { StatStrip } from "./common";
import { SavingsByOperation } from "./SavingsByOperation";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatGb, formatNumber } from "../lib/format";
import type { CostByOperation } from "../api/types";

interface Props {
  open: boolean;
  tables: TableRead[];
  onClose: () => void;
  params?: JsonObject;
}

const savingsToByOperation = (savings?: SavingsProjectionRead | null): CostByOperation[] => {
  if (!savings?.by_operation) return [];
  return Object.entries(savings.by_operation)
    .filter(([, amount]) => amount > 0)
    .map(([operation, savings_usd_month]) => ({
      operation: operation as CostByOperation["operation"],
      savings_usd_month,
      reclaimable_bytes: 0
    }));
};

export const PlanPreview = ({ open, tables, onClose, params = {} }: Props) => {
  const { api } = useAuth();
  const [engine, setEngine] = useState<EngineType>("trino");
  const [mode, setMode] = useState<Mode>("deep");

  const requestTables = useMemo(
    () =>
      tables.map((table) => ({
        schema_name: table.schema_name,
        table_name: table.table_name,
        stats: { ...(table.stats ?? {}), ...(table.stats?.raw ?? {}) },
        partitions: Array.isArray(table.stats?.raw?.partitions)
          ? (table.stats?.raw?.partitions as JsonObject[])
          : []
      })),
    [tables]
  );

  const preview = useMutation({
    mutationFn: () => previewMaintenance(api, { engine, mode, params, tables: requestTables })
  });

  const estimate = useMemo(() => {
    const statements = preview.data?.items.flatMap((item) => item.statements) ?? [];
    const savingsItems = preview.data?.items.map((item) => item.savings).filter(Boolean) ?? [];
    const totalSavings = savingsItems.reduce(
      (sum, item) => sum + (item?.projected_savings_usd_month ?? 0),
      0
    );
    const totalReclaimable = savingsItems.reduce((sum, item) => sum + (item?.reclaimable_bytes ?? 0), 0);
    return {
      tables: tables.length,
      optimizeChunks: statements.filter((statement) => statement.action === "optimize").length,
      statements: statements.length,
      partitions: tables.reduce((total, table) => total + (table.stats?.total_partitions ?? 0), 0),
      sizeGb: tables.reduce((total, table) => total + (table.stats?.total_size_gb ?? 0), 0),
      totalSavings,
      totalReclaimable,
      precision: savingsItems[0]?.precision
    };
  }, [preview.data?.items, tables]);

  useEffect(() => {
    if (open && requestTables.length) preview.mutate();
  }, [open, engine, mode, requestTables.length]);

  return (
    <Drawer
      title="Plan preview"
      width={820}
      open={open}
      onClose={onClose}
      extra={
        <Space>
          <Segmented
            value={engine}
            onChange={(value) => setEngine(value as EngineType)}
            options={[
              { label: "Trino", value: "trino" },
              { label: "Spark", value: "spark_emr" }
            ]}
          />
          <Select
            value={mode}
            onChange={setMode}
            options={[
              { label: "Light", value: "light" },
              { label: "Standard", value: "standard" },
              { label: "Deep", value: "deep" },
              { label: "Manifest only", value: "manifest_only" }
            ]}
            style={{ width: 120 }}
          />
        </Space>
      }
    >
      {!requestTables.length ? (
        <Empty description="Select at least one table to preview." />
      ) : (
        <Space direction="vertical" size={12} className="full-width">
          <Space wrap>
            <CostPrecisionBadge precision={estimate.precision} />
            {estimate.totalSavings > 0 ? (
              <Typography.Text type="secondary">
                Planned savings use persisted scan data for the selected operations only.
              </Typography.Text>
            ) : null}
          </Space>
          <StatStrip
            items={[
              { label: "Tables", value: formatNumber(estimate.tables) },
              { label: "Datalake size", value: formatGb(estimate.sizeGb) },
              { label: "Partitions", value: formatNumber(estimate.partitions, true) },
              { label: "Optimize chunks", value: formatNumber(estimate.optimizeChunks) },
              { label: "Statements", value: formatNumber(estimate.statements) },
              { label: "Planned savings", value: formatCurrency(estimate.totalSavings) },
              { label: "Reclaimable", value: formatBytesGb(estimate.totalReclaimable) }
            ]}
          />
          <Tabs
            items={(preview.data?.items ?? []).map((item, tableIndex) => ({
              key: item.table_ref ?? `table-${tableIndex}`,
              label: item.table_ref,
              children: (
                <Space direction="vertical" size={12} className="full-width">
                  {item.savings ? (
                    <Descriptions size="small" column={2} bordered>
                      <Descriptions.Item label="Now">
                        {formatCurrency(item.savings.current_monthly_cost_usd)}
                      </Descriptions.Item>
                      <Descriptions.Item label="After maintenance">
                        {formatCurrency(item.savings.projected_monthly_cost_usd)}
                      </Descriptions.Item>
                      <Descriptions.Item label="Savings / mo">
                        {formatCurrency(item.savings.projected_savings_usd_month)}
                      </Descriptions.Item>
                      <Descriptions.Item label="Reclaimable">
                        {formatBytesGb(item.savings.reclaimable_bytes)}
                      </Descriptions.Item>
                      {item.savings.query_request_savings_usd_month ? (
                        <Descriptions.Item label="Query request savings" span={2}>
                          {formatCurrency(item.savings.query_request_savings_usd_month)}/mo (estimate, not storage)
                        </Descriptions.Item>
                      ) : null}
                    </Descriptions>
                  ) : null}
                  {item.savings ? (
                    <SavingsByOperation items={savingsToByOperation(item.savings)} />
                  ) : null}
                  {item.notes.length ? (
                    <Typography.Paragraph type="secondary">{item.notes.join(" · ")}</Typography.Paragraph>
                  ) : null}
                  <Tabs
                    tabPosition="left"
                    items={item.statements.map((statement, index) => ({
                      key: `${statement.action}-${index}`,
                      label: statement.action,
                      children: (
                        <div className="sql-block">
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() => {
                              navigator.clipboard.writeText(statement.sql);
                              message.success("SQL copied");
                            }}
                          >
                            Copy
                          </Button>
                          <pre>{statement.sql}</pre>
                        </div>
                      )
                    }))}
                  />
                </Space>
              )
            }))}
          />
        </Space>
      )}
      {preview.isPending ? <Typography.Text type="secondary">Rendering plan...</Typography.Text> : null}
      {preview.isError ? (
        <Typography.Text type="danger">Preview failed: {(preview.error as Error).message}</Typography.Text>
      ) : null}
    </Drawer>
  );
};
