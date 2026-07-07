import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Space, Switch, Tag, Typography, message } from "antd";
import { useState } from "react";
import {
  checkS3LogConfigLogging,
  ingestS3LogConfig,
  listS3LogConfigs,
  patchS3LogConfig,
  probeS3LogConfigTarget,
  provisionS3LogConfig,
  teardownS3LogConfig
} from "../api/client";
import type { S3LogConfigRead, UUID } from "../api/types";
import { ConfirmAction } from "./confirm";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";

interface S3LogConfigPanelProps {
  sourceId: UUID;
}

interface ManualFormValues {
  log_target_bucket?: string;
  log_target_prefix?: string;
  access_key_id?: string;
  secret_access_key?: string;
  region?: string;
}

const BucketCard = ({ sourceId, config }: { sourceId: UUID; config: S3LogConfigRead }) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ManualFormValues>();
  const [probeResult, setProbeResult] = useState<string | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["s3-log-configs", sourceId] });

  const check = useMutation({
    mutationFn: () => checkS3LogConfigLogging(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) {
        message.error(result.error);
      } else {
        message.success(`Logging status: ${result.logging_status}`);
      }
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const saveManual = useMutation({
    mutationFn: (values: ManualFormValues) =>
      patchS3LogConfig(api, sourceId, config.bucket, {
        log_target_bucket: values.log_target_bucket || undefined,
        log_target_prefix: values.log_target_prefix || undefined,
        access_key_id: values.access_key_id || undefined,
        secret_access_key: values.secret_access_key || undefined,
        region: values.region || undefined,
        manual_override: true
      }),
    onSuccess: () => {
      message.success("Log target saved");
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const probe = useMutation({
    mutationFn: () => probeS3LogConfigTarget(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) {
        setProbeResult(`Could not check this path: ${result.error}`);
      } else if (result.files_found > 0) {
        setProbeResult(`Found ${result.files_found}${result.truncated ? "+" : ""} log files at this path`);
      } else {
        setProbeResult("Nothing found yet — double check the path, or wait for logs to arrive");
      }
    },
    onError: (error) => setProbeResult((error as Error).message)
  });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => patchS3LogConfig(api, sourceId, config.bucket, { enabled }),
    onSuccess: () => {
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const ingestNow = useMutation({
    mutationFn: () => ingestS3LogConfig(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) {
        message.error(result.error);
      } else {
        message.success(`Ingested ${result.rows_ingested} row(s), matched ${result.tables_matched} table(s)`);
      }
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const enableLogging = useMutation({
    mutationFn: () => provisionS3LogConfig(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.ok) {
        message.success(`Logging enabled, delivering to ${result.log_target_bucket}`);
      } else {
        message.error(result.error ?? "Could not enable logging");
      }
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const removeTracking = useMutation({
    mutationFn: () => teardownS3LogConfig(api, sourceId, config.bucket, { revert_policy: true }),
    onSuccess: (result) => {
      if (result.warnings.length) {
        message.warning(result.warnings.join("; "));
      } else {
        message.success("Bucket tracking removed");
      }
      invalidate();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const hasTarget = Boolean(config.log_target_bucket);
  const awaitingFirstDelivery = hasTarget && config.enabled && !config.last_ingested_at;

  return (
    <Card
      size="small"
      title={config.bucket}
      extra={
        <Space size={8}>
          <Tag color={config.logging_status === "enabled" ? "green" : config.logging_status === "disabled" ? "gold" : "default"}>
            {config.logging_status}
          </Tag>
          <Switch
            size="small"
            checked={config.enabled}
            disabled={!hasTarget || toggleEnabled.isPending}
            onChange={(checked) => toggleEnabled.mutate(checked)}
          />
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <Space orientation="vertical" size={10} className="full-width">
        <Space wrap size={12}>
          <Button size="small" loading={check.isPending} onClick={() => check.mutate()}>
            Check logging status
          </Button>
          {config.enabled && hasTarget ? (
            <Button size="small" loading={ingestNow.isPending} onClick={() => ingestNow.mutate()}>
              Ingest now
            </Button>
          ) : null}
          <ConfirmAction
            title="Remove tracking for this bucket?"
            description="Removes Cryo's config for this bucket and, if Cryo turned logging on itself, offers to revert the bucket-policy statement it added. Logging itself is left running on AWS unless Cryo added it."
            onConfirm={() => removeTracking.mutate()}
            okText="Remove"
          >
            <Button size="small" danger loading={removeTracking.isPending}>
              Remove tracking
            </Button>
          </ConfirmAction>
        </Space>

        {config.last_error ? (
          <Alert
            type="error"
            showIcon
            title="Last check failed"
            description={config.last_error}
          />
        ) : null}

        {config.logging_status === "enabled" && hasTarget ? (
          <Typography.Text type="secondary">
            Logging target: {config.log_target_bucket}/{config.log_target_prefix ?? ""}
            {config.last_ingested_date ? ` — last ingested ${config.last_ingested_date}` : ""}
          </Typography.Text>
        ) : null}

        {awaitingFirstDelivery ? (
          <Alert
            type="info"
            showIcon
            title="Logging configured, waiting for first delivery"
            description="This usually takes a few hours after logging is turned on."
          />
        ) : null}

        {config.logging_status !== "enabled" ? (
          <>
            <Typography.Text type="secondary">
              Cryo can see request activity for this bucket once server access logging is turned
              on and pointed at a log target it can read. Turn it on with one click if Cryo's
              credentials allow it, enter the target manually below, or ask whoever manages this
              bucket's logging for the path.
            </Typography.Text>
            <ConfirmAction
              title="Turn on request-cost logging for this bucket?"
              description={`Cryo will create or reuse a log target bucket ("${config.bucket}-cryo-access-logs" unless one already exists), grant it permission to receive delivery, and turn on S3 server access logging for "${config.bucket}". Requires the optional write-permission tier -- if Cryo's credentials don't have it, this will fail with a clear error and you can use manual entry below instead.`}
              onConfirm={() => enableLogging.mutate()}
              okText="Enable logging"
            >
              <Button size="small" type="primary" loading={enableLogging.isPending}>
                Enable logging
              </Button>
            </ConfirmAction>
            <Form
              form={form}
              layout="vertical"
              initialValues={{
                log_target_bucket: config.log_target_bucket ?? "",
                log_target_prefix: config.log_target_prefix ?? "",
                access_key_id: config.access_key_id ?? "",
                region: config.region ?? ""
              }}
              onFinish={(values) => saveManual.mutate(values)}
            >
              <Space size={12} className="full-width" style={{ display: "flex" }}>
                <Form.Item name="log_target_bucket" label="Log target bucket" style={{ flex: 1 }}>
                  <Input placeholder="my-access-logs" autoComplete="off" />
                </Form.Item>
                <Form.Item name="log_target_prefix" label="Log target prefix" style={{ flex: 1 }}>
                  <Input placeholder="logs/my-bucket/" autoComplete="off" />
                </Form.Item>
              </Space>
              <Space size={12} className="full-width" style={{ display: "flex" }}>
                <Form.Item name="region" label="Region" style={{ flex: 1 }}>
                  <Input placeholder="us-east-1" autoComplete="off" />
                </Form.Item>
                <Form.Item name="access_key_id" label="Access key ID (optional)" style={{ flex: 1 }}>
                  <Input placeholder="AKIA..." autoComplete="off" />
                </Form.Item>
              </Space>
              <Form.Item
                name="secret_access_key"
                label="Secret access key (optional, stored encrypted)"
              >
                <Input.Password placeholder="Leave blank to keep existing" autoComplete="off" />
              </Form.Item>
              <Space wrap>
                <Button size="small" type="primary" htmlType="submit" loading={saveManual.isPending}>
                  Save target
                </Button>
                <Button size="small" loading={probe.isPending} onClick={() => probe.mutate()}>
                  Validate path
                </Button>
              </Space>
            </Form>
            {probeResult ? (
              <Typography.Text type="secondary">{probeResult}</Typography.Text>
            ) : null}
          </>
        ) : null}
      </Space>
    </Card>
  );
};

export const S3LogConfigPanel = ({ sourceId }: S3LogConfigPanelProps) => {
  const { api } = useAuth();
  const configs = useQuery({
    queryKey: ["s3-log-configs", sourceId],
    queryFn: () => listS3LogConfigs(api, sourceId),
    retry: false
  });

  if (configs.isLoading) {
    return <Typography.Text type="secondary">Loading buckets…</Typography.Text>;
  }

  if (!configs.data || configs.data.length === 0) {
    return (
      <Typography.Text type="secondary">
        No buckets discovered yet — run a scan on this lakehouse first. Bucket discovery uses the
        S3 location recorded for each scanned table.
      </Typography.Text>
    );
  }

  return (
    <Space orientation="vertical" className="full-width" size={0}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
        Turn this on to see per-table GET/PUT/LIST request costs, not just storage costs.
      </Typography.Paragraph>
      {configs.data.map((config) => (
        <BucketCard key={config.bucket} sourceId={sourceId} config={config} />
      ))}
    </Space>
  );
};
