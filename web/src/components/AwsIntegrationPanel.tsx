import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Form, Input, Space, Switch, Typography, message } from "antd";
import {
  createAwsIntegration,
  deleteAwsIntegration,
  getAwsIntegration,
  getAwsUsage,
  patchAwsIntegration,
  testAwsIntegration
} from "../api/client";
import type { AWSIntegrationWrite, UUID } from "../api/types";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatTime } from "../lib/format";

interface AwsIntegrationPanelProps {
  sourceId: UUID;
}

export const AwsIntegrationPanel = ({ sourceId }: AwsIntegrationPanelProps) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<AWSIntegrationWrite & { secret_access_key?: string }>();

  const integration = useQuery({
    queryKey: ["aws-integration", sourceId],
    queryFn: () => getAwsIntegration(api, sourceId),
    retry: false
  });

  const usage = useQuery({
    queryKey: ["aws-usage", sourceId],
    queryFn: () => getAwsUsage(api, sourceId),
    enabled: Boolean(integration.data?.enabled),
    retry: false
  });

  const save = useMutation({
    mutationFn: (values: AWSIntegrationWrite) => {
      if (integration.data) {
        return patchAwsIntegration(api, sourceId, values);
      }
      return createAwsIntegration(api, sourceId, values);
    },
    onSuccess: () => {
      message.success("AWS integration saved");
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const remove = useMutation({
    mutationFn: () => deleteAwsIntegration(api, sourceId),
    onSuccess: () => {
      message.success("AWS integration removed");
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const test = useMutation({
    mutationFn: () => testAwsIntegration(api, sourceId),
    onSuccess: (result) => {
      if (result.ok) {
        message.success(`Connected · account ${result.account_id ?? "?"}`);
      } else {
        message.error(result.error ?? "Connection failed");
      }
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const existing = integration.data;

  return (
    <Space direction="vertical" className="full-width" size={12}>
      <Alert
        type="info"
        showIcon
        message="Free-tier AWS metrics only"
        description="Uses S3 CloudWatch daily storage metrics and the Price List API — no LIST, no charges."
      />

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          bucket: existing?.bucket ?? "",
          region: existing?.region ?? "us-east-1",
          access_key_id: existing?.access_key_id ?? "",
          enabled: existing?.enabled ?? true
        }}
        key={existing?.id ?? "new"}
        onFinish={(values) => {
          const body: AWSIntegrationWrite = {
            bucket: values.bucket,
            region: values.region,
            access_key_id: values.access_key_id || undefined,
            enabled: values.enabled
          };
          if (values.secret_access_key) {
            body.secret_access_key = values.secret_access_key;
          }
          save.mutate(body);
        }}
      >
        <Form.Item name="bucket" label="S3 bucket" rules={[{ required: true }]}>
          <Input placeholder="my-iceberg-bucket" />
        </Form.Item>
        <Form.Item name="region" label="Region" rules={[{ required: true }]}>
          <Input placeholder="ap-south-1" />
        </Form.Item>
        <Form.Item name="access_key_id" label="Access key ID (optional)">
          <Input placeholder="Uses IRSA / default chain if blank" autoComplete="off" />
        </Form.Item>
        <Form.Item name="secret_access_key" label="Secret access key (optional)">
          <Input.Password placeholder={existing ? "Leave blank to keep existing" : ""} autoComplete="off" />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {existing ? "Update" : "Connect"}
          </Button>
          {existing ? (
            <>
              <Button loading={test.isPending} onClick={() => test.mutate()}>
                Test STS
              </Button>
              <Button danger loading={remove.isPending} onClick={() => remove.mutate()}>
                Remove
              </Button>
            </>
          ) : null}
        </Space>
      </Form>

      {existing ? (
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="Last tested">
            {existing.last_tested_at ? formatTime(existing.last_tested_at) : "Never"}
          </Descriptions.Item>
          <Descriptions.Item label="Last test OK">
            {existing.last_test_ok == null ? "—" : existing.last_test_ok ? "Yes" : "No"}
          </Descriptions.Item>
        </Descriptions>
      ) : null}

      {usage.data ? (
        <Descriptions size="small" column={1} title="Live bucket metrics" bordered>
          <Descriptions.Item label="Measured">
            {usage.data.measured_at ? formatTime(usage.data.measured_at) : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Total size">{formatBytesGb(usage.data.total_size_bytes)}</Descriptions.Item>
          <Descriptions.Item label="Objects">{usage.data.object_count?.toLocaleString() ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Price">
            {usage.data.price_per_gb_month != null
              ? `${formatCurrency(usage.data.price_per_gb_month)}/GB-mo (${usage.data.price_source ?? "AWS"})`
              : "—"}
          </Descriptions.Item>
        </Descriptions>
      ) : integration.data?.enabled && usage.isError ? (
        <Typography.Text type="secondary">Usage metrics unavailable — check credentials and bucket.</Typography.Text>
      ) : null}
    </Space>
  );
};
