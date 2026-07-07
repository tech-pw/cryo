import { Alert, Form, Input, InputNumber, Radio, Space, Switch, Typography } from "antd";
import { useWatch } from "antd/es/form/Form";

const { Text, Paragraph } = Typography;

export interface QueryLogIngestionFieldsProps {
  /** When the engine is Spark EMR, ingestion is unsupported and the section collapses. */
  engine: "trino" | "spark_emr";
}

/**
 * Sub-form for the per-destination query-log ingestion block. Lives inside the Destination
 * drawer on Connections. Sub-form values live on flat ``query_log_*`` form fields; the
 * parent ``onFinish`` handler merges them into the ``query_log_config`` JSONB block sent
 * to the backend.
 *
 * Behaviour:
 * - Hidden entirely for Spark EMR (the event-log ingest path isn't built yet).
 * - When the toggle is OFF the rest of the section is collapsed (still rendered so existing
 *   values don't get nuked by a temporary disable).
 * - When ``mode`` is ``read_through`` the Trino-specific fields (pull interval, watermark
 *   overlap, multi-coordinator list) are hidden and the user is asked for the external
 *   table identifier.
 * - When the user picks a very low retention (≤ 1 day) the cadence defaults collapse to
 *   60s pull + 5 min overlap and we surface a warning that a missed curator tick at that
 *   cadence means lost data.
 */
export const QueryLogIngestionFields = ({ engine }: QueryLogIngestionFieldsProps) => {
  const enabled = useWatch("query_log_enabled", undefined as never);
  const mode = useWatch("query_log_mode", undefined as never);
  const retentionDays = useWatch("query_log_retention_days", undefined as never);

  if (engine !== "trino") {
    return (
      <Alert
        type="info"
        showIcon
        style={{ marginTop: 8 }}
        message="Query-log ingestion is Trino-only in this build."
        description="Spark history ingestion lands separately -- this destination's maintenance path doesn't need it."
      />
    );
  }

  return (
    <div style={{ marginTop: 12, padding: 12, border: "1px solid var(--line)", borderRadius: 8 }}>
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Text strong>Query log ingestion</Text>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          Stream this destination's queries into the Cryo archive so the Optimize page
          (layout recommendations, partition evolution) and the Governor (variance-aware
          QPP) have real traffic to learn from. Without this, recommendations can only be
          derived from manifest metadata, not from what users actually run.
        </Paragraph>

        <Form.Item
          name="query_log_enabled"
          label="Enable query log ingestion"
          valuePropName="checked"
          style={{ marginBottom: 0 }}
        >
          <Switch />
        </Form.Item>

        {enabled ? (
          <>
            <Form.Item name="query_log_mode" label="Mode" style={{ marginBottom: 8 }}>
              <Radio.Group>
                <Space direction="vertical">
                  <Radio value="trino_system_runtime">
                    <Text strong>Poll Trino system.runtime.queries</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      Curator pulls recent queries from one or more Trino coordinators and
                      archives them. Recommended for most deployments.
                    </Text>
                  </Radio>
                  <Radio value="read_through">
                    <Text strong>Read from existing Iceberg query log</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      You already run a Trino event listener that writes every completed
                      query to an Iceberg table. Cryo reads that table directly; nothing
                      extra to archive. Pick this if you want full control over retention.
                    </Text>
                  </Radio>
                </Space>
              </Radio.Group>
            </Form.Item>

            {mode === "read_through" ? (
              <Form.Item
                name="query_log_external_table"
                label="External Iceberg table (namespace.table)"
                tooltip="Must be readable via this destination's catalog connection. Schema: needs a query text column and a timestamp column."
              >
                <Input placeholder="logging.trino_query_log" />
              </Form.Item>
            ) : null}

            {mode === "trino_system_runtime" ? (
              <>
                <Form.Item
                  name="query_log_telemetry_coordinators"
                  label="Telemetry coordinators (one host:port per line)"
                  tooltip="Leave blank to use the destination's primary coordinator. List every active coordinator for multi-coordinator clusters -- the curator will poll every one and dedupe by query_id."
                >
                  <Input.TextArea
                    rows={3}
                    placeholder={"http://trino-coord-1:8080\nhttp://trino-coord-2:8080"}
                  />
                </Form.Item>

                <Space.Compact block>
                  <Form.Item
                    name="query_log_pull_interval_seconds"
                    label="Pull interval (seconds)"
                    style={{ flex: 1, marginRight: 8 }}
                    tooltip="How often the curator polls Trino. Below 1-day retention this is auto-set to 60s."
                  >
                    <InputNumber min={10} max={3600} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item
                    name="query_log_watermark_overlap_seconds"
                    label="Overlap (seconds)"
                    style={{ flex: 1 }}
                    tooltip="How far back the curator re-asks Trino for queries. Must be wider than Trino's query.min-expire-age (default 15 min) so a missed tick never loses data."
                  >
                    <InputNumber min={300} max={86400} style={{ width: "100%" }} />
                  </Form.Item>
                </Space.Compact>
              </>
            ) : null}

            <Form.Item
              name="query_log_sample_rate"
              label="Archive sample rate (0.0 - 1.0)"
              tooltip="Deterministic per query_id. 1.0 = archive every query; 0.01 = sample 1%. Apply when the destination is too chatty to keep up with full archival."
            >
              <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
            </Form.Item>

            <Form.Item
              name="query_log_retention_days"
              label="Iceberg retention (days, 1-365)"
              tooltip="Snapshots older than this are expired on every curator tick. NULL = no auto-expiry (you manage retention)."
            >
              <InputNumber min={1} max={365} style={{ width: "100%" }} />
            </Form.Item>

            {typeof retentionDays === "number" && retentionDays <= 1 ? (
              <Alert
                type="warning"
                showIcon
                message="Very short retention"
                description="At ≤ 1 day, the curator pulls every 60s with a 5-min overlap. A missed curator tick (process restart, network blip) means lost query data. Don't go below 1 day unless you also have a Trino event listener as a backup."
              />
            ) : null}
          </>
        ) : null}
      </Space>
    </div>
  );
};