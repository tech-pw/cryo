import { CheckCircleOutlined, CloseCircleOutlined, PlusOutlined, RobotOutlined, SlackOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AutoComplete,
  Button,
  Collapse,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message
} from "antd";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  createAIProvider,
  createDestination,
  createSlackConfig,
  createSource,
  deleteAIProvider,
  deleteSlackConfig,
  listAIProviders,
  listAIProviderTypes,
  listDestinations,
  listSlackConfigs,
  listSources,
  patchAIProvider,
  patchDestination,
  patchSlackConfig,
  patchSource,
  testAIProvider,
  testAIProviderConfig,
  testDestination,
  testDestinationConfig,
  testSlackConfig,
  testSlackConfigValues,
  testSource,
  ApiError,
  type AIProviderRead,
  type AIProviderType,
  type AIProviderWrite,
  type SlackConfigRead,
  type SlackConfigWrite
} from "../api/client";
import type { DestinationRead, DestinationWrite, SourceRead, SourceTestResult, SourceWrite } from "../api/types";
import { CronInput } from "../components/CronInput";
import { AwsIntegrationPanel } from "../components/AwsIntegrationPanel";
import { EmptyState, HintLabel, PageHeader } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { HealthBadge, StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { cronFormValidator } from "../lib/cron";
import {
  markConnectionNeedsScan,
  isConnectionPendingScan,
  type ConnectionScanNotice
} from "../lib/connectionScanPrompt";

// Matches cryo/api/redact.py's REDACTED_PLACEHOLDER -- a saved secret field is never sent back
// to the browser as its real value, only this sentinel. Used to decide when a "Test connection"
// click should ask the backend to test against the real stored secret (via source_id/
// destination_id/provider_id/config_id) instead of this literal placeholder string.
const REDACTED_PLACEHOLDER = "***REDACTED***";

export const Connections = () => {
  const { api, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sourceDrawer, setSourceDrawer] = useState<SourceRead | "new" | null>(null);
  const [destinationDrawer, setDestinationDrawer] = useState<DestinationRead | "new" | null>(null);
  const [aiProviderDrawer, setAiProviderDrawer] = useState<AIProviderRead | "new" | null>(null);
  const [slackDrawer, setSlackDrawer] = useState<SlackConfigRead | "new" | null>(null);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [pendingScanVersion, setPendingScanVersion] = useState(0);

  const redirectToScanner = (notice: ConnectionScanNotice) => {
    markConnectionNeedsScan(notice);
    setPendingScanVersion((value) => value + 1);
    navigate("/scans", { state: { connectionNotice: notice } });
  };

  const sources = useQuery({ queryKey: ["sources", token], queryFn: () => listSources(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const aiProviders = useQuery({
    queryKey: ["ai-providers", token],
    queryFn: () => listAIProviders(api)
  });
  const aiProviderTypes = useQuery({
    queryKey: ["ai-provider-types", token],
    queryFn: () => listAIProviderTypes(api)
  });
  const slackConfigs = useQuery({
    queryKey: ["slack-configs", token],
    queryFn: () => listSlackConfigs(api)
  });

  const saveSource = useMutation({
    mutationFn: (body: SourceWrite) =>
      sourceDrawer && sourceDrawer !== "new" ? patchSource(api, sourceDrawer.id, body) : createSource(api, body),
    onSuccess: (saved) => {
      const isNew = sourceDrawer === "new";
      message.success(isNew ? "Lakehouse connected" : "Lakehouse saved");
      setSourceDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
      if (isNew) {
        redirectToScanner({ kind: "lakehouse", id: saved.id, name: saved.name });
      }
    },
    onError: (error) => message.error((error as Error).message)
  });

  const saveDestination = useMutation({
    mutationFn: (body: DestinationWrite) =>
      destinationDrawer && destinationDrawer !== "new"
        ? patchDestination(api, destinationDrawer.id, body)
        : createDestination(api, body),
    onSuccess: (saved) => {
      const isNew = destinationDrawer === "new";
      message.success(isNew ? "Engine connected" : "Engine saved");
      setDestinationDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["destinations"] });
      if (isNew) {
        redirectToScanner({ kind: "engine", id: saved.id, name: saved.name });
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
        message.error("Admin role required to create or edit engines. Sign in with role admin.");
        return;
      }
      message.error((error as Error).message);
    }
  });

  const probe = useMutation({
    mutationFn: (destination: DestinationRead) => testDestination(api, destination.id),
    onSuccess: setTestResult,
    onError: (error) => message.error((error as Error).message)
  });

  const saveAiProvider = useMutation({
    mutationFn: async (body: AIProviderWrite & { id?: string }) => {
      if (body.id) {
        return patchAIProvider(api, body.id, body);
      }
      return createAIProvider(api, body);
    },
    onSuccess: () => {
      message.success("AI provider saved");
      setAiProviderDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const deleteAiProviderMutation = useMutation({
    mutationFn: (id: string) => deleteAIProvider(api, id),
    onSuccess: () => {
      message.success("AI provider deleted");
      void queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const testAiProviderMutation = useMutation({
    mutationFn: (id: string) => testAIProvider(api, id),
    onSuccess: (data) => {
      if (data.success) {
        message.success(`AI responded: "${data.response}"`);
      } else {
        message.error(`Test failed: ${data.error}`);
      }
    },
    onError: (error) => message.error((error as Error).message)
  });

  const saveSlackConfig = useMutation({
    mutationFn: async (body: SlackConfigWrite & { id?: string }) => {
      if (body.id) {
        return patchSlackConfig(api, body.id, body);
      }
      return createSlackConfig(api, body);
    },
    onSuccess: () => {
      message.success("Slack configuration saved");
      setSlackDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["slack-configs"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const deleteSlackConfigMutation = useMutation({
    mutationFn: (id: string) => deleteSlackConfig(api, id),
    onSuccess: () => {
      message.success("Slack configuration deleted");
      void queryClient.invalidateQueries({ queryKey: ["slack-configs"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const testSlackMutation = useMutation({
    mutationFn: (id: string) => testSlackConfig(api, id),
    onSuccess: () => {
      message.success("Test message sent to Slack!");
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <div>
      <PageHeader
        title="Connections"
        subtitle="Lakehouses are where your Iceberg data lives. Engines are what actually runs maintenance on it."
      />
      {testResult ? (
        <Alert
          className="page-alert"
          type={testResult.ok ? "success" : "warning"}
          showIcon
          message={testResult.ok ? "Engine capacity check passed" : "Engine capacity check returned warnings"}
          description={<pre className="inline-json">{JSON.stringify(testResult, null, 2)}</pre>}
          closable
          onClose={() => setTestResult(null)}
        />
      ) : null}
      <Tabs
        items={[
          {
            key: "sources",
            label: "Lakehouses",
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Typography.Paragraph type="secondary" className="tab-intro">
                  The Iceberg catalogs your data lakehouse lives in — REST catalog, Hive Metastore, or
                  Glue. Cryo reads these to discover tables and scan health. Read-only: nothing here
                  ever runs maintenance, it only tells Cryo what tables exist.
                </Typography.Paragraph>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setSourceDrawer("new")}>
                  New lakehouse
                </Button>
                <Table<SourceRead>
                  key={`lakehouses-${pendingScanVersion}`}
                  size="small"
                  rowKey="id"
                  loading={sources.isLoading}
                  dataSource={sources.data ?? []}
                  columns={[
                    {
                      title: "Name",
                      dataIndex: "name",
                      render: (value, row) => (
                        <Space size={8}>
                          <Typography.Text strong>{value}</Typography.Text>
                          {isConnectionPendingScan("lakehouse", row.id) ? (
                            <Tag color="gold">Scan required</Tag>
                          ) : null}
                        </Space>
                      )
                    },
                    { title: "Type", dataIndex: "type" },
                    { title: "Enabled", dataIndex: "enabled", render: (value) => <StatusTag status={value ? "succeeded" : "skipped"} /> },
                    {
                      title: "Config",
                      dataIndex: "config",
                      render: (value) => <Typography.Text code>{Object.keys(value ?? {}).join(", ") || "-"}</Typography.Text>
                    },
                    {
                      title: "",
                      render: (_, row) => (
                        <Space>
                          <Button size="small" onClick={() => setSourceDrawer(row)}>Edit</Button>
                          <Button size="small" onClick={() => patchSource(api, row.id, { enabled: !row.enabled }).then(() => queryClient.invalidateQueries({ queryKey: ["sources"] }))}>
                            {row.enabled ? "Disable" : "Enable"}
                          </Button>
                        </Space>
                      )
                    }
                  ]}
                  locale={{ emptyText: <EmptyState title="No lakehouses connected" description="Connect a catalog to start scanning Iceberg tables." /> }}
                />
              </Space>
            )
          },
          {
            key: "destinations",
            label: "Engines",
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Typography.Paragraph type="secondary" className="tab-intro">
                  The processing engines that actually run maintenance — a Trino coordinator or a
                  Spark/EMR cluster. Presets and manual runs target one of these to do the real work
                  (compaction, snapshot expiry, orphan cleanup).
                </Typography.Paragraph>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setDestinationDrawer("new")}>
                  New engine
                </Button>
                <Table<DestinationRead>
                  key={`engines-${pendingScanVersion}`}
                  size="small"
                  rowKey="id"
                  loading={destinations.isLoading}
                  dataSource={destinations.data ?? []}
                  columns={[
                    { title: "Name", dataIndex: "name", render: (value, row) => (
                      <Space size={8}>
                        <Typography.Text strong>{value}</Typography.Text>
                        {isConnectionPendingScan("engine", row.id) ? (
                          <Tag color="gold">Scan lakehouse first</Tag>
                        ) : null}
                      </Space>
                    ) },
                    {
                      title: "Enabled",
                      dataIndex: "enabled",
                      render: (value) => <HealthBadge healthy={value}>{value ? "Enabled" : "Disabled"}</HealthBadge>
                    },
                    {
                      title: "Off-peak window",
                      render: (_, row) => row.offpeak_start === null || row.offpeak_start === undefined ? "-" : `${row.offpeak_start}:00-${row.offpeak_end}:00`
                    },
                    {
                      title: "",
                      render: (_, row) => (
                        <Space>
                          <Button size="small" onClick={() => setDestinationDrawer(row)}>Edit</Button>
                          <Button size="small" loading={probe.isPending} onClick={() => probe.mutate(row)}>Test</Button>
                          <Button size="small" onClick={() => patchDestination(api, row.id, { enabled: !row.enabled }).then(() => queryClient.invalidateQueries({ queryKey: ["destinations"] }))}>
                            {row.enabled ? "Disable" : "Enable"}
                          </Button>
                        </Space>
                      )
                    }
                  ]}
                  locale={{ emptyText: <EmptyState title="No engines connected" description="Add a Trino or Spark engine to run maintenance." /> }}
                />
              </Space>
            )
          },
          {
            key: "ai-providers",
            label: (
              <Space>
                <RobotOutlined />
                AI Providers
              </Space>
            ),
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Typography.Paragraph type="secondary" className="tab-intro">
                  Connect an AI provider (Anthropic Claude or OpenAI GPT) to enable natural language
                  queries, intelligent health explanations, and AI-powered recommendations. API keys
                  are stored encrypted and never logged.
                </Typography.Paragraph>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setAiProviderDrawer("new")}>
                  New AI provider
                </Button>
                <Table<AIProviderRead>
                  size="small"
                  rowKey="id"
                  loading={aiProviders.isLoading}
                  dataSource={aiProviders.data ?? []}
                  columns={[
                    {
                      title: "Name",
                      dataIndex: "name",
                      render: (value, row) => (
                        <Space>
                          <Typography.Text strong>{value}</Typography.Text>
                          {row.is_default ? <Tag color="blue">Default</Tag> : null}
                        </Space>
                      )
                    },
                    {
                      title: "Provider",
                      dataIndex: "provider_type",
                      render: (value) => (
                        <Tag color={value === "anthropic" ? "orange" : "green"}>
                          {value === "anthropic" ? "Anthropic" : "OpenAI"}
                        </Tag>
                      )
                    },
                    { title: "Model", dataIndex: "model", render: (value) => <Typography.Text code>{value}</Typography.Text> },
                    {
                      title: "API Key",
                      dataIndex: "has_api_key",
                      render: (value) =>
                        value ? (
                          <Space>
                            <CheckCircleOutlined style={{ color: "var(--ant-color-success)" }} />
                            <Typography.Text type="secondary">Configured</Typography.Text>
                          </Space>
                        ) : (
                          <Space>
                            <CloseCircleOutlined style={{ color: "var(--ant-color-error)" }} />
                            <Typography.Text type="secondary">Missing</Typography.Text>
                          </Space>
                        )
                    },
                    {
                      title: "Enabled",
                      dataIndex: "enabled",
                      render: (value) => <StatusTag status={value ? "succeeded" : "skipped"} />
                    },
                    {
                      title: "",
                      render: (_, row) => (
                        <Space>
                          <Button size="small" onClick={() => setAiProviderDrawer(row)}>Edit</Button>
                          <Button
                            size="small"
                            loading={testAiProviderMutation.isPending}
                            disabled={!row.has_api_key}
                            onClick={() => testAiProviderMutation.mutate(row.id)}
                          >
                            Test
                          </Button>
                          <ConfirmAction
                            title="Delete this AI provider?"
                            description="Copilot and other AI features will stop using this key immediately."
                            onConfirm={() => deleteAiProviderMutation.mutate(row.id)}
                          >
                            <Button size="small" danger loading={deleteAiProviderMutation.isPending}>
                              Delete
                            </Button>
                          </ConfirmAction>
                        </Space>
                      )
                    }
                  ]}
                  locale={{
                    emptyText: (
                      <EmptyState
                        title="No AI providers configured"
                        description="Add an Anthropic or OpenAI API key to enable AI features like natural language queries and intelligent recommendations."
                      />
                    )
                  }}
                />
              </Space>
            )
          },
          {
            key: "slack",
            label: (
              <Space>
                <SlackOutlined />
                Slack
              </Space>
            ),
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Typography.Paragraph type="secondary" className="tab-intro">
                  Connect Slack to receive daily anomaly reports with AI-generated summaries.
                  Configure your webhook URL, reporting schedule, and channel.
                </Typography.Paragraph>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setSlackDrawer("new")}>
                  New Slack connection
                </Button>
                <Table<SlackConfigRead>
                  size="small"
                  rowKey="id"
                  loading={slackConfigs.isLoading}
                  dataSource={slackConfigs.data ?? []}
                  columns={[
                    {
                      title: "Name",
                      dataIndex: "name",
                      render: (value, row) => (
                        <Space>
                          <Typography.Text strong>{value}</Typography.Text>
                          {row.is_default ? <Tag color="blue">Default</Tag> : null}
                        </Space>
                      )
                    },
                    {
                      title: "Channel",
                      dataIndex: "channel",
                      render: (value) => value ? <Typography.Text code>{value}</Typography.Text> : <Typography.Text type="secondary">Default</Typography.Text>
                    },
                    {
                      title: "Webhook",
                      dataIndex: "has_webhook",
                      render: (value) =>
                        value ? (
                          <Space>
                            <CheckCircleOutlined style={{ color: "var(--ant-color-success)" }} />
                            <Typography.Text type="secondary">Configured</Typography.Text>
                          </Space>
                        ) : (
                          <Space>
                            <CloseCircleOutlined style={{ color: "var(--ant-color-error)" }} />
                            <Typography.Text type="secondary">Missing</Typography.Text>
                          </Space>
                        )
                    },
                    {
                      title: "Daily Report",
                      render: (_, row) => (
                        <Space direction="vertical" size={0}>
                          <StatusTag status={row.daily_report_enabled ? "succeeded" : "skipped"} />
                          {row.daily_report_enabled && (
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              {row.daily_report_cron} {row.daily_report_use_ai ? "(+ AI)" : ""}
                            </Typography.Text>
                          )}
                        </Space>
                      )
                    },
                    {
                      title: "Enabled",
                      dataIndex: "enabled",
                      render: (value) => <StatusTag status={value ? "succeeded" : "skipped"} />
                    },
                    {
                      title: "",
                      render: (_, row) => (
                        <Space>
                          <Button size="small" onClick={() => setSlackDrawer(row)}>Edit</Button>
                          <Button
                            size="small"
                            loading={testSlackMutation.isPending}
                            disabled={!row.has_webhook}
                            onClick={() => testSlackMutation.mutate(row.id)}
                          >
                            Test
                          </Button>
                          <ConfirmAction
                            title="Delete this Slack configuration?"
                            description="Daily reports and alerts will stop posting to this webhook."
                            onConfirm={() => deleteSlackConfigMutation.mutate(row.id)}
                          >
                            <Button size="small" danger loading={deleteSlackConfigMutation.isPending}>
                              Delete
                            </Button>
                          </ConfirmAction>
                        </Space>
                      )
                    }
                  ]}
                  locale={{
                    emptyText: (
                      <EmptyState
                        title="No Slack connections configured"
                        description="Add a Slack webhook to receive daily anomaly reports with AI-generated summaries."
                      />
                    )
                  }}
                />
              </Space>
            )
          }
        ]}
      />
      <SourceDrawer
        key={sourceDrawer && sourceDrawer !== "new" ? sourceDrawer.id : String(sourceDrawer)}
        value={sourceDrawer}
        saving={saveSource.isPending}
        onClose={() => setSourceDrawer(null)}
        onSave={(body) => saveSource.mutate(body)}
      />
      <DestinationDrawer
        key={destinationDrawer && destinationDrawer !== "new" ? destinationDrawer.id : String(destinationDrawer)}
        value={destinationDrawer}
        saving={saveDestination.isPending}
        onClose={() => setDestinationDrawer(null)}
        onSave={(body) => saveDestination.mutate(body)}
      />
      <AIProviderDrawer
        key={aiProviderDrawer && aiProviderDrawer !== "new" ? aiProviderDrawer.id : String(aiProviderDrawer)}
        value={aiProviderDrawer}
        providerTypes={aiProviderTypes.data ?? []}
        saving={saveAiProvider.isPending}
        onClose={() => setAiProviderDrawer(null)}
        onSave={(body) => saveAiProvider.mutate(body)}
      />
      <SlackDrawer
        key={slackDrawer && slackDrawer !== "new" ? slackDrawer.id : String(slackDrawer)}
        value={slackDrawer}
        saving={saveSlackConfig.isPending}
        onClose={() => setSlackDrawer(null)}
        onSave={(body) => saveSlackConfig.mutate(body)}
      />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Lakehouse (Source) drawer
//
// Config is passed almost verbatim to PyIceberg's `load_catalog(**properties)`, so its keyspace
// is genuinely open-ended (auth/oauth/region/etc. vary by catalog implementation). Rather than
// pretend to enumerate every possible property, this form gives named fields for everything we
// actually use today (see cryo/sources/*.py + the seeded local config) and an "Advanced" JSON
// panel for anything else — so nothing is hidden, but nobody has to hand-write JSON for the
// common case either.
// ---------------------------------------------------------------------------

const CATALOG_TYPES = [
  { value: "rest", label: "REST catalog" },
  { value: "hive", label: "Hive Metastore" },
  { value: "glue", label: "AWS Glue" },
  { value: "polaris", label: "Apache Polaris" },
  { value: "nessie", label: "Project Nessie" }
];

const CATALOG_URI_HINT: Record<string, string> = {
  rest: "http://localhost:8181",
  hive: "thrift://localhost:9083",
  glue: "",
  polaris: "http://localhost:8181/api/catalog",
  nessie: "http://localhost:19120/api/v1"
};

const KNOWN_SOURCE_KEYS = [
  "uri",
  "warehouse",
  "catalog_name",
  "type",
  "s3.endpoint",
  "s3.access-key-id",
  "s3.secret-access-key",
  "s3.path-style-access",
  "s3.region",
  "s3.role-arn",
  "s3.external-id",
  "s3.role-session-name"
];

const S3_AUTH_METHODS = [
  { value: "default", label: "Default credentials (IAM Role / Instance Profile / Environment)" },
  { value: "access_key", label: "Access Key + Secret Key" },
  { value: "assume_role", label: "Assume IAM Role (cross-account)" }
];

// AutoComplete, not a strict Select: S3-compatible endpoints (MinIO, etc.) can use arbitrary
// region strings, so the field must still accept free text -- this just prevents typos like
// "ap-south21" for the common case of real AWS regions.
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
].map((region) => ({ value: region }));

interface SourceFormValues {
  name: string;
  type: string;
  uri?: string;
  warehouse?: string;
  catalog_name?: string;
  s3_auth_method: "default" | "access_key" | "assume_role";
  s3_endpoint?: string;
  s3_region?: string;
  s3_access_key_id?: string;
  s3_secret_access_key?: string;
  s3_role_arn?: string;
  s3_external_id?: string;
  s3_role_session_name?: string;
  s3_path_style_access?: boolean;
  advanced_json?: string;
  secret_ref?: string | null;
  enabled: boolean;
}

const decomposeSourceConfig = (config: Record<string, unknown> | undefined) => {
  const cfg = config ?? {};
  const advanced = Object.fromEntries(Object.entries(cfg).filter(([key]) => !KNOWN_SOURCE_KEYS.includes(key)));

  // Determine auth method from config
  let s3_auth_method: "default" | "access_key" | "assume_role" = "default";
  if (cfg["s3.role-arn"]) {
    s3_auth_method = "assume_role";
  } else if (cfg["s3.access-key-id"]) {
    s3_auth_method = "access_key";
  }

  return {
    uri: typeof cfg.uri === "string" ? cfg.uri : undefined,
    warehouse: typeof cfg.warehouse === "string" ? cfg.warehouse : undefined,
    catalog_name: typeof cfg.catalog_name === "string" ? cfg.catalog_name : undefined,
    s3_auth_method,
    s3_endpoint: typeof cfg["s3.endpoint"] === "string" ? (cfg["s3.endpoint"] as string) : undefined,
    s3_region: typeof cfg["s3.region"] === "string" ? (cfg["s3.region"] as string) : undefined,
    s3_access_key_id: typeof cfg["s3.access-key-id"] === "string" ? (cfg["s3.access-key-id"] as string) : undefined,
    s3_secret_access_key:
      typeof cfg["s3.secret-access-key"] === "string" ? (cfg["s3.secret-access-key"] as string) : undefined,
    s3_role_arn: typeof cfg["s3.role-arn"] === "string" ? (cfg["s3.role-arn"] as string) : undefined,
    s3_external_id: typeof cfg["s3.external-id"] === "string" ? (cfg["s3.external-id"] as string) : undefined,
    s3_role_session_name: typeof cfg["s3.role-session-name"] === "string" ? (cfg["s3.role-session-name"] as string) : undefined,
    s3_path_style_access: cfg["s3.path-style-access"] === "true" || cfg["s3.path-style-access"] === true,
    advanced_json: Object.keys(advanced).length ? JSON.stringify(advanced, null, 2) : ""
  };
};

const buildSourceConfig = (values: SourceFormValues): Record<string, unknown> => {
  let extra: Record<string, unknown> = {};
  if (values.advanced_json?.trim()) {
    extra = JSON.parse(values.advanced_json) as Record<string, unknown>;
  }
  const config: Record<string, unknown> = { ...extra };
  if (values.uri) config.uri = values.uri;
  if (values.warehouse) config.warehouse = values.warehouse;
  if (values.catalog_name) config.catalog_name = values.catalog_name;

  // S3 config based on auth method
  if (values.s3_endpoint) config["s3.endpoint"] = values.s3_endpoint;
  if (values.s3_region) config["s3.region"] = values.s3_region;
  if (values.s3_path_style_access) config["s3.path-style-access"] = "true";

  if (values.s3_auth_method === "access_key") {
    if (values.s3_access_key_id) config["s3.access-key-id"] = values.s3_access_key_id;
    if (values.s3_secret_access_key) config["s3.secret-access-key"] = values.s3_secret_access_key;
  } else if (values.s3_auth_method === "assume_role") {
    if (values.s3_role_arn) config["s3.role-arn"] = values.s3_role_arn;
    if (values.s3_external_id) config["s3.external-id"] = values.s3_external_id;
    if (values.s3_role_session_name) config["s3.role-session-name"] = values.s3_role_session_name;
  }
  // For "default" auth method, we don't add any credentials - uses AWS SDK default chain

  return config;
};

/**
 * A single "Warehouse" field is only ever a default — individual namespaces or tables in a real
 * lakehouse can (and often do) point at different S3 buckets/prefixes. Rather than pretend one
 * field settles that, this shows what's actually reachable so the user can see the real shape of
 * the catalog before saving.
 */
const SourceTestResultPanel = ({ result }: { result: SourceTestResult }) => {
  if (!result.ok) {
    return (
      <Alert
        className="page-alert"
        type="error"
        showIcon
        message="Couldn't connect"
        description={result.error}
      />
    );
  }
  return (
    <Alert
      className="page-alert"
      type="success"
      showIcon
      message={`Connected — found ${result.namespaces.length} namespace${result.namespaces.length === 1 ? "" : "s"}`}
      description={
        <Space direction="vertical" size={6} className="full-width">
          {result.namespaces.length ? (
            <Space size={[4, 4]} wrap>
              {result.namespaces.map((namespace, index) => (
                <Typography.Text key={namespace || `namespace-${index}`} code>
                  {namespace || "(unnamed)"}
                </Typography.Text>
              ))}
            </Space>
          ) : null}
          {result.note ? (
            <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
              {result.note}
            </Typography.Text>
          ) : null}
        </Space>
      }
    />
  );
};

const SourceDrawer = ({
  value,
  saving,
  onClose,
  onSave
}: {
  value: SourceRead | "new" | null;
  saving: boolean;
  onClose: () => void;
  onSave: (body: SourceWrite) => void;
}) => {
  const { api } = useAuth();
  const [form] = Form.useForm<SourceFormValues>();
  const isEditing = value && value !== "new";
  const existingConfig = isEditing ? decomposeSourceConfig(value.config as Record<string, unknown>) : {};
  const [catalogType, setCatalogType] = useState<string>(isEditing ? value.type : "rest");
  const [s3AuthMethod, setS3AuthMethod] = useState<"default" | "access_key" | "assume_role">(
    (existingConfig as { s3_auth_method?: "default" | "access_key" | "assume_role" }).s3_auth_method ?? "default"
  );

  const [testPassed, setTestPassed] = useState(false);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getFieldsValue(true) as SourceFormValues;
      return testSource(api, {
        type: current.type,
        config: buildSourceConfig(current),
        // If editing and a secret field still holds the redacted placeholder (untouched), test
        // against the real stored value instead of the literal "***REDACTED***" string.
        source_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => setTestPassed(Boolean(data.ok)),
    onError: () => {
      setTestPassed(false);
      message.error("Advanced properties must be valid JSON before testing.");
    }
  });

  return (
    <Drawer title={isEditing ? "Edit lakehouse" : "New lakehouse"} open={Boolean(value)} onClose={onClose} size={620} destroyOnHidden>
      <Form<SourceFormValues>
        form={form}
        layout="vertical"
        initialValues={{
          name: isEditing ? value.name : "",
          type: isEditing ? value.type : "rest",
          secret_ref: isEditing ? value.secret_ref : null,
          enabled: isEditing ? value.enabled : true,
          s3_auth_method: "default",
          ...existingConfig
        }}
        onValuesChange={(changed) => {
          if (changed.type) setCatalogType(changed.type);
          if (changed.s3_auth_method) setS3AuthMethod(changed.s3_auth_method);
          setTestPassed(false);
          testConnection.reset();
        }}
        onFinish={(values) => {
          try {
            onSave({
              name: values.name,
              type: values.type,
              config: buildSourceConfig(values),
              secret_ref: values.secret_ref || null,
              enabled: values.enabled
            });
          } catch {
            message.error("Advanced properties must be valid JSON.");
          }
        }}
      >
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="production-lakehouse" />
        </Form.Item>
        <Form.Item name="type" label="Catalog type" rules={[{ required: true }]}>
          <Select options={CATALOG_TYPES} />
        </Form.Item>

        {catalogType !== "glue" ? (
          <Form.Item
            name="uri"
            label="Catalog URI"
            rules={[{ required: true, message: "Required for this catalog type" }]}
          >
            <Input placeholder={CATALOG_URI_HINT[catalogType] ?? "http://localhost:8181"} />
          </Form.Item>
        ) : null}
        <Form.Item name="warehouse" label="Warehouse">
          <Input placeholder={catalogType === "glue" ? "s3://my-bucket/warehouse" : "warehouse"} />
        </Form.Item>
        <Form.Item
          name="catalog_name"
          label={<HintLabel label="Catalog name" tooltip="Overrides the name PyIceberg registers this catalog under. Leave blank to use the lakehouse name above." />}
        >
          <Input placeholder="(defaults to the name above)" />
        </Form.Item>

        <Form.Item>
          <Button loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
            Test connection
          </Button>
          {testConnection.isPending && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              This can take up to 2 minutes on slow or VPN-gated networks — sit back and relax.
            </Typography.Text>
          )}
        </Form.Item>
        {testConnection.data ? <SourceTestResultPanel result={testConnection.data} /> : null}

        <Divider titlePlacement="left" plain style={{ fontSize: 12, color: "var(--ink-faint, rgba(0,0,0,0.45))" }}>
          Storage connection
        </Divider>
        <Typography.Paragraph type="secondary" style={{ marginTop: -8, fontSize: 12.5 }}>
          Configure how Cryo authenticates to S3 storage. For production, use IAM roles.
          Access keys are only recommended for local/dev environments (e.g. MinIO).
        </Typography.Paragraph>

        <Form.Item
          name="s3_auth_method"
          label={<HintLabel label="Authentication method" tooltip="How to authenticate to S3. IAM roles are recommended for production." />}
        >
          <Select options={S3_AUTH_METHODS} />
        </Form.Item>

        <Space size={12} className="full-width" style={{ display: "flex" }}>
          <Form.Item name="s3_region" label="AWS Region" style={{ flex: 1 }}>
            <AutoComplete
              options={AWS_REGIONS}
              filterOption={(input, option) => (option?.value ?? "").toLowerCase().includes(input.toLowerCase())}
              placeholder="us-east-1"
              popupMatchSelectWidth={220}
            />
          </Form.Item>
          <Form.Item name="s3_endpoint" label="S3 endpoint (optional)" style={{ flex: 1 }}>
            <Input placeholder="http://localhost:9000" />
          </Form.Item>
        </Space>

        {s3AuthMethod === "default" && (
          <Alert
            type="info"
            showIcon
            message="Using default AWS credentials"
            description="Cryo will use the AWS SDK default credential chain: environment variables, IAM role (IRSA/instance profile), or ~/.aws/credentials."
            style={{ marginBottom: 16 }}
          />
        )}

        {s3AuthMethod === "access_key" && (
          <Space size={12} className="full-width" style={{ display: "flex" }}>
            <Form.Item name="s3_access_key_id" label="Access key ID" style={{ flex: 1 }}>
              <Input autoComplete="off" placeholder="AKIA..." />
            </Form.Item>
            <Form.Item name="s3_secret_access_key" label="Secret access key" style={{ flex: 1 }}>
              <Input.Password autoComplete="off" />
            </Form.Item>
          </Space>
        )}

        {s3AuthMethod === "assume_role" && (
          <>
            <Form.Item
              name="s3_role_arn"
              label={<HintLabel label="Role ARN" tooltip="The ARN of the IAM role to assume for cross-account access." />}
              rules={[{ required: true, message: "Role ARN is required for assume role auth" }]}
            >
              <Input placeholder="arn:aws:iam::123456789012:role/MyCrossAccountRole" />
            </Form.Item>
            <Space size={12} className="full-width" style={{ display: "flex" }}>
              <Form.Item
                name="s3_external_id"
                label={<HintLabel label="External ID (optional)" tooltip="External ID for additional security when assuming the role." />}
                style={{ flex: 1 }}
              >
                <Input placeholder="my-external-id" />
              </Form.Item>
              <Form.Item
                name="s3_role_session_name"
                label={<HintLabel label="Session name (optional)" tooltip="Name for the assumed role session. Defaults to 'cryo-session'." />}
                style={{ flex: 1 }}
              >
                <Input placeholder="cryo-session" />
              </Form.Item>
            </Space>
          </>
        )}

        <Form.Item name="s3_path_style_access" label="Path-style access" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Collapse
          ghost
          items={[
            {
              key: "advanced",
              label: "Advanced properties (optional)",
              children: (
                <>
                  <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
                    Any other PyIceberg catalog property (OAuth credentials, custom headers, SSL
                    settings, etc.) — merged in alongside the fields above.
                  </Typography.Paragraph>
                  <Form.Item name="advanced_json" noStyle>
                    <Input.TextArea rows={5} className="mono-input" placeholder='{"credential": "client-id:client-secret"}' />
                  </Form.Item>
                </>
              )
            }
          ]}
        />

        {isEditing ? (
          <Collapse
            style={{ marginTop: 16, marginBottom: 16 }}
            items={[
              {
                key: "aws-integration",
                label: "AWS S3 cost metrics (free tier)",
                children: <AwsIntegrationPanel sourceId={value.id} />
              }
            ]}
          />
        ) : null}

        <Form.Item name="secret_ref" label="Secret reference" style={{ marginTop: 16 }}>
          <Input placeholder="Encrypted secret reference, never raw credentials" />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        {!testPassed && (
          <Alert
            type="warning"
            showIcon
            message="Test the connection before saving"
            description={'Click "Test connection" above with a passing result to enable Save.'}
            style={{ marginBottom: 16 }}
          />
        )}
        <Button type="primary" htmlType="submit" loading={saving} disabled={!testPassed}>
          Save
        </Button>
      </Form>
    </Drawer>
  );
};

// ---------------------------------------------------------------------------
// Engine (Destination) drawer
//
// Unlike lakehouse config, an engine's config keys are entirely Cryo's own (see
// cryo/engines/trino/client.py and cryo/engines/spark/emr.py) — a small, fixed, known surface,
// so this can be fully structured with no JSON escape hatch needed except the one genuinely
// open-ended Spark field (spark_submit_params, arbitrary --conf overrides).
// ---------------------------------------------------------------------------

interface TrinoCoordinatorFormValue {
  host: string;
  port: number;
  http_scheme: "http" | "https";
}

interface DestinationFormValues {
  name: string;
  engine: "trino" | "spark_emr";
  offpeak_start?: number;
  offpeak_end?: number;
  secret_ref?: string | null;
  enabled: boolean;
  // Trino
  coordinators?: TrinoCoordinatorFormValue[];
  user?: string;
  catalog?: string;
  schema?: string;
  use_basic_auth?: boolean;
  auth_username?: string;
  auth_password?: string;
  // Spark EMR
  application_id?: string;
  execution_role_arn?: string;
  region?: string;
  s3_logs_bucket?: string;
  s3_logs_prefix?: string;
  entry_point?: string;
  poll_interval_seconds?: number;
  timeout_seconds?: number;
  spark_submit_params_json?: string;
}

const decomposeDestinationConfig = (engine: string, config: Record<string, unknown> | undefined) => {
  const cfg = config ?? {};
  if (engine === "spark_emr") {
    const sparkParams = cfg.spark_submit_params;
    return {
      application_id: typeof cfg.application_id === "string" ? cfg.application_id : "",
      execution_role_arn: typeof cfg.execution_role_arn === "string" ? cfg.execution_role_arn : "",
      region: typeof cfg.region === "string" ? cfg.region : "us-east-1",
      s3_logs_bucket: typeof cfg.s3_logs_bucket === "string" ? cfg.s3_logs_bucket : "",
      s3_logs_prefix: typeof cfg.s3_logs_prefix === "string" ? cfg.s3_logs_prefix : "cryo-spark-logs",
      entry_point:
        typeof cfg.entry_point === "string" ? cfg.entry_point : "s3://cryo-artifacts/cryo_spark_runner.py",
      poll_interval_seconds: typeof cfg.poll_interval_seconds === "number" ? cfg.poll_interval_seconds : 10,
      timeout_seconds: typeof cfg.timeout_seconds === "number" ? cfg.timeout_seconds : 3600,
      spark_submit_params_json: sparkParams ? JSON.stringify(sparkParams, null, 2) : ""
    };
  }

  const rawCoordinators = Array.isArray(cfg.coordinators) ? cfg.coordinators : ["localhost"];
  const coordinators: TrinoCoordinatorFormValue[] = rawCoordinators.map((item) => {
    if (typeof item === "string") {
      return { host: item, port: 8080, http_scheme: "http" };
    }
    const record = item as Record<string, unknown>;
    return {
      host: typeof record.host === "string" ? record.host : "",
      port: typeof record.port === "number" ? record.port : 8080,
      http_scheme: record.http_scheme === "https" || record.scheme === "https" ? "https" : "http"
    };
  });
  const auth = cfg.auth as Record<string, unknown> | undefined;
  return {
    coordinators,
    user: typeof cfg.user === "string" ? cfg.user : "cryo",
    catalog: typeof cfg.catalog === "string" ? cfg.catalog : "iceberg",
    schema: typeof cfg.schema === "string" ? cfg.schema : undefined,
    use_basic_auth: Boolean(auth && auth.type === "basic"),
    auth_username: typeof auth?.username === "string" ? auth.username : "",
    auth_password: typeof auth?.password === "string" ? auth.password : ""
  };
};

const buildDestinationConfig = (values: DestinationFormValues): Record<string, unknown> => {
  if (values.engine === "spark_emr") {
    const config: Record<string, unknown> = {
      application_id: values.application_id,
      execution_role_arn: values.execution_role_arn,
      region: values.region || "us-east-1",
      s3_logs_prefix: values.s3_logs_prefix || "cryo-spark-logs",
      entry_point: values.entry_point || "s3://cryo-artifacts/cryo_spark_runner.py",
      poll_interval_seconds: values.poll_interval_seconds || 10,
      timeout_seconds: values.timeout_seconds || 3600
    };
    if (values.s3_logs_bucket) config.s3_logs_bucket = values.s3_logs_bucket;
    if (values.spark_submit_params_json?.trim()) {
      config.spark_submit_params = JSON.parse(values.spark_submit_params_json);
    }
    return config;
  }

  const config: Record<string, unknown> = {
    coordinators: (values.coordinators ?? []).map((c) => ({
      host: c.host,
      port: c.port || 8080,
      http_scheme: c.http_scheme || "http"
    })),
    user: values.user || "cryo",
    catalog: values.catalog || "iceberg"
  };
  if (values.schema) config.schema = values.schema;
  if (values.use_basic_auth) {
    config.auth = { type: "basic", username: values.auth_username, password: values.auth_password };
  }
  return config;
};

const DestinationDrawer = ({
  value,
  saving,
  onClose,
  onSave
}: {
  value: DestinationRead | "new" | null;
  saving: boolean;
  onClose: () => void;
  onSave: (body: DestinationWrite) => void;
}) => {
  const { api } = useAuth();
  const [form] = Form.useForm<DestinationFormValues>();
  const isEditing = value && value !== "new";
  const initialEngine = isEditing ? value.engine : "trino";
  const [engine, setEngine] = useState<"trino" | "spark_emr">(initialEngine as "trino" | "spark_emr");
  const existingConfig = isEditing
    ? decomposeDestinationConfig(initialEngine, value.config as Record<string, unknown>)
    : decomposeDestinationConfig("trino", undefined);
  const useBasicAuth = Form.useWatch("use_basic_auth", form);
  const [testPassed, setTestPassed] = useState(false);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getFieldsValue(true) as DestinationFormValues;
      return testDestinationConfig(api, {
        engine: current.engine,
        config: buildDestinationConfig(current),
        destination_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => {
      setTestPassed(Boolean(data.ok));
      if (!data.ok) message.error(data.message ?? "Test failed");
    },
    onError: () => {
      setTestPassed(false);
      message.error("Spark submit params must be valid JSON before testing.");
    }
  });

  return (
    <Drawer title={isEditing ? "Edit engine" : "New engine"} open={Boolean(value)} onClose={onClose} size={620} destroyOnHidden>
      <Form<DestinationFormValues>
        form={form}
        layout="vertical"
        initialValues={{
          name: isEditing ? value.name : "",
          engine: initialEngine,
          secret_ref: isEditing ? value.secret_ref : null,
          enabled: isEditing ? value.enabled : true,
          offpeak_start: isEditing ? value.offpeak_start : 0,
          offpeak_end: isEditing ? value.offpeak_end : 7,
          ...existingConfig
        }}
        onValuesChange={(changed) => {
          if (changed.engine) setEngine(changed.engine);
          setTestPassed(false);
          testConnection.reset();
        }}
        onFinish={(values) => {
          try {
            onSave({
              name: values.name,
              engine: values.engine,
              config: buildDestinationConfig(values),
              secret_ref: values.secret_ref || null,
              enabled: values.enabled,
              offpeak_start: values.offpeak_start ?? null,
              offpeak_end: values.offpeak_end ?? null
            });
          } catch {
            message.error("Spark submit params must be valid JSON.");
          }
        }}
      >
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="trino-primary" />
        </Form.Item>
        <Form.Item name="engine" label="Engine" rules={[{ required: true }]}>
          <Select options={[{ label: "Trino", value: "trino" }, { label: "Spark on EMR Serverless", value: "spark_emr" }]} />
        </Form.Item>

        {engine === "trino" ? (
          <>
            <Form.List name="coordinators">
              {(fields, { add, remove }) => (
                <Form.Item
                  label={
                    <HintLabel
                      label="Coordinators"
                      tooltip="One or more Trino coordinator hosts. Multiple entries let Cryo spread load or fail over."
                    />
                  }
                >
                  <Space direction="vertical" size={8} className="full-width">
                    {fields.map((field) => {
                      const { key: fieldKey, name: fieldName, ...fieldRest } = field;
                      return (
                      <Space key={fieldKey} align="baseline" size={8}>
                        <Form.Item {...fieldRest} name={[fieldName, "host"]} noStyle rules={[{ required: true, message: "Host required" }]}>
                          <Input placeholder="trino.internal" style={{ width: 240 }} />
                        </Form.Item>
                        <Form.Item {...fieldRest} name={[fieldName, "port"]} noStyle>
                          <InputNumber placeholder="8080" min={1} max={65535} style={{ width: 90 }} />
                        </Form.Item>
                        <Form.Item {...fieldRest} name={[fieldName, "http_scheme"]} noStyle>
                          <Select
                            style={{ width: 90 }}
                            options={[{ label: "http", value: "http" }, { label: "https", value: "https" }]}
                          />
                        </Form.Item>
                        {fields.length > 1 ? <Button size="small" danger onClick={() => remove(fieldName)}>Remove</Button> : null}
                      </Space>
                      );
                    })}
                    <Button
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={() => add({ host: "", port: 8080, http_scheme: "http" })}
                    >
                      Add coordinator
                    </Button>
                  </Space>
                </Form.Item>
              )}
            </Form.List>
            <Space size={12} className="full-width" style={{ display: "flex" }}>
              <Form.Item name="user" label="User" style={{ flex: 1 }}>
                <Input placeholder="cryo" />
              </Form.Item>
              <Form.Item name="catalog" label="Catalog" style={{ flex: 1 }}>
                <Input placeholder="iceberg" />
              </Form.Item>
              <Form.Item name="schema" label="Schema (optional)" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
            </Space>
            <Form.Item name="use_basic_auth" label="Use basic auth" valuePropName="checked">
              <Switch />
            </Form.Item>
            {useBasicAuth ? (
              <Space size={12} className="full-width" style={{ display: "flex" }}>
                <Form.Item name="auth_username" label="Username" style={{ flex: 1 }}>
                  <Input autoComplete="off" />
                </Form.Item>
                <Form.Item name="auth_password" label="Password" style={{ flex: 1 }}>
                  <Input.Password autoComplete="off" />
                </Form.Item>
              </Space>
            ) : null}
          </>
        ) : (
          <>
            <Form.Item name="application_id" label="EMR Serverless application ID" rules={[{ required: true }]}>
              <Input placeholder="00fcxxxxxxxxxxxx" />
            </Form.Item>
            <Form.Item name="execution_role_arn" label="Execution role ARN" rules={[{ required: true }]}>
              <Input placeholder="arn:aws:iam::123456789012:role/cryo-emr-execution" />
            </Form.Item>
            <Space size={12} className="full-width" style={{ display: "flex" }}>
              <Form.Item name="region" label="AWS region" style={{ flex: 1 }}>
                <AutoComplete
                  options={AWS_REGIONS}
                  filterOption={(input, option) => (option?.value ?? "").toLowerCase().includes(input.toLowerCase())}
                  placeholder="us-east-1"
                  popupMatchSelectWidth={220}
                />
              </Form.Item>
              <Form.Item name="s3_logs_bucket" label="S3 logs bucket (optional)" style={{ flex: 1 }}>
                <Input placeholder="my-emr-logs" />
              </Form.Item>
              <Form.Item name="s3_logs_prefix" label="S3 logs prefix" style={{ flex: 1 }}>
                <Input placeholder="cryo-spark-logs" />
              </Form.Item>
            </Space>
            <Form.Item name="entry_point" label="Spark entry point">
              <Input placeholder="s3://cryo-artifacts/cryo_spark_runner.py" />
            </Form.Item>
            <Space size={12} className="full-width" style={{ display: "flex" }}>
              <Form.Item
                name="poll_interval_seconds"
                label={<HintLabel label="Poll interval (s)" tooltip="How often Cryo checks EMR for job status." />}
                style={{ flex: 1 }}
              >
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item
                name="timeout_seconds"
                label={<HintLabel label="Timeout (s)" tooltip="Cryo gives up waiting for the job after this long." />}
                style={{ flex: 1 }}
              >
                <InputNumber min={1} style={{ width: "100%" }} />
              </Form.Item>
            </Space>
            <Collapse
              ghost
              items={[
                {
                  key: "spark-params",
                  label: "Advanced Spark submit parameters (optional)",
                  children: (
                    <>
                      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
                        Extra <code>--conf</code> overrides passed to <code>spark-submit</code>, as JSON key/value pairs.
                      </Typography.Paragraph>
                      <Form.Item name="spark_submit_params_json" noStyle>
                        <Input.TextArea rows={4} className="mono-input" placeholder='{"--conf spark.executor.memory": "8g"}' />
                      </Form.Item>
                    </>
                  )
                }
              ]}
            />
          </>
        )}

        <Form.Item>
          <Button loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
            Test connection
          </Button>
          {testConnection.isPending && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              This can take up to 2 minutes on slow or VPN-gated networks — sit back and relax.
            </Typography.Text>
          )}
        </Form.Item>
        {testConnection.data ? (
          <Alert
            type={testConnection.data.ok ? "success" : "error"}
            showIcon
            message={testConnection.data.ok ? "Connection succeeded" : "Connection failed"}
            description={
              testConnection.data.ok
                ? testConnection.data.message ??
                  `Capacity: ${testConnection.data.used ?? 0}/${testConnection.data.capacity ?? 0} in use`
                : testConnection.data.message
            }
            style={{ marginBottom: 16 }}
          />
        ) : null}

        <Divider titlePlacement="left" plain style={{ fontSize: 12, color: "var(--ink-faint, rgba(0,0,0,0.45))" }}>
          Scheduling
        </Divider>
        <Space size={12}>
          <Form.Item
            name="offpeak_start"
            label={<HintLabel label="Off-peak start (hr)" tooltip="Hour of day, in this engine's local time, when low-priority maintenance is allowed to start." />}
          >
            <InputNumber min={0} max={23} />
          </Form.Item>
          <Form.Item name="offpeak_end" label="Off-peak end (hr)">
            <InputNumber min={0} max={23} />
          </Form.Item>
        </Space>

        <Form.Item name="secret_ref" label="Secret reference">
          <Input placeholder="Encrypted secret reference, never raw credentials" />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        {!testPassed && (
          <Alert
            type="warning"
            showIcon
            message="Test the connection before saving"
            description={'Click "Test connection" above with a passing result to enable Save.'}
            style={{ marginBottom: 16 }}
          />
        )}
        <Button type="primary" htmlType="submit" loading={saving} disabled={!testPassed}>
          Save
        </Button>
      </Form>
    </Drawer>
  );
};

// ---------------------------------------------------------------------------
// AI Provider drawer
// ---------------------------------------------------------------------------

interface AIProviderFormValues {
  name: string;
  provider_type: string;
  api_key: string;
  model: string;
  enabled: boolean;
  is_default: boolean;
  // Dynamic config fields (e.g., api_base for Azure)
  config_api_base?: string;
  config_api_version?: string;
  // Allow any config_ prefixed fields
  [key: `config_${string}`]: string | undefined;
}

// Default provider types as fallback when API hasn't loaded
const DEFAULT_PROVIDER_TYPES: AIProviderType[] = [
  {
    type: "anthropic",
    name: "Anthropic",
    models: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-sonnet-20241022"],
    default_model: "claude-sonnet-4-20250514",
    config_fields: []
  },
  {
    type: "openai",
    name: "OpenAI",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    default_model: "gpt-4o",
    config_fields: []
  },
  {
    type: "azure_openai",
    name: "Azure OpenAI",
    models: ["gpt4o", "gpt-4", "gpt-4-turbo", "gpt-35-turbo"],
    default_model: "gpt4o",
    config_fields: [
      { name: "api_base", label: "API Base URL", placeholder: "https://your-resource.openai.azure.com", required: true },
      { name: "api_version", label: "API Version", placeholder: "2024-03-01-preview", required: false, default: "2024-03-01-preview" }
    ]
  }
];

const AIProviderDrawer = ({
  value,
  providerTypes,
  saving,
  onClose,
  onSave
}: {
  value: AIProviderRead | "new" | null;
  providerTypes: AIProviderType[];
  saving: boolean;
  onClose: () => void;
  onSave: (body: AIProviderWrite & { id?: string }) => void;
}) => {
  const { api } = useAuth();
  const [form] = Form.useForm<AIProviderFormValues>();
  const isEditing = value && value !== "new";
  const existingConfig = isEditing ? (value.config as Record<string, string>) : {};
  const [selectedType, setSelectedType] = useState<string>(isEditing ? value.provider_type : "anthropic");

  // Use API data if available, otherwise fall back to defaults
  const effectiveProviderTypes = providerTypes.length > 0 ? providerTypes : DEFAULT_PROVIDER_TYPES;
  const currentTypeInfo = effectiveProviderTypes.find((t) => t.type === selectedType);
  const modelOptions = currentTypeInfo?.models.map((m) => ({ label: m, value: m })) ?? [];
  const configFields = currentTypeInfo?.config_fields ?? [];
  const [testPassed, setTestPassed] = useState(false);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getFieldsValue(true) as AIProviderFormValues;
      const config: Record<string, string> = {};
      if (current.config_api_base) config.api_base = current.config_api_base;
      if (current.config_api_version) config.api_version = current.config_api_version;
      return testAIProviderConfig(api, {
        provider_type: current.provider_type,
        api_key: current.api_key || "",
        model: current.model,
        config,
        provider_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => {
      setTestPassed(data.success);
      if (!data.success) message.error(`Test failed: ${data.error}`);
      else message.success(`AI responded: "${data.response}"`);
    },
    onError: (error) => {
      setTestPassed(false);
      message.error((error as Error).message);
    }
  });

  return (
    <Drawer
      title={isEditing ? "Edit AI provider" : "New AI provider"}
      open={Boolean(value)}
      onClose={onClose}
      size={520}
      destroyOnHidden
    >
      <Form<AIProviderFormValues>
        form={form}
        layout="vertical"
        initialValues={{
          name: isEditing ? value.name : "",
          provider_type: isEditing ? value.provider_type : "anthropic",
          api_key: "",
          model: isEditing ? value.model : "claude-sonnet-4-20250514",
          enabled: isEditing ? value.enabled : true,
          is_default: isEditing ? value.is_default : false,
          config_api_base: existingConfig.api_base ?? "",
          config_api_version: existingConfig.api_version ?? "2024-03-01-preview"
        }}
        onValuesChange={(changed) => {
          if (changed.provider_type) {
            setSelectedType(changed.provider_type);
            const newTypeInfo = effectiveProviderTypes.find((t) => t.type === changed.provider_type);
            if (newTypeInfo) {
              form.setFieldValue("model", newTypeInfo.default_model);
              // Set defaults for config fields
              newTypeInfo.config_fields?.forEach((field) => {
                if (field.default) {
                  form.setFieldValue(`config_${field.name}` as keyof AIProviderFormValues, field.default);
                }
              });
            }
          }
          setTestPassed(false);
          testConnection.reset();
        }}
        onFinish={(values) => {
          // Build config object from config_ prefixed fields
          const config: Record<string, string> = {};
          if (values.config_api_base) config.api_base = values.config_api_base;
          if (values.config_api_version) config.api_version = values.config_api_version;

          const body: AIProviderWrite & { id?: string } = {
            name: values.name,
            provider_type: values.provider_type,
            api_key: values.api_key || undefined, // Don't send empty string (keeps existing)
            model: values.model,
            config: Object.keys(config).length > 0 ? config : undefined,
            enabled: values.enabled,
            is_default: values.is_default
          };
          if (isEditing) {
            body.id = value.id;
          }
          onSave(body);
        }}
      >
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="anthropic-production" />
        </Form.Item>

        <Form.Item name="provider_type" label="Provider" rules={[{ required: true }]}>
          <Select
            options={effectiveProviderTypes.map((t) => ({
              label: (
                <Space>
                  <RobotOutlined />
                  {t.name}
                </Space>
              ),
              value: t.type
            }))}
          />
        </Form.Item>

        <Form.Item
          name="api_key"
          label={
            <HintLabel
              label="API Key"
              tooltip={
                isEditing && value.has_api_key
                  ? "Leave blank to keep the existing key. Enter a new value to replace it."
                  : "Your API key from the provider's dashboard. Stored encrypted."
              }
            />
          }
          rules={[{ required: !isEditing, message: "API key is required for new providers" }]}
        >
          <Input.Password
            placeholder={isEditing && value.has_api_key ? "••••••••••••••••" : "sk-..."}
            autoComplete="off"
          />
        </Form.Item>

        {/* Dynamic config fields for providers like Azure OpenAI */}
        {configFields.map((field, index) => (
          <Form.Item
            key={field.name || `config-field-${index}`}
            name={`config_${field.name}`}
            label={field.label}
            rules={[{ required: field.required, message: `${field.label} is required` }]}
          >
            <Input placeholder={field.placeholder ?? ""} />
          </Form.Item>
        ))}

        <Form.Item name="model" label={selectedType === "azure_openai" ? "Deployment Name" : "Model"} rules={[{ required: true }]}>
          <Select options={modelOptions} />
        </Form.Item>

        <Form.Item>
          <Button loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
            Test connection
          </Button>
          {testConnection.isPending && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              This can take up to 2 minutes on slow or VPN-gated networks — sit back and relax.
            </Typography.Text>
          )}
        </Form.Item>

        <Divider />

        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Form.Item
          name="is_default"
          label={
            <HintLabel
              label="Set as default"
              tooltip="The default provider is used for AI chat and copilot features. Only one provider can be default."
            />
          }
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>

        <Alert
          type="info"
          showIcon
          message="API keys are stored encrypted"
          description="Keys are encrypted at rest and never logged. They're only used server-side to call the AI provider's API."
          style={{ marginBottom: 16 }}
        />

        {!testPassed && (
          <Alert
            type="warning"
            showIcon
            message="Test the connection before saving"
            description={'Click "Test connection" above with a passing result to enable Save.'}
            style={{ marginBottom: 16 }}
          />
        )}
        <Button type="primary" htmlType="submit" loading={saving} disabled={!testPassed}>
          Save
        </Button>
      </Form>
    </Drawer>
  );
};

// ---------------------------------------------------------------------------
// Slack Configuration drawer
// ---------------------------------------------------------------------------

interface SlackFormValues {
  name: string;
  webhook_url: string;
  channel: string;
  daily_report_enabled: boolean;
  daily_report_cron: string;
  daily_report_use_ai: boolean;
  enabled: boolean;
  is_default: boolean;
}

const SlackDrawer = ({
  value,
  saving,
  onClose,
  onSave
}: {
  value: SlackConfigRead | "new" | null;
  saving: boolean;
  onClose: () => void;
  onSave: (body: SlackConfigWrite & { id?: string }) => void;
}) => {
  const { api } = useAuth();
  const [form] = Form.useForm<SlackFormValues>();
  const isEditing = value && value !== "new";
  const [testPassed, setTestPassed] = useState(false);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getFieldsValue(true) as SlackFormValues;
      return testSlackConfigValues(api, {
        webhook_url: current.webhook_url || "",
        channel: current.channel || undefined,
        config_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: () => {
      setTestPassed(true);
      message.success("Test message sent to Slack!");
    },
    onError: (error) => {
      setTestPassed(false);
      message.error((error as Error).message);
    }
  });

  return (
    <Drawer
      title={isEditing ? "Edit Slack connection" : "New Slack connection"}
      open={Boolean(value)}
      onClose={onClose}
      size={520}
      destroyOnHidden
    >
      <Form<SlackFormValues>
        form={form}
        layout="vertical"
        initialValues={{
          name: isEditing ? value.name : "",
          webhook_url: "",
          channel: isEditing ? (value.channel ?? "") : "",
          daily_report_enabled: isEditing ? value.daily_report_enabled : true,
          daily_report_cron: isEditing ? value.daily_report_cron : "0 9 * * *",
          daily_report_use_ai: isEditing ? value.daily_report_use_ai : true,
          enabled: isEditing ? value.enabled : true,
          is_default: isEditing ? value.is_default : false
        }}
        onValuesChange={() => {
          setTestPassed(false);
          testConnection.reset();
        }}
        onFinish={(values) => {
          const body: SlackConfigWrite & { id?: string } = {
            name: values.name,
            webhook_url: values.webhook_url || undefined, // Don't send empty string (keeps existing)
            channel: values.channel || undefined,
            daily_report_enabled: values.daily_report_enabled,
            daily_report_cron: values.daily_report_cron,
            daily_report_use_ai: values.daily_report_use_ai,
            enabled: values.enabled,
            is_default: values.is_default
          };
          if (isEditing) {
            body.id = value.id;
          }
          onSave(body);
        }}
      >
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="daily-reports" />
        </Form.Item>

        <Form.Item
          name="webhook_url"
          label={
            <HintLabel
              label="Webhook URL"
              tooltip={
                isEditing && value.has_webhook
                  ? "Leave blank to keep the existing webhook. Enter a new value to replace it."
                  : "Create an incoming webhook in your Slack workspace settings."
              }
            />
          }
          rules={[{ required: !isEditing, message: "Webhook URL is required for new connections" }]}
        >
          <Input.Password
            placeholder={isEditing && value.has_webhook ? "••••••••••••••••" : "https://hooks.slack.com/services/T.../B.../xxx"}
            autoComplete="off"
          />
        </Form.Item>

        <Form.Item
          name="channel"
          label={
            <HintLabel
              label="Channel override (optional)"
              tooltip="Override the default channel configured in the webhook. Leave blank to use the webhook's default channel."
            />
          }
        >
          <Input placeholder="#cryo-alerts" />
        </Form.Item>

        <Form.Item>
          <Button loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
            Send test message
          </Button>
        </Form.Item>

        <Divider>Daily Report Settings</Divider>

        <Form.Item name="daily_report_enabled" label="Enable daily reports" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Form.Item
          name="daily_report_cron"
          rules={[{ validator: cronFormValidator() }]}
          label={
            <HintLabel
              label="Report schedule"
              tooltip="Cron expression for when to send the daily report. Times are in UTC."
            />
          }
        >
          <CronInput placeholder="0 9 * * *" />
        </Form.Item>

        <Form.Item
          name="daily_report_use_ai"
          label={
            <HintLabel
              label="Include AI summary"
              tooltip="Use the configured AI provider to generate an executive summary of the report."
            />
          }
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>

        <Divider />

        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Form.Item
          name="is_default"
          label={
            <HintLabel
              label="Set as default"
              tooltip="The default Slack connection is used for automated reports. Only one can be default."
            />
          }
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>

        <Alert
          type="info"
          showIcon
          message="How to create a Slack webhook"
          description={
            <ol style={{ margin: 0, paddingLeft: 16 }}>
              <li>Go to your Slack workspace settings</li>
              <li>Navigate to Apps → Manage → Custom Integrations → Incoming Webhooks</li>
              <li>Click "Add to Slack" and select the channel</li>
              <li>Copy the webhook URL and paste it above</li>
            </ol>
          }
          style={{ marginBottom: 16 }}
        />

        {!testPassed && (
          <Alert
            type="warning"
            showIcon
            message="Send a test message before saving"
            description={'Click "Send test message" above with a passing result to enable Save.'}
            style={{ marginBottom: 16 }}
          />
        )}
        <Button type="primary" htmlType="submit" loading={saving} disabled={!testPassed}>
          Save
        </Button>
      </Form>
    </Drawer>
  );
};
