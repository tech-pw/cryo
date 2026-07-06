import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, InputNumber, Row, Segmented, Select, Space, Switch, Table, Tooltip, Typography, message } from "antd";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  cancelRun,
  getScannerConfig,
  getScannerStatus,
  getSourceNamespaces,
  getSourceNamespaceTables,
  listRuns,
  listSources,
  listTables,
  patchScannerConfig,
  triggerScan
} from "../api/client";
import type { RunRead, ScannerConfigUpdate, SourceRead } from "../api/types";
import { leaf } from "../lib/predicate";
import { CronInput } from "../components/CronInput";
import { ConfirmAction } from "../components/confirm";
import { EmptyState, PageHeader, StatStrip } from "../components/common";
import { ScanConsole } from "../components/ScanConsole";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { cronFormValidator } from "../lib/cron";
import {
  clearAllLakehousePendingScans,
  readConnectionScanNotice,
  type ConnectionScanNotice
} from "../lib/connectionScanPrompt";
import { formatNumber, formatTime } from "../lib/format";
import { qualifiedTableName } from "../lib/tableLabels";
import { useEffect, useMemo, useState } from "react";

type ScanScope = "all" | "source" | "schemas" | "tables";

const ACTIVE_STATUSES = new Set(["accepted", "queued", "running"]);

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const summaryNumber = (run: RunRead, key: string): number => {
  const value = run.summary?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
};

const targetLabel = (run: RunRead): string => {
  const value = run.summary?.target_label;
  return typeof value === "string" ? value : "Scan run";
};

const SCOPE_OPTIONS: Array<{ label: string; value: ScanScope }> = [
  { label: "All", value: "all" },
  { label: "Lakehouse", value: "source" },
  { label: "Schemas", value: "schemas" },
  { label: "Tables", value: "tables" }
];

