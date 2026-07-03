import { CopyOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Button, Drawer, Empty, Segmented, Select, Space, Tabs, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { previewMaintenance } from "../api/client";
import type { EngineType, JsonObject, Mode, TableRead } from "../api/types";
import { StatStrip } from "./common";
import { useAuth } from "../lib/auth";
import { formatGb, formatNumber } from "../lib/format";

interface Props {
  open: boolean;
  tables: TableRead[];
  onClose: () => void;
  params?: JsonObject;
}

export const PlanPreview = ({ open, tables, onClose, params = {} }: Props) => {
  const { api } = useAuth();
  const [engine, setEngine] = useState<EngineType>("trino");
  const [mode, setMode] = useState<Mode>("standard");

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
    return {
      tables: tables.length,
      optimizeChunks: statements.filter((statement) => statement.action === "optimize").length,
      statements: statements.length,
      partitions: tables.reduce((total, table) => total + (table.stats?.total_partitions ?? 0), 0),
      sizeGb: tables.reduce((total, table) => total + (table.stats?.total_size_gb ?? 0), 0)
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
              { label: "Deep", value: "deep" }
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
          <Typography.Paragraph type="secondary">
            Projected savings will appear here once `/maintenance/preview` returns a SavingsProjection.
          </Typography.Paragraph>
          <StatStrip
            items={[
              { label: "Tables", value: formatNumber(estimate.tables) },
              { label: "Fleet size", value: formatGb(estimate.sizeGb) },
              { label: "Partitions", value: formatNumber(estimate.partitions, true) },
              { label: "Optimize chunks", value: formatNumber(estimate.optimizeChunks) },
              { label: "Statements", value: formatNumber(estimate.statements) }
            ]}
          />
          <Tabs
            items={(preview.data?.items ?? []).map((item) => ({
              key: item.table_ref,
              label: item.table_ref,
              children: (
                <Space direction="vertical" size={12} className="full-width">
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
