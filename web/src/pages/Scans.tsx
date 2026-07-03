import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Switch, Table, Typography, message } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  getScannerConfig,
  getScannerStatus,
  listRuns,
  patchScannerConfig,
  triggerScan
} from "../api/client";
import type { RunRead, ScannerConfigUpdate } from "../api/types";
import { EmptyState, PageHeader, StatStrip } from "../components/common";
import { ScanConsole } from "../components/ScanConsole";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatNumber, formatTime } from "../lib/format";
import { useEffect } from "react";

const ACTIVE_STATUSES = new Set(["accepted", "queued", "running"]);

const CRON_PRESETS = [
  { label: "Daily at 2 AM", value: "0 2 * * *" },
  { label: "Daily at 9 AM", value: "0 9 * * *" },
  { label: "Every 6 hours", value: "0 */6 * * *" },
  { label: "Every 12 hours", value: "0 */12 * * *" },
  { label: "Weekdays at 2 AM", value: "0 2 * * 1-5" }
];

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const summaryNumber = (run: RunRead, key: string): number => {
  const value = run.summary?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
};

const targetLabel = (run: RunRead): string => {
  const value = run.summary?.target_label;
  return typeof value === "string" ? value : "Scan run";
};

const validateCron = (_: unknown, value?: string) => {
  if (!value) return Promise.reject(new Error("Cron expression is required"));
  if (value.trim().split(/\s+/).length !== 5) {
    return Promise.reject(new Error("Use five-field cron syntax"));
  }
  return Promise.resolve();
};

export const Scans = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token } = useAuth();
  const [form] = Form.useForm<ScannerConfigUpdate>();
  const canOperate = token !== "viewer";

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
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      navigate(`/scans/${result.run_id}`);
    },
    onError: (error) => message.error((error as Error).message)
  });

  const activeRun = status.data?.active_run;
  const latestRun = status.data?.latest_run;

  return (
    <div>
      <PageHeader
        title="Scanner"
        subtitle="Schedule catalog scans, trigger ad-hoc refreshes, and inspect scan progress."
        actions={
          <Space>
            <Button
              type="primary"
              loading={scanAll.isPending}
              disabled={!canOperate || Boolean(activeRun)}
              onClick={() => scanAll.mutate()}
            >
              Scan all tables now
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={8}>
          <Card title="Schedule">
            <Form<ScannerConfigUpdate>
              form={form}
              layout="vertical"
              disabled={!canOperate}
              onFinish={(values) => saveConfig.mutate(values)}
            >
              <Form.Item name="enabled" label="Enable scheduled scans" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="schedule_cron" label="Cron schedule" rules={[{ validator: validateCron }]}>
                <Select options={CRON_PRESETS} showSearch />
              </Form.Item>
              <Form.Item name="timezone" label="Timezone">
                <Input placeholder="UTC" />
              </Form.Item>
              <Form.Item name="num_chunks" label="Chunks" rules={[{ required: true }]}>
                <InputNumber min={1} max={128} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item name="full_scan_default" label="Scheduled scans force rescan" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="cost_full_scan_default" label="Scheduled scans include full cost scan" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="max_concurrent_runs" label="Max concurrent scan runs">
                <InputNumber min={1} max={4} style={{ width: "100%" }} />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={saveConfig.isPending}>
                Save schedule
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={16}>
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
              {activeRun ? (
                <Typography.Text>
                  Active scan <Link to={`/scans/${activeRun.id}`}>{shortId(activeRun.id)}</Link>{" "}
                  <StatusTag status={activeRun.status} />
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">No active scan run.</Typography.Text>
              )}
              {latestRun ? (
                <Typography.Text type="secondary">
                  Latest run {shortId(latestRun.id)} finished {formatTime(latestRun.finished_at)}
                </Typography.Text>
              ) : null}
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={runId ? 11 : 24}>
          <Card title="Recent scans">
            <Table<RunRead>
              size="small"
              rowKey="id"
              loading={runs.isLoading}
              dataSource={runs.data ?? []}
              pagination={{ pageSize: 12 }}
              onRow={(row) => ({ onClick: () => navigate(`/scans/${row.id}`) })}
              columns={[
                {
                  title: "Run",
                  width: 104,
                  render: (_, row) => (
                    <Link to={`/scans/${row.id}`}>
                      <Typography.Text code>{shortId(row.id)}</Typography.Text>
                    </Link>
                  )
                },
                {
                  title: "Target",
                  width: 220,
                  render: (_, row) => targetLabel(row)
                },
                { title: "Status", dataIndex: "status", width: 112, render: (value) => <StatusTag status={value} /> },
                { title: "Trigger", dataIndex: "trigger", width: 120, render: (value) => value ?? "-" },
                { title: "Scanned", width: 96, align: "right", render: (_, row) => formatNumber(summaryNumber(row, "scanned")) },
                { title: "Skipped", width: 96, align: "right", render: (_, row) => formatNumber(summaryNumber(row, "skipped")) },
                { title: "Failed", width: 96, align: "right", render: (_, row) => formatNumber(summaryNumber(row, "failed")) },
                { title: "Created", dataIndex: "created_at", width: 148, render: formatTime }
              ]}
              locale={{
                emptyText: (
                  <EmptyState
                    title="No scan runs yet"
                    description="Scheduled and ad-hoc scanner runs will appear here."
                  />
                )
              }}
              scroll={{ x: runId ? 1000 : 1120 }}
            />
          </Card>
        </Col>

        {runId ? (
          <Col xs={24} xl={13}>
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
          </Col>
        ) : null}
      </Row>
    </div>
  );
};