const ScanScopeCard = ({
  canOperate,
  hasActiveRun,
  onQueued
}: {
  canOperate: boolean;
  hasActiveRun: boolean;
  onQueued: (runId: string) => void;
}) => {
  const { api, token } = useAuth();
  const [scope, setScope] = useState<ScanScope>("all");
  const [sourceId, setSourceId] = useState<string | undefined>();
  const [schemaNames, setSchemaNames] = useState<string[]>([]);
  const [tableIds, setTableIds] = useState<string[]>([]);
  // Names of live-but-never-scanned tables selected for targeting -- sent as table_names, not
  // table_ids, since no Cryo-assigned id exists for them yet (see TableNameRef).
  const [unscannedTableSelection, setUnscannedTableSelection] = useState<string[]>([]);

  const needsSource = scope === "source" || scope === "schemas" || scope === "tables";

  const sources = useQuery({
    queryKey: ["sources-for-scan", token],
    queryFn: () => listSources(api),
    enabled: needsSource
  });

  // Fleets can have thousands of tables per lakehouse; this is a best-effort picker, not a
  // guarantee of completeness -- see the truncation warning below when it's capped. Only
  // populated once a lakehouse has actually been scanned at least once (Cryo only knows real
  // table names/ids from its own persisted Table rows).
  const sourceTables = useQuery({
    queryKey: ["source-tables-for-scan", token, sourceId],
    queryFn: () => listTables(api, { predicate: leaf("source_id", "=", sourceId as string), limit: 500 }),
    enabled: Boolean(sourceId) && (scope === "schemas" || scope === "tables")
  });

  // Live namespace list straight from the catalog (same cheap call "Test connection" uses) --
  // unlike sourceTables above, this works even before a lakehouse has ever been scanned, so
  // narrowing by schema doesn't force a scan-then-rescan round trip just to see what's there.
  const sourceNamespaces = useQuery({
    queryKey: ["source-namespaces-for-scan", token, sourceId],
    queryFn: () => getSourceNamespaces(api, sourceId as string),
    enabled: Boolean(sourceId) && (scope === "schemas" || scope === "tables")
  });

  // Live table names within exactly one namespace -- only fetched once "tables" scope has
  // narrowed to a single schema, so this stays a single cheap call, not a full lakehouse walk.
  // These are for *visibility* only: selecting a specific table still needs its Cryo-assigned
  // id, which only exists after that table has actually been scanned once.
  const singleNarrowedSchema = scope === "tables" && schemaNames.length === 1 ? schemaNames[0] : undefined;
  const namespaceTables = useQuery({
    queryKey: ["source-namespace-tables-for-scan", token, sourceId, singleNarrowedSchema],
    queryFn: () => getSourceNamespaceTables(api, sourceId as string, singleNarrowedSchema as string),
    enabled: Boolean(sourceId) && Boolean(singleNarrowedSchema)
  });

  const schemaOptions = useMemo(() => {
    const fromCatalog = sourceNamespaces.data?.namespaces ?? [];
    const fromScannedTables = (sourceTables.data?.items ?? []).map((t) => t.schema_name);
    const names = new Set([...fromCatalog, ...fromScannedTables]);
    return Array.from(names)
      .sort()
      .map((name) => ({ label: name, value: name }));
  }, [sourceNamespaces.data, sourceTables.data]);

  // Live tables that exist in the narrowed-to schema but haven't been scanned yet -- selectable
  // via table_names (no Cryo id exists for them yet, so they can't go through table_ids).
  const unscannedTablesInSchema = useMemo(() => {
    if (!singleNarrowedSchema || !namespaceTables.data?.tables.length) return [];
    const known = new Set(
      (sourceTables.data?.items ?? [])
        .filter((t) => t.schema_name === singleNarrowedSchema)
        .map((t) => t.table_name)
    );
    return namespaceTables.data.tables.filter((name) => !known.has(name));
  }, [singleNarrowedSchema, namespaceTables.data, sourceTables.data]);

  const tableOptions = useMemo(
    () =>
      (sourceTables.data?.items ?? [])
        .filter((t) => schemaNames.length === 0 || schemaNames.includes(t.schema_name))
        .map((t) => ({ label: qualifiedTableName(t), value: t.id })),
    [sourceTables.data, schemaNames]
  );

  const resetSelection = () => {
    setSourceId(undefined);
    setSchemaNames([]);
    setTableIds([]);
    setUnscannedTableSelection([]);
  };

  const trigger = useMutation({
    mutationFn: () => {
      if (scope === "source") {
        return triggerScan(api, { scope: "source", source_id: sourceId, full_scan: true, trigger: "web-scanner" });
      }
      if (scope === "schemas") {
        return triggerScan(api, {
          scope: "schemas",
          source_id: sourceId,
          schema_names: schemaNames,
          full_scan: true,
          trigger: "web-scanner"
        });
      }
      if (scope === "tables") {
        return triggerScan(api, {
          scope: "tables",
          source_id: sourceId,
          table_ids: tableIds,
          table_names: unscannedTableSelection.map((table_name) => ({
            schema_name: singleNarrowedSchema as string,
            table_name
          })),
          full_scan: true,
          trigger: "web-scanner"
        });
      }
      return triggerScan(api, { scope: "all", full_scan: true, trigger: "web-scanner" });
    },
    onSuccess: (result) => {
      message.success(result.message || `Scan queued: ${result.run_id}`);
      onQueued(result.run_id);
    },
    onError: (error) => message.error((error as Error).message)
  });

  const canSubmit =
    scope === "all" ||
    (scope === "source" && Boolean(sourceId)) ||
    (scope === "schemas" && Boolean(sourceId) && schemaNames.length > 0) ||
    (scope === "tables" && (tableIds.length > 0 || unscannedTableSelection.length > 0));

  const disabledReason = !canOperate
    ? "You don't have permission to trigger scans (viewer role)."
    : hasActiveRun
      ? "A scan is already running -- cancel it (above) or wait for it to finish before starting another."
      : !canSubmit
        ? "Select a valid scope before triggering."
        : undefined;

  // Only relevant to "tables" scope -- "schemas" is populated from the full, uncapped live
  // namespace list (sourceNamespaces), not the 500-row table fetch this warns about.
  const truncated =
    scope === "tables" &&
    sourceTables.data &&
    sourceTables.data.total > sourceTables.data.items.length;

  return (
    <Card title="Trigger a scan">
      <Space direction="vertical" size={12} className="full-width">
        <Form layout="vertical">
          <Form.Item label="Scope">
            <Segmented<ScanScope>
              value={scope}
              onChange={(value) => {
                setScope(value);
                resetSelection();
              }}
              options={SCOPE_OPTIONS}
              block
            />
          </Form.Item>

          {needsSource ? (
            <Form.Item label="Lakehouse" required>
              <Select
                showSearch
                allowClear
                placeholder="Select a lakehouse"
                loading={sources.isLoading}
                value={sourceId}
                onChange={(value) => {
                  setSourceId(value);
                  setSchemaNames([]);
                  setTableIds([]);
                  setUnscannedTableSelection([]);
                }}
                filterOption={(input, option) => (option?.label ?? "").toLowerCase().includes(input.toLowerCase())}
                options={(sources.data ?? []).map((s: SourceRead) => ({ label: s.name, value: s.id }))}
                style={{ width: "100%" }}
              />
            </Form.Item>
          ) : null}

          {scope === "schemas" && sourceId ? (
            <Form.Item label="Schemas" required>
              <Select
                mode="multiple"
                allowClear
                loading={sourceNamespaces.isLoading}
                value={schemaNames}
                onChange={setSchemaNames}
                options={schemaOptions}
                placeholder="Select one or more schemas"
                style={{ width: "100%" }}
              />
            </Form.Item>
          ) : null}

          {scope === "tables" && sourceId ? (
            <>
              <Form.Item label="Narrow by schema (optional)">
                <Select
                  mode="multiple"
                  allowClear
                  loading={sourceNamespaces.isLoading}
                  value={schemaNames}
                  onChange={(value) => {
                    setSchemaNames(value);
                    setUnscannedTableSelection([]);
                  }}
                  options={schemaOptions}
                  placeholder="All schemas"
                  style={{ width: "100%" }}
                />
              </Form.Item>

              {tableOptions.length > 0 ? (
                <Form.Item label="Tables" required>
                  <Select
                    mode="multiple"
                    showSearch
                    allowClear
                    loading={sourceTables.isLoading}
                    value={tableIds}
                    onChange={setTableIds}
                    options={tableOptions}
                    filterOption={(input, option) =>
                      (option?.label ?? "").toLowerCase().includes(input.toLowerCase())
                    }
                    placeholder="Select one or more tables"
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              ) : null}

              {unscannedTablesInSchema.length > 0 ? (
                <Form.Item
                  label={`Not-yet-scanned tables in "${singleNarrowedSchema}"`}
                  extra={`Selecting any of these scans just them by name -- Cryo will assign each a real id as part of this run.`}
                >
                  <Select
                    mode="multiple"
                    showSearch
                    allowClear
                    value={unscannedTableSelection}
                    onChange={setUnscannedTableSelection}
                    options={unscannedTablesInSchema.map((name) => ({ label: name, value: name }))}
                    filterOption={(input, option) =>
                      (option?.label ?? "").toLowerCase().includes(input.toLowerCase())
                    }
                    placeholder="Select one or more tables to scan for the first time"
                    style={{ width: "100%" }}
                  />
                </Form.Item>
              ) : null}

              {tableOptions.length === 0 && schemaNames.length === 0 ? (
                <Alert
                  type="info"
                  showIcon
                  message="No tables known yet"
                  description={
                    <Space direction="vertical" size={8}>
                      <Typography.Text>
                        Cryo only knows individual table ids once they've been scanned at least
                        once. Narrow by a schema above to see what's actually in the catalog, or
                        scan this whole lakehouse first.
                      </Typography.Text>
                      <Button
                        size="small"
                        onClick={() => {
                          setScope("source");
                          setSchemaNames([]);
                          setTableIds([]);
                          setUnscannedTableSelection([]);
                        }}
                      >
                        Switch to "One lakehouse" scope
                      </Button>
                    </Space>
                  }
                  style={{ marginBottom: 16, width: "100%" }}
                />
              ) : null}
            </>
          ) : null}

          {truncated ? (
            <Alert
              type="warning"
              showIcon
              message={`Only showing the first ${sourceTables.data?.items.length} of ${sourceTables.data?.total} tables in this lakehouse -- narrow by schema to see the rest.`}
              style={{ marginBottom: 16, width: "100%" }}
            />
          ) : null}

          <ConfirmAction
            title="Trigger this scan?"
            description={
              scope === "all"
                ? "Queues a full fleet scan across every registered table. This can be expensive on large catalogs."
                : "Queues a scan for the selected scope."
            }
            onConfirm={() => trigger.mutate()}
          >
            <Tooltip title={disabledReason}>
              <span style={{ display: "block", width: "100%" }}>
                <Button
                  type="primary"
                  loading={trigger.isPending}
                  disabled={!canOperate || !canSubmit || hasActiveRun}
                  style={{ width: "100%" }}
                >
                  Trigger scan
                </Button>
              </span>
            </Tooltip>
          </ConfirmAction>
        </Form>
      </Space>
    </Card>
  );
};

