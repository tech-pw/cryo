import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import { CopyOutlined } from "@ant-design/icons";
import {
  createAwsIntegration,
  deleteAwsIntegration,
  getAwsIntegration,
  getAwsUsage,
  patchAwsIntegration,
  testAwsIntegration
} from "../api/client";
import type { AWSIntegrationRead, AWSIntegrationWrite, UUID } from "../api/types";
import { ConfirmAction } from "./confirm";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatTime } from "../lib/format";

interface AwsIntegrationPanelProps {
  sourceId: UUID;
  /** When true, render in a denser card layout (used inside a grid of integrations). */
  compact?: boolean;
}

// Real AWS regions only -- this integration calls CloudWatch/Pricing directly, so unlike the
// lakehouse S3 config (which also has to support MinIO/S3-compatible endpoints), there's no
// legitimate custom value here. A strict Select prevents typos entirely rather than just
// suggesting corrections.
const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "af-south-1",
  "ap-east-1",
  "ap-south-1",
  "ap-south-2",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-southeast-3",
  "ap-southeast-4",
  "ap-northeast-1",
  "ap-northeast-2",
  "ap-northeast-3",
  "ca-central-1",
  "eu-central-1",
  "eu-central-2",
  "eu-west-1",
  "eu-west-2",
  "eu-west-3",
  "eu-north-1",
  "eu-south-1",
  "eu-south-2",
  "me-central-1",
  "me-south-1",
  "sa-east-1"
].map((region) => ({ value: region, label: region }));

