import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Result,
  Skeleton,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createAwsCostInsightConfig,
  deleteAwsCostInsightConfig,
  getAwsCostInsightConfig,
  getCostByBucket,
  getCostDailySeries,
  getSizeByPrefix,
  ingestAwsCostInsightCur,
  ingestAwsCostInsightInventory,
  listSources,
  patchAwsCostInsightConfig
} from "../api/client";
import type {
  AWSCostInsightConfigRead,
  AWSCostInsightConfigWrite,
  CostByBucketResponse,
  CostDailySeriesResponse,
  SizeByPrefixResponse,
  SourceRead,
  UUID
} from "../api/types";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatTime } from "../lib/format";
import { OverviewPage } from "../components/layout/OverviewPage";
import {
  ChartFrame,
  EmptyState,
  PageHeader,
  QueryErrorAlert,
  StatStrip
} from "../components/common";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";

const CHART_COLORS = [
  cryoColors.primary,
  "#5b9ce8",
  "#7fb069",
  "#e7a23c",
  "#cc6677",
  "#8561c5",
  "#3d8eb9"
];

const ConfigForm = ({
  sourceId,
  existing,
  onSaved
}: {
  sourceId: UUID;
  existing: AWSCostInsightConfigRead | null;
  onSaved: (cfg: AWSCostInsightConfigRead) => void;
}) => {
  const { api } = useAuth();
  const [form] = Form.useForm<AWSCostInsightConfigWrite>();

  const save = useMutation({
    mutationFn: (values: AWSCostInsightConfigWrite) =>
      existing
        ? patchAwsCostInsightConfig(api, sourceId, values)
        : createAwsCostInsightConfig(api, sourceId, values),
    onSuccess: (cfg) => {
      message.success(existing ? "Cost insight config updated" : "Cost insight config created");
      onSaved(cfg);
    },
    onError: (err) => message.error((err as Error).message)
  });

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        cur_bucket: existing?.cur_bucket ?? "",
        cur_prefix: existing?.cur_prefix ?? "",
        cur_report_name: existing?.cur_report_name ?? "",
        fixture_path: existing?.fixture_path ?? "",
        inventory_bucket: existing?.inventory_bucket ?? "",
        inventory_prefix: existing?.inventory_prefix ?? "",
        inventory_format: existing?.inventory_format ?? "CSV",
        access_key_id: existing?.access_key_id ?? "",
        enabled: existing?.enabled ?? true
      }}
      key={existing?.id ?? "new"}
      onFinish={(values) => {
        const body: AWSCostInsightConfigWrite = {
          cur_bucket: values.cur_bucket || null,
          cur_prefix: values.cur_prefix || null,
          cur_report_name: values.cur_report_name || null,
          fixture_path: values.fixture_path || null,
          inventory_bucket: values.inventory_bucket || null,
          inventory_prefix: values.inventory_prefix || null,
          inventory_format: values.inventory_format || "CSV",
          access_key_id: values.access_key_id || null,
          enabled: values.enabled
        };
        if (values.secret_access_key) {
          body.secret_access_key = values.secret_access_key;
        }
        save.mutate(body);
      }}
    >
      <Typography.Paragraph type="secondary">
        Configure CUR delivery and S3 Inventory. Both are free (delivered reports) — no AWS API
        charges. Leave <code>fixture_path</code> set in dev/CI to load CUR from a local file.
      </Typography.Paragraph>

      <Form.Item name="cur_bucket" label="CUR delivery bucket">
        <Input placeholder="my-account-cur" autoComplete="off" />
      </Form.Item>
      <Form.Item name="cur_prefix" label="CUR prefix">
        <Input placeholder="cur-prod/cryo-monthly/" autoComplete="off" />
      </Form.Item>
      <Form.Item name="cur_report_name" label="CUR report name">
        <Input placeholder="cryo-monthly" autoComplete="off" />
      </Form.Item>
      <Form.Item
        name="fixture_path"
        label={
          <Tooltip title="Optional local path to a CUR CSV/JSON/parquet for dev/CI. When set, the ingestor reads this file instead of listing the CUR bucket.">
            Fixture path (dev/CI) <Typography.Text type="secondary">ⓘ</Typography.Text>
          </Tooltip>
        }
      >
        <Input placeholder="/path/to/cur.csv" autoComplete="off" />
      </Form.Item>

      <Form.Item name="inventory_bucket" label="S3 Inventory destination bucket">
        <Input placeholder="my-account-inventory" autoComplete="off" />
      </Form.Item>
      <Form.Item name="inventory_prefix" label="Inventory prefix">
        <Input placeholder="inventory/prod/" autoComplete="off" />
      </Form.Item>
      <Form.Item name="inventory_format" label="Inventory format">
        <Input placeholder="CSV" autoComplete="off" />
      </Form.Item>

      <Form.Item
        name="access_key_id"
        label={
          <Tooltip title="Leave blank to use IRSA / instance profile / ~/.aws/credentials.">
            Access key ID (optional) <Typography.Text type="secondary">ⓘ</Typography.Text>
          </Tooltip>
        }
      >
        <Input placeholder="AKIA..." autoComplete="off" />
      </Form.Item>
      <Form.Item
        name="secret_access_key"
        label={
          <Tooltip title="Stored encrypted at rest. Leave blank to keep the existing secret.">
            Secret access key (optional) <Typography.Text type="secondary">ⓘ</Typography.Text>
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
      <Button type="primary" htmlType="submit" loading={save.isPending}>
        {existing ? "Save changes" : "Create config"}
      </Button>
    </Form>
  );
};