export const Scans = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { api, role, token } = useAuth();
  const [form] = Form.useForm<ScannerConfigUpdate>();
  const canOperate = role !== "viewer";
  const [connectionNotice, setConnectionNotice] = useState<ConnectionScanNotice | null>(() =>
    readConnectionScanNotice(location.state)
  );

  useEffect(() => {
    const notice = readConnectionScanNotice(location.state);
    if (notice) {
      setConnectionNotice(notice);
    }
  }, [location.state]);

  const config = useQuery({
    queryKey: ["scanner-config", token],
    queryFn: () => getScannerConfig(api)
  });
  const status = useQuery({
    queryKey: ["scanner-status", token],
    queryFn: () => getScannerStatus(api),
    refetchInterval: (query) => {
      const active = query.state.data?.active_run;
      return active && ACTIVE_STATUSES.has(active.status) ? 5000 : 15000;
    }
  });
  const runs = useQuery({
    queryKey: ["scan-runs", token],
    queryFn: () => listRuns(api, { kind: "scan", limit: 100 }),
    refetchInterval: (query) => {
      const active = (query.state.data ?? []).some((run) => ACTIVE_STATUSES.has(run.status));
      return active ? 5000 : 15000;
    }
  });

  useEffect(() => {
    if (config.data) {
      form.setFieldsValue(config.data);
    }
  }, [config.data, form]);

  const saveConfig = useMutation({
    mutationFn: (values: ScannerConfigUpdate) => patchScannerConfig(api, values),
    onSuccess: () => {
      message.success("Scanner schedule saved");
      void queryClient.invalidateQueries({ queryKey: ["scanner-config"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const scanAll = useMutation({
    mutationFn: () => triggerScan(api, { scope: "all", full_scan: true, trigger: "web-scanner" }),
    onSuccess: (result) => {
      message.success(result.message || `Scan queued: ${result.run_id}`);
      clearAllLakehousePendingScans();
      setConnectionNotice(null);
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      navigate(`/scans/${result.run_id}`, { replace: true, state: {} });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const cancelActiveRun = useMutation({
    mutationFn: (id: string) => cancelRun(api, id),
    onSuccess: () => {
      message.success("Scan cancelled");
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const scanNoticeSource = useMutation({
    mutationFn: (sourceId: string) =>
      triggerScan(api, { scope: "source", source_id: sourceId, full_scan: true, trigger: "web-scanner" }),
    onSuccess: (result) => {
      message.success(result.message || `Scan queued: ${result.run_id}`);
      clearAllLakehousePendingScans();
      setConnectionNotice(null);
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      navigate(`/scans/${result.run_id}`, { replace: true, state: {} });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const activeRun = status.data?.active_run;
  const latestRun = status.data?.latest_run;

  const tableColumns = useMemo(() => {
    const baseColumns = [
      {
        title: "Run",
        width: 90,
        render: (_: any, row: RunRead) => (
          <Link to={`/scans/${row.id}`}>
            <Typography.Text code className="mono-id">{shortId(row.id)}</Typography.Text>
          </Link>
        )
      },
      {
        title: "Target",
        render: (_: any, row: RunRead) => (
          <Typography.Text ellipsis={{ tooltip: true }} style={{ maxWidth: runId ? 110 : "none" }}>
            {targetLabel(row)}
          </Typography.Text>
        )
      },
      {
        title: "Status",
        dataIndex: "status",
        width: 100,
        render: (value: string) => <StatusTag status={value} />
      }
    ];

    if (runId) {
      return [
        ...baseColumns,
        {
          title: "Time",
          dataIndex: "created_at",
          width: 90,
          render: (value: string) => {
            const str = formatTime(value);
            return <span style={{ fontSize: "12px" }}>{str.includes(", ") ? str.split(", ")[1] : str}</span>;
          }
        }
      ];
    }

    return [
      ...baseColumns,
      { title: "Trigger", dataIndex: "trigger", width: 110, render: (value: string) => value ?? "-" },
      { title: "Scanned", width: 85, align: "right" as const, render: (_: any, row: RunRead) => formatNumber(summaryNumber(row, "scanned")) },
      { title: "Skipped", width: 85, align: "right" as const, render: (_: any, row: RunRead) => formatNumber(summaryNumber(row, "skipped")) },
      { title: "Failed", width: 85, align: "right" as const, render: (_: any, row: RunRead) => formatNumber(summaryNumber(row, "failed")) },
      { title: "Created", dataIndex: "created_at", width: 140, render: formatTime }
    ];
  }, [runId]);

  return (
    <div>
      <PageHeader
        title="Scanner"
        subtitle="Schedule catalog scans, trigger ad-hoc refreshes, and inspect scan progress."
        actions={
          <Space>
            <Link to="/tables">
              <Button>Open table explorer</Button>
            </Link>
            <Link to="/runs">
              <Button>Open runs</Button>
            </Link>
            <ConfirmAction
              title="Scan all tables now?"
              description="Queues a full fleet scan across every registered table. This can be expensive on large catalogs."
              onConfirm={() => scanAll.mutate()}
            >
              <Tooltip
                title={
                  !canOperate
                    ? "You don't have permission to trigger scans (viewer role)."
                    : activeRun
                      ? "A scan is already running -- cancel it below or wait for it to finish before starting another."
                      : undefined
                }
              >
                <span>
                  <Button
                    type="primary"
                    loading={scanAll.isPending}
                    disabled={!canOperate || Boolean(activeRun)}
                  >
                    Scan all tables now
                  </Button>
                </span>
              </Tooltip>
            </ConfirmAction>
          </Space>
        }
      />

      {connectionNotice ? (
        <Alert
          className="page-alert"
          type="success"
          showIcon
          closable
          message={
            connectionNotice.kind === "lakehouse"
              ? `Lakehouse "${connectionNotice.name}" is connected`
              : `Engine "${connectionNotice.name}" is connected`
          }
          description={
            connectionNotice.kind === "lakehouse" ? (
              <Space direction="vertical" size={8}>
                <Typography.Text>
                  Run a catalog scan to discover tables and populate health grades for this lakehouse.
                </Typography.Text>
                <Space>
                  <ConfirmAction
                    title={`Scan "${connectionNotice.name}" now?`}
                    description="Queues a scan of just this lakehouse -- other connected lakehouses aren't touched."
                    onConfirm={() => scanNoticeSource.mutate(connectionNotice.id)}
                  >
                    <Button
                      type="primary"
                      loading={scanNoticeSource.isPending}
                      disabled={!canOperate || Boolean(activeRun)}
                    >
                      Scan this lakehouse now
                    </Button>
                  </ConfirmAction>
                  <Button type="link" onClick={() => navigate("/connections")}>
                    Back to Connections
                  </Button>
                </Space>
              </Space>
            ) : (
              <Space direction="vertical" size={8}>
                <Typography.Text>
                  Scan your lakehouse catalog first so Cryo knows which tables exist. Then assign a preset to
                  run maintenance on this engine.
                </Typography.Text>
                <Button type="link" onClick={() => navigate("/connections")}>
                  Back to Connections
                </Button>
              </Space>
            )
          }
          onClose={() => {
            setConnectionNotice(null);
            navigate(location.pathname, { replace: true, state: {} });
          }}
        />
      ) : null}

      <Row gutter={[12, 12]} align="stretch">
        {/* Left Column: Operational Hub */}
        <Col xs={24} xl={16} style={{ display: "flex" }}>
          <Space direction="vertical" size={12} className="full-width" style={{ display: "flex", flex: 1 }}>
            <Card title="Scanner status" loading={status.isLoading}>
              <Space direction="vertical" size={12} className="full-width">
                <StatStrip
                  items={[
                    { label: "Tables with stats", value: `${formatNumber(status.data?.tables_with_stats)} / ${formatNumber(status.data?.total_tables)}` },
                    { label: "Queue depth", value: formatNumber(status.data?.queue_depth ?? null) },
                    { label: "Next scheduled scan", value: formatTime(status.data?.next_scan_at) },
                    { label: "Latest table scan", value: formatTime(status.data?.latest_table_scan_at) }
                  ]}
                />
                <Alert
                  type={activeRun ? "info" : "warning"}
                  showIcon
                  icon={<div className={activeRun ? "pulse-dot" : "pulse-dot warning"} style={{ marginInlineEnd: 4 }} />}
                  message={
                    activeRun ? (
                      <Space>
                        <Typography.Text strong>Active scan in progress</Typography.Text>
                        <Link to={`/scans/${activeRun.id}`}>
                          <Typography.Text code className="mono-id">{shortId(activeRun.id)}</Typography.Text>
                        </Link>
                        <StatusTag status={activeRun.status} />
                      </Space>
                    ) : (
                      <Typography.Text strong>No active scan run</Typography.Text>
                    )
                  }
                  description={
                    <Typography.Text type="secondary" style={{ fontSize: "12px" }}>
                      {activeRun
                        ? "Discovering schemas and compiling health statistics. Click the Run ID or select it below to view live logs."
                        : latestRun
                          ? `Ready for ad-hoc triggers. Latest run ${shortId(latestRun.id)} finished ${formatTime(latestRun.finished_at)}.`
                          : "Ready for ad-hoc triggers."}
                    </Typography.Text>
                  }
                  action={
                    activeRun && canOperate ? (
                      <ConfirmAction
                        title="Cancel this scan?"
                        description="Stops the in-progress run so a new scan can be triggered. Already-scanned tables keep their results."
                        onConfirm={() => cancelActiveRun.mutate(activeRun.id)}
                      >
                        <Button size="small" danger loading={cancelActiveRun.isPending}>
                          Cancel scan
                        </Button>
                      </ConfirmAction>
                    ) : undefined
                  }
                  style={{ width: "100%" }}
                />
              </Space>
            </Card>

            {runId ? (
              <Card
                title={
                  <Space>
                    <span>Scan progress</span>
                    <Typography.Text type="secondary">{shortId(runId)}</Typography.Text>
                  </Space>
                }
              >
                <ScanConsole runId={runId} />
              </Card>
            ) : null}

            <Card title="Recent scans" style={{ flex: 1 }}>
              <Table<RunRead>
                size="small"
                rowKey="id"
                loading={runs.isLoading}
                dataSource={runs.data ?? []}
                pagination={{ pageSize: 12 }}
                onRow={(row) => ({ onClick: () => navigate(`/scans/${row.id}`) })}
                columns={tableColumns}
                locale={{
                  emptyText: (
                    <EmptyState
                      title="No scan runs yet"
                      description="Scheduled and ad-hoc scanner runs will appear here."
                    />
                  )
                }}
              />
            </Card>
          </Space>
        </Col>

        {/* Right Column: Controls & Configuration */}
        <Col xs={24} xl={8} style={{ display: "flex" }}>
          <Space direction="vertical" size={12} className="full-width" style={{ display: "flex", flex: 1 }}>
            <ScanScopeCard
              canOperate={canOperate}
              hasActiveRun={Boolean(activeRun)}
              onQueued={(runId) => {
                void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
                void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
                navigate(`/scans/${runId}`);
              }}
            />

            <Card title="Schedule" style={{ flex: 1 }}>
              <Form<ScannerConfigUpdate>
                form={form}
                layout="vertical"
                disabled={!canOperate}
                onFinish={(values) => saveConfig.mutate(values)}
              >
                <Row gutter={12}>
                  <Col span={12}>
                    <Form.Item name="enabled" label="Scheduled scans" valuePropName="checked">
                      <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name="full_scan_default" label="Force rescan" valuePropName="checked">
                      <Switch checkedChildren="Yes" unCheckedChildren="No" style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>

                <Form.Item
                  name="schedule_cron"
                  label={
                    <Tooltip title="Cron expressions here are evaluated in UTC. Maintenance off-peak windows elsewhere in Cryo use IST (UTC+5:30). Example: 30 18 * * * UTC = midnight IST.">
                      <span>Cron schedule</span>
                    </Tooltip>
                  }
                  rules={[{ validator: cronFormValidator() }]}
                >
                  <CronInput />
                </Form.Item>
                <Tooltip title="Scheduled scans run in UTC. Translate to IST when aligning with off-peak maintenance windows (UTC+5:30).">
                  <Typography.Paragraph type="secondary" style={{ marginTop: -8, fontSize: "12px", marginBottom: 12 }}>
                    Timezone: UTC (see IST in tooltip)
                  </Typography.Paragraph>
                </Tooltip>

                <Row gutter={12}>
                  <Col span={12}>
                    <Form.Item name="num_chunks" label="Chunks" rules={[{ required: true }]}>
                      <InputNumber min={1} max={128} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name="max_concurrent_runs" label="Max concurrency">
                      <InputNumber min={1} max={4} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>

                <Form.Item name="cost_full_scan_default" label="Include full cost scan" valuePropName="checked">
                  <Switch checkedChildren="Yes" unCheckedChildren="No" />
                </Form.Item>

                <Button type="primary" htmlType="submit" loading={saveConfig.isPending} style={{ width: "100%", marginTop: 8 }}>
                  Save schedule
                </Button>
              </Form>
            </Card>
          </Space>
        </Col>
      </Row>
    </div>
  );
};