const formatRelative = (value?: string | null): string => {
  if (!value) return "Never";
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return "Never";
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const min = Math.round(deltaSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
};

export const AwsIntegrationPanel = ({ sourceId, compact = false }: AwsIntegrationPanelProps) => {
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
    retry: false,
    refetchInterval: 60_000
  });

  const save = useMutation({
    mutationFn: (values: AWSIntegrationWrite) => {
      if (integration.data) {
        return patchAwsIntegration(api, sourceId, values);
      }
      return createAwsIntegration(api, sourceId, values);
    },
    onSuccess: () => {
      message.success(integration.data ? "AWS integration updated" : "AWS integration connected");
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const remove = useMutation({
    mutationFn: () => deleteAwsIntegration(api, sourceId),
    onSuccess: () => {
      message.success("AWS integration removed");
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const test = useMutation({
    mutationFn: () => testAwsIntegration(api, sourceId),
    onSuccess: (result) => {
      if (result.ok) {
        message.success(`Connected to AWS account ${result.account_id ?? "?"}`);
      } else {
        message.error(result.error ?? "Connection failed");
      }
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const existing: AWSIntegrationRead | undefined = integration.data;

  if (integration.isError && !integration.isLoading) {
    const status = (integration.error as { status?: number } | null)?.status;
    if (status === 404) {
      return (
        <Space orientation="vertical" size={10} className="full-width">
          <Typography.Text type="secondary">
            Connect this lakehouse's S3 bucket to Cryo so we can pull free CloudWatch storage metrics
            and real regional pricing — no extra AWS charges.
          </Typography.Text>
        </Space>
      );
    }
  }

  return (
    <Space orientation="vertical" className="full-width" size={12}>
      {!compact ? (
        <Alert
          type="info"
          showIcon
          title="Free-tier AWS metrics only"
          description="Uses S3 CloudWatch daily storage metrics and the Price List API — no LIST, no charges."
        />
      ) : null}

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
        <Form.Item
          name="bucket"
          label="S3 bucket"
          rules={[{ required: true, message: "Bucket name is required" }]}
        >
          <Input placeholder="my-iceberg-bucket" autoComplete="off" />
        </Form.Item>
        <Form.Item name="region" label="Region" rules={[{ required: true }]}>
          <Select showSearch options={AWS_REGIONS} placeholder="ap-south-1" popupMatchSelectWidth={220} />
        </Form.Item>
        <Form.Item
          name="access_key_id"
          label={
            <Tooltip title="Leave blank to use IRSA, instance profile, or ~/.aws/credentials.">
              Access key ID (optional) <Typography.Text type="secondary">ⓘ</Typography.Text>
            </Tooltip>
          }
        >
          <Input placeholder="AKIA..." autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="secret_access_key"
          label={
            <Tooltip
              title={
                existing
                  ? "Stored encrypted at rest. Leave blank to keep the existing secret."
                  : "Stored encrypted at rest. Leave blank to use the default AWS credential chain."
              }
            >
              Secret access key <Typography.Text type="secondary">ⓘ</Typography.Text>
            </Tooltip>
          }
        >
          <Input.Password
            placeholder={existing ? "Leave blank to keep existing" : "Optional with IRSA"}
            autoComplete="off"
          />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {existing ? "Save changes" : "Connect"}
          </Button>
          {existing ? (
            <>
              <Button loading={test.isPending} onClick={() => test.mutate()}>
                Test connection
              </Button>
              <ConfirmAction
                title="Remove AWS integration?"
                description="CloudWatch cost metrics for this source will stop updating. You can reconnect any time."
                onConfirm={() => remove.mutate()}
                okText="Remove"
              >
                <Button danger loading={remove.isPending}>
                  Remove
                </Button>
              </ConfirmAction>
            </>
          ) : null}
        </Space>
      </Form>

      {existing ? (
        <Space size={16} wrap>
          <Tag color={existing.enabled ? "green" : "default"}>
            {existing.enabled ? "Enabled" : "Disabled"}
          </Tag>
          <Typography.Text type="secondary">
            Last tested: {formatRelative(existing.last_tested_at)}
            {existing.last_tested_at ? ` (${formatTime(existing.last_tested_at)})` : ""}
          </Typography.Text>
          {existing.last_test_ok === true ? <Tag color="green">OK</Tag> : null}
          {existing.last_test_ok === false ? <Tag color="red">Failed</Tag> : null}
        </Space>
      ) : null}

      {test.data?.ok && test.data.arn ? (
        <Space size={6} align="center">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {test.data.arn}
          </Typography.Text>
          <Tooltip title="Copy ARN">
            <Button
              size="small"
              type="text"
              icon={<CopyOutlined />}
              onClick={() => {
                void navigator.clipboard.writeText(test.data?.arn ?? "").then(
                  () => message.success("ARN copied"),
                  () => message.error("Could not copy to clipboard")
                );
              }}
            />
          </Tooltip>
        </Space>
      ) : null}

      {existing?.enabled ? (
        usage.isLoading ? (
          <Typography.Text type="secondary">Loading bucket metrics…</Typography.Text>
        ) : usage.data ? (
          <Space size={24} wrap>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Bucket size
              </Typography.Text>
              <div>
                <Typography.Text strong style={{ fontSize: 16 }}>
                  {formatBytesGb(usage.data.total_size_bytes)}
                </Typography.Text>
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Objects
              </Typography.Text>
              <div>
                <Typography.Text strong style={{ fontSize: 16 }}>
                  {usage.data.object_count?.toLocaleString() ?? "—"}
                </Typography.Text>
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                $/GB-month
              </Typography.Text>
              <div>
                <Typography.Text strong style={{ fontSize: 16 }}>
                  {usage.data.price_per_gb_month != null
                    ? formatCurrency(usage.data.price_per_gb_month)
                    : "—"}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                  {usage.data.price_source ?? "AWS"}
                </Typography.Text>
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Measured
              </Typography.Text>
              <div>
                <Typography.Text strong style={{ fontSize: 16 }}>
                  {usage.data.measured_at ? formatTime(usage.data.measured_at) : "—"}
                </Typography.Text>
              </div>
            </div>
          </Space>
        ) : usage.isError ? (
          <Typography.Text type="secondary">
            Usage metrics unavailable — check credentials and bucket access.
          </Typography.Text>
        ) : null
      ) : null}
    </Space>
  );
};