const StatusTag = ({ config }: { config: AWSCostInsightConfigRead | null | undefined }) => {
  if (!config) {
    return <Tag color="default">Not configured</Tag>;
  }
  if (!config.enabled) {
    return <Tag color="default">Disabled</Tag>;
  }
  if (config.last_ingest_ok === true) {
    return (
      <Tooltip
        title={
          config.last_ingested_through
            ? `Through ${config.last_ingested_through}`
            : "Last ingest OK"
        }
      >
        <Tag icon={<CheckCircleOutlined />} color="green">
          Ingesting
        </Tag>
      </Tooltip>
    );
  }
  if (config.last_ingest_ok === false) {
    return (
      <Tooltip title={config.last_ingest_error ?? "Last ingest failed"}>
        <Tag icon={<CloseCircleOutlined />} color="red">
          Ingest failed
        </Tag>
      </Tooltip>
    );
  }
  return <Tag color="processing">Configured · untested</Tag>;
};

const SourceCard = ({
  source,
  config,
  onManage
}: {
  source: SourceRead;
  config: AWSCostInsightConfigRead | undefined | null;
  onManage: () => void;
}) => {
  const configured = !!config;
  return (
    <Card
      size="small"
      className="cost-insights-source-card"
      title={
        <Space size={8}>
          <CloudOutlined />
          <Typography.Text strong>{source.name}</Typography.Text>
          <Tag>{source.type}</Tag>
        </Space>
      }
      extra={<StatusTag config={config} />}
    >
      <Space orientation="vertical" size={10} className="full-width">
        <Space size={20} wrap>
          <Statistic
            title="CUR bucket"
            value={config?.cur_bucket || "—"}
            valueStyle={{ fontSize: 14 }}
          />
          <Statistic
            title="Inventory bucket"
            value={config?.inventory_bucket || "—"}
            valueStyle={{ fontSize: 14 }}
          />
          <Statistic
            title="Last ingest"
            value={config?.last_ingested_at ? formatTime(config.last_ingested_at) : "—"}
            valueStyle={{ fontSize: 14 }}
          />
        </Space>
        <Space wrap>
          <Button size="small" type="primary" onClick={onManage}>
            {configured ? "Manage" : "Configure CUR"}
          </Button>
        </Space>
      </Space>
    </Card>
  );
};

