import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Collapse, Divider, Form, InputNumber, Select, Space, Switch, Typography } from "antd";
import type { DestinationRead } from "../../api/types";
import { HintLabel } from "../common";
import { presetFieldRules } from "../../lib/presetForm";
import { ROUTING_RULE_FIELDS, ROUTING_RULE_OPERATORS } from "../../lib/presetOptions";

export const PresetConfigFields = ({ destinations }: { destinations: DestinationRead[] }) => {
  const destinationOptions = destinations.map((item) => ({
    label: item.name,
    value: item.name
  }));

  return (
    <>
      <Divider titlePlacement="left" plain>
        Optimization planning
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Controls when Cryo plans optimize work and how large each chunk can be.
      </Typography.Paragraph>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="min_small_files"
          label={
            <HintLabel
              label="Min small files"
              tooltip="Skip optimize unless a table or partition has at least this many small files."
            />
          }
          rules={[presetFieldRules.nonNegative("Min small files")]}
        >
          <InputNumber min={0} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item
          name="chunk_size_gb"
          label={
            <HintLabel
              label="Chunk size (GB)"
              tooltip="Maximum data Cryo places in one optimize chunk. Tables below this size may run a full-table optimize."
            />
          }
          rules={[presetFieldRules.positive("Chunk size")]}
        >
          <InputNumber min={1} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item
          name="max_partitions_per_chunk"
          label={
            <HintLabel
              label="Max partitions / chunk"
              tooltip="Caps partitions per optimize statement to avoid Trino FilterNode complexity errors."
            />
          }
          rules={[presetFieldRules.positive("Max partitions per chunk")]}
        >
          <InputNumber min={1} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="max_chunks"
          label={
            <HintLabel
              label="Max chunks"
              tooltip="Optional cap on optimize chunks generated for one table. Leave empty for no cap."
            />
          }
          rules={[presetFieldRules.positive("Max chunks")]}
        >
          <InputNumber min={1} placeholder="No cap" style={{ width: 140 }} />
        </Form.Item>
      </Space>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="delete_ratio_trigger"
          label={
            <HintLabel
              label="Delete ratio trigger (%)"
              tooltip="When delete-file percentage reaches this threshold, optimize can run even below the small-file threshold. 0 disables."
            />
          }
          rules={[presetFieldRules.deleteRatio]}
        >
          <InputNumber min={0} max={100} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="partition_write_idle_hours"
          label={
            <HintLabel
              label="Partition idle hours"
              tooltip="Skip partitions written within this many hours. 0 disables hot-partition gating."
            />
          }
          rules={[presetFieldRules.nonNegative("Partition idle hours")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="file_size_mb"
          label={
            <HintLabel
              label="Target file size (MB)"
              tooltip="Target output file size passed to engine optimize statements."
            />
          }
          rules={[presetFieldRules.positive("Target file size")]}
        >
          <InputNumber min={1} style={{ width: 180 }} />
        </Form.Item>
      </Space>

      <Divider titlePlacement="left" plain>
        Snapshot and orphan retention
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Retention windows for expire_snapshots and remove_orphan_files operations.
      </Typography.Paragraph>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="retention_days"
          label={
            <HintLabel
              label="Snapshot retention (days)"
              tooltip="Snapshots older than this window are eligible for expiry."
            />
          }
          rules={[presetFieldRules.positive("Snapshot retention")]}
        >
          <InputNumber min={1} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="retain_last"
          label={
            <HintLabel
              label="Retain last snapshots"
              tooltip="Minimum number of snapshots to keep after expiry."
            />
          }
          rules={[presetFieldRules.positive("Retain last snapshots")]}
        >
          <InputNumber min={1} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="orphan_retention_days"
          label={
            <HintLabel
              label="Orphan retention (days)"
              tooltip="Minimum orphan file age before removal. Leave empty to use snapshot retention."
            />
          }
          rules={[presetFieldRules.positive("Orphan retention")]}
        >
          <InputNumber min={1} placeholder="Use snapshot retention" style={{ width: 220 }} />
        </Form.Item>
      </Space>

      <Divider titlePlacement="left" plain>
        Statistics
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Optional Puffin statistics generation for standard and deep modes.
      </Typography.Paragraph>
      <Form.Item
        name="compute_statistics"
        label={
          <HintLabel
            label="Compute statistics"
            tooltip="Include compute_statistics when the mode and engine support it."
          />
        }
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="stats_columns"
          label={
            <HintLabel
              label="Stats columns"
              tooltip="Columns to include in generated statistics. Leave empty for engine defaults."
            />
          }
          style={{ minWidth: 280, flex: 1 }}
        >
          <Select mode="tags" placeholder="column_a, column_b" tokenSeparators={[","]} />
        </Form.Item>
        <Form.Item
          name="bloom_filter_columns"
          label={
            <HintLabel
              label="Bloom filter columns"
              tooltip="Columns to include in bloom filter statistics when supported."
            />
          }
          style={{ minWidth: 280, flex: 1 }}
        >
          <Select mode="tags" placeholder="column_a, column_b" tokenSeparators={[","]} />
        </Form.Item>
      </Space>
      <Form.Item
        name="stats_compute_ndv"
        label={
          <HintLabel
            label="Compute NDV"
            tooltip="Whether to compute number-of-distinct-values statistics when supported."
          />
        }
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>

      <Divider titlePlacement="left" plain>
        Scheduling guardrails
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Admission controls applied when scheduling and running maintenance.
      </Typography.Paragraph>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="cooldown_hours"
          label={
            <HintLabel
              label="Cooldown (hours)"
              tooltip="Hours before the same table can be maintained again. 0 disables cooldown."
            />
          }
          rules={[presetFieldRules.nonNegative("Cooldown")]}
        >
          <InputNumber min={0} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item
          name="deadline_minutes"
          label={
            <HintLabel
              label="Run deadline (minutes)"
              tooltip="Optional scheduler deadline for a preset run. Leave empty for no deadline."
            />
          }
          rules={[presetFieldRules.positive("Run deadline")]}
        >
          <InputNumber min={1} placeholder="No deadline" style={{ width: 180 }} />
        </Form.Item>
      </Space>

      <Divider titlePlacement="left" plain>
        SLA thresholds
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Dashboard and alert thresholds for freshness, maintenance age, and query latency.
      </Typography.Paragraph>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="sla_freshness_hours"
          label={
            <HintLabel
              label="Freshness SLA (hours)"
              tooltip="Alert when table data freshness exceeds this threshold."
            />
          }
          rules={[presetFieldRules.nonNegative("Freshness SLA")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="sla_maintenance_days"
          label={
            <HintLabel
              label="Maintenance SLA (days)"
              tooltip="Alert when a table has not been maintained within this window."
            />
          }
          rules={[presetFieldRules.nonNegative("Maintenance SLA")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="sla_p95_latency_ms"
          label={
            <HintLabel
              label="P95 latency SLA (ms)"
              tooltip="Alert when observed query latency exceeds this threshold."
            />
          }
          rules={[presetFieldRules.nonNegative("P95 latency SLA")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
      </Space>

      <Divider titlePlacement="left" plain>
        Adaptive routing
      </Divider>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -8 }}>
        Score candidate engines instead of always using the preset destination.
      </Typography.Paragraph>
      <Form.Item
        name="adaptive_routing"
        label={
          <HintLabel
            label="Adaptive routing"
            tooltip="Enable destination scoring using table size, engine preferences, and catalog latency."
          />
        }
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Form.Item
        name="engine_pool"
        label={
          <HintLabel
            label="Engine pool"
            tooltip="Destinations eligible for adaptive routing. Leave empty to consider all destinations."
          />
        }
      >
        <Select
          mode="multiple"
          allowClear
          placeholder="All destinations"
          options={destinationOptions}
          style={{ width: "100%" }}
        />
      </Form.Item>
      <Space className="full-width" size={12} wrap>
        <Form.Item
          name="spark_min_gb"
          label={
            <HintLabel
              label="Prefer Spark above (GB)"
              tooltip="Bias adaptive routing toward Spark when table size exceeds this threshold."
            />
          }
          rules={[presetFieldRules.nonNegative("Spark threshold")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="native_min_gb"
          label={
            <HintLabel
              label="Prefer native above (GB)"
              tooltip="Bias adaptive routing toward the native engine above this table size."
            />
          }
          rules={[presetFieldRules.nonNegative("Native threshold")]}
        >
          <InputNumber min={0} style={{ width: 180 }} />
        </Form.Item>
        <Form.Item
          name="latency_p99_ms"
          label={
            <HintLabel
              label="Catalog latency threshold (ms)"
              tooltip="Catalog p99 latency used when scoring destinations."
            />
          }
          rules={[presetFieldRules.nonNegative("Catalog latency threshold")]}
        >
          <InputNumber min={0} style={{ width: 220 }} />
        </Form.Item>
      </Space>

      <Collapse
        ghost
        items={[
          {
            key: "routing-rules",
            label: "Routing rules (optional)",
            children: (
              <>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
                  Route tables to a different destination when a predicate matches. Rules are
                  evaluated in order; the first match wins.
                </Typography.Paragraph>
                <Form.List name="routing_rules" rules={[presetFieldRules.routingRules]}>
                  {(fields, { add, remove }, { errors }) => (
                    <>
                      {fields.map((field) => (
                        <Space key={field.key} align="start" wrap className="full-width">
                          <Form.Item
                            {...field}
                            name={[field.name, "field"]}
                            rules={[{ required: true, message: "Field required" }]}
                          >
                            <Select
                              placeholder="Field"
                              options={[...ROUTING_RULE_FIELDS]}
                              style={{ width: 180 }}
                            />
                          </Form.Item>
                          <Form.Item
                            {...field}
                            name={[field.name, "op"]}
                            initialValue=">"
                            rules={[{ required: true, message: "Operator required" }]}
                          >
                            <Select
                              placeholder="Op"
                              options={[...ROUTING_RULE_OPERATORS]}
                              style={{ width: 90 }}
                            />
                          </Form.Item>
                          <Form.Item
                            {...field}
                            name={[field.name, "value"]}
                            rules={[{ required: true, message: "Value required" }]}
                          >
                            <InputNumber placeholder="Value" style={{ width: 120 }} />
                          </Form.Item>
                          <Form.Item
                            {...field}
                            name={[field.name, "destination"]}
                            rules={[{ required: true, message: "Destination required" }]}
                          >
                            <Select
                              placeholder="Destination"
                              options={destinationOptions}
                              style={{ width: 180 }}
                            />
                          </Form.Item>
                          <Button
                            type="text"
                            icon={<MinusCircleOutlined />}
                            aria-label="Remove routing rule"
                            onClick={() => remove(field.name)}
                          />
                        </Space>
                      ))}
                      <Form.ErrorList errors={errors} />
                      <Button type="dashed" icon={<PlusOutlined />} onClick={() => add()}>
                        Add routing rule
                      </Button>
                    </>
                  )}
                </Form.List>
              </>
            )
          }
        ]}
      />
    </>
  );
};