export const CostInsights = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [managingSourceId, setManagingSourceId] = useState<UUID | null>(null);
  const [viewingSourceId, setViewingSourceId] = useState<UUID | null>(null);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);

  const sources = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api),
    retry: 1
  });

  const sourceIds = useMemo(() => (sources.data ?? []).map((s) => s.id), [sources.data]);

  const configResults = useQueries({
    queries: sourceIds.map((id) => ({
      queryKey: ["aws-cost-insight-config", id, api.token],
      queryFn: () => getAwsCostInsightConfig(api, id),
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 30_000
    }))
  });

  const configs: Record<UUID, AWSCostInsightConfigRead | null> = useMemo(() => {
    const map: Record<UUID, AWSCostInsightConfigRead | null> = {};
    sourceIds.forEach((id, i) => {
      const r = configResults[i];
      if (r?.isError) {
        const status = (r.error as { status?: number } | null)?.status;
        map[id] = status === 404 ? null : null;
      } else if (r?.data) {
        map[id] = r.data;
      } else {
        map[id] = null;
      }
    });
    return map;
  }, [sourceIds, configResults]);

  const configuredSources = sourceIds.filter((id) => configs[id]?.enabled);
  const viewingSource = useMemo(
    () => (sources.data ?? []).find((s) => s.id === viewingSourceId) ?? null,
    [sources.data, viewingSourceId]
  );

  const rangeQuery = useMemo(() => {
    if (!range) return undefined;
    const [start, end] = range;
    return {
      start: start ? start.format("YYYY-MM-DD") : undefined,
      end: end ? end.format("YYYY-MM-DD") : undefined
    };
  }, [range]);

  const byBucket = useQuery<CostByBucketResponse>({
    queryKey: ["cost-insight-by-bucket", viewingSourceId, rangeQuery, api.token],
    queryFn: () => getCostByBucket(api, viewingSourceId as UUID, rangeQuery),
    enabled: viewingSourceId !== null
  });

  const daily = useQuery<CostDailySeriesResponse>({
    queryKey: ["cost-insight-daily", viewingSourceId, rangeQuery, api.token],
    queryFn: () => getCostDailySeries(api, viewingSourceId as UUID, rangeQuery),
    enabled: viewingSourceId !== null
  });

  const byPrefix = useQuery<SizeByPrefixResponse>({
    queryKey: ["cost-insight-by-prefix", viewingSourceId, api.token],
    queryFn: () => getSizeByPrefix(api, viewingSourceId as UUID, { limit: 50 }),
    enabled: viewingSourceId !== null
  });

  const ingestCur = useMutation({
    mutationFn: (sourceId: UUID) => ingestAwsCostInsightCur(api, sourceId),
    onSuccess: (res) => {
      if (res.error) {
        message.error(`CUR ingest failed: ${res.error}`);
      } else {
        message.success(
          `CUR ingested · ${res.rows_ingested.toLocaleString()} rows from ${res.files_processed} file(s)`
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["aws-cost-insight-config"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-insight-"] });
      void queryClient.invalidateQueries({ queryKey: ["aws-usage"] });
    }
  });

  const ingestInventory = useMutation({
    mutationFn: (sourceId: UUID) => ingestAwsCostInsightInventory(api, sourceId),
    onSuccess: (res) => {
      if (res.error) {
        message.error(`Inventory ingest failed: ${res.error}`);
      } else {
        message.success(
          `Inventory ingested · ${res.rows_ingested.toLocaleString()} objects from ${res.files_processed} file(s)`
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["aws-cost-insight-config"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-insight-by-prefix"] });
    }
  });

  const deleteConfig = useMutation({
    mutationFn: (sourceId: UUID) => deleteAwsCostInsightConfig(api, sourceId),
    onSuccess: () => {
      message.success("Config removed");
      void queryClient.invalidateQueries({ queryKey: ["aws-cost-insight-config"] });
    }
  });

  const refreshAll = useMutation({
    mutationFn: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sources"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-cost-insight-config"] }),
        queryClient.invalidateQueries({ queryKey: ["cost-insight-"] })
      ]);
    },
    onSuccess: () => message.success("Refreshed")
  });

  const isViewer = role === "viewer";
  const totalSpend = (byBucket.data?.total_cost_usd ?? 0).toFixed(2);
  const topBucket = byBucket.data?.items?.[0]?.bucket;
  const trackedPrefixes = byPrefix.data?.items?.length ?? 0;

  const stats = [
    {
      label: "Configured sources",
      value: configuredSources.length,
      tooltip: "Lakehouses with an enabled CUR / Inventory config."
    },
    {
      label: "Spend in range",
      value: formatCurrency(Number(totalSpend)),
      tooltip: "Sum of CUR line items across the selected range."
    },
    {
      label: "Top bucket",
      value: topBucket || "—",
      tooltip: "Highest-cost bucket in the current range."
    },
    {
      label: "Tracked prefixes",
      value: trackedPrefixes,
      tooltip: "Distinct table prefixes seen in the latest S3 Inventory."
    }
  ];

  return (
    <OverviewPage
      title="Cost insights"
      subtitle="Real AWS cost attribution from CUR (per-bucket, daily) and S3 Inventory (per-table). Free — uses delivered report files only."
      actions={
        <Space wrap>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refreshAll.mutate()}
            loading={refreshAll.isPending}
          >
            Refresh
          </Button>
        </Space>
      }
      loading={sources.isLoading}
      error={sources.error}
      errorTitle="Failed to load lakehouses"
      kpis={<StatStrip items={stats} />}
      preContent={
        <Space wrap>
          <span>Viewing:</span>
          <select
            value={viewingSourceId ?? ""}
            onChange={(e) => setViewingSourceId(e.target.value || null)}
            className="ant-select ant-select-single"
            style={{ minWidth: 200, padding: "4px 8px", border: "1px solid var(--line)" }}
          >
            <option value="">— pick a source —</option>
            {(sources.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <DatePicker.RangePicker
            value={range as [Dayjs, Dayjs] | null}
            onChange={(values) => setRange(values as [Dayjs | null, Dayjs | null] | null)}
            allowClear
          />
        </Space>
      }
    >
      <Space orientation="vertical" size={12} className="full-width">
        <Alert
          type="info"
          showIcon
          title="How this works"
          description={
            <>
              Cryo reads the CUR files AWS delivers to your own bucket (and S3 Inventory
              reports). No Cost Explorer API calls, no <code>GetMetricData</code>. Setup is
              one-time billing-admin: enable CUR delivery + an Inventory report for the
              lakehouse's prefix. See{" "}
              <Link to="/aws">AWS Integrations</Link> for the free-tier CloudWatch path.
            </>
          }
        />

        {!viewingSource ? (
          <EmptyState
            title="Pick a lakehouse above"
            description="Or configure CUR / Inventory for a source below to begin ingesting."
          />
        ) : (
          <Space orientation="vertical" size={12} className="full-width">
            <QueryErrorAlert error={byBucket.error} title="Failed to load CUR aggregation" />
            <QueryErrorAlert error={daily.error} title="Failed to load daily series" />
            <QueryErrorAlert error={byPrefix.error} title="Failed to load Inventory" />

            <Card
              size="small"
              title={
                <Space>
                  <span>Spend by bucket (CUR)</span>
                  {byBucket.data && (
                    <Tag color="processing">{formatCurrency(byBucket.data.total_cost_usd)}</Tag>
                  )}
                </Space>
              }
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<PlayCircleOutlined />}
                    onClick={() => viewingSource && ingestCur.mutate(viewingSource.id)}
                    loading={ingestCur.isPending}
                    disabled={isViewer}
                  >
                    Ingest CUR
                  </Button>
                </Space>
              }
            >
              {byBucket.isLoading ? (
                <Skeleton active />
              ) : (byBucket.data?.items?.length ?? 0) === 0 ? (
                <EmptyState
                  title="No CUR data yet"
                  description="Trigger an ingest or check the CUR delivery bucket + prefix."
                />
              ) : (
                <ChartFrame empty={false} ariaLabel="Cost by bucket">
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart
                      data={byBucket.data?.items ?? []}
                      margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                      <XAxis
                        dataKey="bucket"
                        tick={chartAxisTick}
                        angle={-25}
                        textAnchor="end"
                        height={70}
                      />
                      <YAxis tick={chartAxisTick} tickFormatter={(v) => `$${Number(v).toFixed(2)}`} />
                      <RechartsTooltip
                        {...chartTooltipProps}
                        formatter={(value) => formatCurrency(Number(value))}
                      />
                      <Bar dataKey="total_cost_usd" name="Cost">
                        {(byBucket.data?.items ?? []).map((entry, idx) => (
                          <Cell key={entry.bucket} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartFrame>
              )}
            </Card>

            <Card
              size="small"
              title="Daily cost trend"
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {daily.data?.items?.length ?? 0} days
                </Typography.Text>
              }
            >
              {daily.isLoading ? (
                <Skeleton active />
              ) : (daily.data?.items?.length ?? 0) === 0 ? (
                <EmptyState
                  title="No daily points yet"
                  description="Ingest CUR to populate the time series."
                />
              ) : (
                <ChartFrame empty={false} ariaLabel="Daily cost trend">
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart
                      data={daily.data?.items ?? []}
                      margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                      <XAxis
                        dataKey="usage_date"
                        tick={chartAxisTick}
                        tickFormatter={(v) => dayjs(v).format("MMM D")}
                      />
                      <YAxis tick={chartAxisTick} tickFormatter={(v) => `$${Number(v).toFixed(2)}`} />
                      <RechartsTooltip
                        {...chartTooltipProps}
                        labelFormatter={(v) => dayjs(v as string).format("MMM D, YYYY")}
                        formatter={(value) => formatCurrency(Number(value))}
                      />
                      <Line
                        type="monotone"
                        dataKey="total_cost_usd"
                        stroke={cryoColors.primary}
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </ChartFrame>
              )}
            </Card>

            <Card
              size="small"
              title="Size by table prefix (S3 Inventory)"
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<ThunderboltOutlined />}
                    onClick={() => viewingSource && ingestInventory.mutate(viewingSource.id)}
                    loading={ingestInventory.isPending}
                    disabled={isViewer}
                  >
                    Ingest inventory
                  </Button>
                </Space>
              }
            >
              {byPrefix.isLoading ? (
                <Skeleton active />
              ) : (byPrefix.data?.items?.length ?? 0) === 0 ? (
                <EmptyState
                  title="No inventory data yet"
                  description="Trigger an ingest, or set up S3 Inventory for the bucket prefix."
                />
              ) : (
                <Table
                  size="small"
                  rowKey="table_prefix"
                  pagination={false}
                  dataSource={byPrefix.data?.items ?? []}
                  columns={[
                    {
                      title: "Table prefix",
                      dataIndex: "table_prefix",
                      render: (value: string) => <Typography.Text code>{value}</Typography.Text>
                    },
                    {
                      title: "Size",
                      dataIndex: "size_bytes",
                      render: (value: number) => formatBytesGb(value),
                      align: "right"
                    },
                    {
                      title: "Objects",
                      dataIndex: "object_count",
                      align: "right"
                    },
                    {
                      title: "Last modified",
                      dataIndex: "last_modified",
                      render: (value?: string | null) => (value ? formatTime(value) : "—")
                    }
                  ]}
                />
              )}
            </Card>
          </Space>
        )}

        <PageHeader title="Sources" />
        {sources.data && sources.data.length === 0 ? (
          <EmptyState
            title="No lakehouses yet"
            description="Add a lakehouse source first."
            action={
              <Link to="/connections">
                <Button type="primary">Add a lakehouse</Button>
              </Link>
            }
          />
        ) : (
          <div className="cost-insights-grid">
            {(sources.data ?? []).map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                config={configs[source.id]}
                onManage={() => setManagingSourceId(source.id)}
              />
            ))}
          </div>
        )}
      </Space>

      <Drawer
        title={
          managingSourceId
            ? `CUR / Inventory · ${
                sources.data?.find((s) => s.id === managingSourceId)?.name ?? ""
              }`
            : "Configure"
        }
        width={560}
        open={managingSourceId !== null}
        onClose={() => setManagingSourceId(null)}
        destroyOnClose
      >
        {managingSourceId ? (
          <Space orientation="vertical" size={12} className="full-width">
            <ConfigForm
              sourceId={managingSourceId}
              existing={configs[managingSourceId] ?? null}
              onSaved={() => {
                void queryClient.invalidateQueries({ queryKey: ["aws-cost-insight-config"] });
                setManagingSourceId(null);
              }}
            />
            {configs[managingSourceId] ? (
              <>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Last ingest:{" "}
                  {configs[managingSourceId]?.last_ingested_at
                    ? formatTime(configs[managingSourceId]!.last_ingested_at!)
                    : "never"}
                </Typography.Text>
                <Space wrap>
                  <Button
                    icon={<PlayCircleOutlined />}
                    onClick={() => ingestCur.mutate(managingSourceId)}
                    loading={ingestCur.isPending}
                    disabled={isViewer}
                  >
                    Ingest CUR now
                  </Button>
                  <Button
                    icon={<ThunderboltOutlined />}
                    onClick={() => ingestInventory.mutate(managingSourceId)}
                    loading={ingestInventory.isPending}
                    disabled={isViewer}
                  >
                    Ingest Inventory now
                  </Button>
                  <Popconfirm
                    title="Remove cost insight config?"
                    description="Stops future ingests. Existing CUR / Inventory rows stay until pruned."
                    onConfirm={() => deleteConfig.mutate(managingSourceId)}
                    okText="Remove"
                  >
                    <Button danger icon={<DeleteOutlined />} disabled={isViewer}>
                      Remove
                    </Button>
                  </Popconfirm>
                </Space>
                {configs[managingSourceId]?.last_ingest_error ? (
                  <Alert
                    type="error"
                    showIcon
                    icon={<ExclamationCircleOutlined />}
                    title="Last ingest failed"
                    description={configs[managingSourceId]?.last_ingest_error}
                  />
                ) : null}
              </>
            ) : null}
          </Space>
        ) : (
          <Result icon={<ExperimentOutlined />} title="Pick a source" />
        )}
      </Drawer>
    </OverviewPage>
  );
};