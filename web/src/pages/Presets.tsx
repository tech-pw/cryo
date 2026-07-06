import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  Checkbox,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  message,
  Alert
} from "antd";
import { useState } from "react";
import {
  createPreset,
  createPresetRule,
  deletePreset,
  deletePresetRule,
  listDestinations,
  listPresetRules,
  listPresets,
  listTables,
  patchPreset,
  previewRouting,
  simulatePreset
} from "../api/client";
import type { PresetRead, PresetWrite, UUID } from "../api/types";
import { ConfirmAction } from "../components/confirm";
import { CronInput } from "../components/CronInput";
import { EmptyState, PageHeader } from "../components/common";
import { FilterBar } from "../components/FilterBar";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { cronFormValidator } from "../lib/cron";
import { formatGb, formatNumber } from "../lib/format";
import { qualifiedTableName, schemaTableName } from "../lib/tableLabels";
import { tableFiltersToPredicate, type TableFilterState } from "../lib/predicate";

const operations = ["expire_snapshots", "remove_orphan_files", "optimize", "optimize_manifests"];
type SimulationSample = NonNullable<Awaited<ReturnType<typeof simulatePreset>>["sample"]>[number];

export const Presets = () => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<PresetRead | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [rulePreset, setRulePreset] = useState<PresetRead | null>(null);
  const [simPreset, setSimPreset] = useState<PresetRead | null>(null);
  const [routingPreset, setRoutingPreset] = useState<PresetRead | null>(null);

  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });

  const save = useMutation({
    mutationFn: (body: PresetWrite) => (editing ? patchPreset(api, editing.id, body) : createPreset(api, body)),
    onSuccess: () => {
      message.success("Preset saved");
      setDrawerOpen(false);
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["presets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const removePreset = useMutation({
    mutationFn: (id: UUID) => deletePreset(api, id),
    onSuccess: () => {
      message.success("Preset disabled");
      void queryClient.invalidateQueries({ queryKey: ["presets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <div>
      <PageHeader
        title="Presets"
        subtitle="Maintenance modes, ordered operations, guardrails, and table-matching rules."
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setDrawerOpen(true)}>
            New preset
          </Button>
        }
      />
      <Table<PresetRead>
        size="small"
        rowKey="id"
        loading={presets.isLoading}
        dataSource={presets.data ?? []}
        columns={[
          {
            title: "Preset",
            render: (_, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{row.name}</Typography.Text>
                <Typography.Text type="secondary">{row.description ?? "No description"}</Typography.Text>
              </Space>
            )
          },
          { title: "Mode", dataIndex: "mode" },
          {
            title: "Engine",
            render: (_, row) => destinations.data?.find((item) => item.id === row.destination_id)?.name ?? row.destination_id
          },
          { title: "Priority", dataIndex: "priority", align: "right" },
          { title: "Enabled", dataIndex: "enabled", render: (value) => <StatusTag status={value ? "succeeded" : "skipped"} /> },
          {
            title: "Actions",
            render: (_, row) => (
              <Space>
                <Button size="small" onClick={() => { setEditing(row); setDrawerOpen(true); }}>
                  Edit
                </Button>
                <Button size="small" onClick={() => setRulePreset(row)}>
                  Rules
                </Button>
                <Button size="small" onClick={() => setSimPreset(row)}>
                  Simulate
                </Button>
                <Button size="small" onClick={() => setRoutingPreset(row)}>
                  Routing
                </Button>
                {role === "admin" ? (
                  <ConfirmAction
                    title="Disable this preset?"
                    description="The preset will be disabled and no longer assignable."
                    onConfirm={() => removePreset.mutate(row.id)}
                  >
                    <Button size="small" danger loading={removePreset.isPending}>
                      Disable
                    </Button>
                  </ConfirmAction>
                ) : null}
              </Space>
            )
          }
        ]}
        locale={{ emptyText: <EmptyState title="No presets" description="Create one to assign maintenance behavior to tables." /> }}
      />
      <PresetDrawer
        key={editing?.id ?? (drawerOpen ? "new" : "closed")}
        open={drawerOpen}
        preset={editing}
        destinations={destinations.data ?? []}
        saving={save.isPending}
        onClose={() => { setDrawerOpen(false); setEditing(null); }}
        onSave={(body) => save.mutate(body)}
      />
      <RulesDrawer preset={rulePreset} onClose={() => setRulePreset(null)} />
      <SimulatorDrawer preset={simPreset} onClose={() => setSimPreset(null)} />
      <RoutingDrawer preset={routingPreset} onClose={() => setRoutingPreset(null)} />
    </div>
  );
};

const PresetDrawer = ({
  open,
  preset,
  destinations,
  saving,
  onClose,
  onSave
}: {
  open: boolean;
  preset: PresetRead | null;
  destinations: Array<{ id: UUID; name: string }>;
  saving: boolean;
  onClose: () => void;
  onSave: (body: PresetWrite) => void;
}) => {
  const [form] = Form.useForm();

  return (
    <Drawer title={preset ? "Edit preset" : "New preset"} width={620} open={open} onClose={onClose} destroyOnHidden>
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: preset?.name,
          description: preset?.description,
          destination_id: preset?.destination_id ?? destinations[0]?.id,
          mode: preset?.mode ?? "standard",
          operations: preset?.operations?.length ? preset.operations : operations,
          schedule_cron: preset?.schedule_cron,
          priority: preset?.priority ?? 100,
          enabled: preset?.enabled ?? true,
          params: JSON.stringify(preset?.params ?? { min_small_files: 128, chunk_size_gb: 100 }, null, 2),
          guardrails: JSON.stringify(preset?.guardrails ?? { cooldown_hours: 24 }, null, 2)
        }}
        onFinish={(values) => {
          try {
            onSave({
              name: values.name,
              description: values.description || null,
              destination_id: values.destination_id,
              mode: values.mode,
              operations: values.operations,
              schedule_cron: values.schedule_cron || null,
              priority: values.priority,
              enabled: values.enabled,
              params: JSON.parse(values.params || "{}"),
              guardrails: JSON.parse(values.guardrails || "{}")
            });
          } catch {
            message.error("Params and guardrails must be valid JSON.");
          }
        }}
      >
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="description" label="Description">
          <Input />
        </Form.Item>
        <Form.Item name="destination_id" label="Engine" rules={[{ required: true }]}>
          <Select options={destinations.map((item) => ({ label: item.name, value: item.id }))} />
        </Form.Item>
        <Space className="full-width" size={12}>
          <Form.Item name="mode" label="Mode" rules={[{ required: true }]}>
            <Select
              options={[
                { label: "Light", value: "light" },
                { label: "Standard", value: "standard" },
                { label: "Deep", value: "deep" },
                { label: "Manifest only", value: "manifest_only" }
              ]}
              style={{ width: 160 }}
            />
          </Form.Item>
          <Form.Item name="priority" label="Priority">
            <InputNumber min={0} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
        <Form.Item name="operations" label="Operations">
          <Checkbox.Group options={operations} />
        </Form.Item>
        <Form.Item name="schedule_cron" label="Schedule cron" rules={[{ validator: cronFormValidator({ allowEmpty: true }) }]}>
          <CronInput allowEmpty />
        </Form.Item>
        <Form.Item name="params" label="Params JSON">
          <Input.TextArea rows={6} className="mono-input" />
        </Form.Item>
        <Form.Item name="guardrails" label="Guardrails JSON">
          <Input.TextArea rows={5} className="mono-input" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={saving}>
          Save preset
        </Button>
      </Form>
    </Drawer>
  );
};

const RulesDrawer = ({ preset, onClose }: { preset: PresetRead | null; onClose: () => void }) => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<TableFilterState>({ activeOnly: true });
  const rules = useQuery({
    queryKey: ["preset-rules", preset?.id, token],
    queryFn: () => listPresetRules(api, preset!.id),
    enabled: Boolean(preset)
  });
  const addRule = useMutation({
    mutationFn: () => {
      const predicate = tableFiltersToPredicate(filters);
      if (!predicate || !preset) throw new Error("Choose at least one filter.");
      return createPresetRule(api, preset.id, { predicate, enabled: true });
    },
    onSuccess: () => {
      message.success("Rule added");
      void queryClient.invalidateQueries({ queryKey: ["preset-rules", preset?.id] });
    },
    onError: (error) => message.error((error as Error).message)
  });
  const removeRule = useMutation({
    mutationFn: (ruleId: UUID) => deletePresetRule(api, preset!.id, ruleId),
    onSuccess: () => {
      message.success("Rule deleted");
      void queryClient.invalidateQueries({ queryKey: ["preset-rules", preset?.id] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <Drawer title={`Rules: ${preset?.name ?? ""}`} open={Boolean(preset)} onClose={onClose} width={700} destroyOnHidden>
      <Space direction="vertical" size={12} className="full-width">
        <Card title="New rule from filters" size="small">
          <FilterBar value={filters} onChange={setFilters} />
          <Button type="primary" loading={addRule.isPending} onClick={() => addRule.mutate()}>
            Add rule
          </Button>
        </Card>
        <Table
          size="small"
          rowKey="id"
          loading={rules.isLoading}
          dataSource={rules.data ?? []}
          columns={[
            { title: "Enabled", dataIndex: "enabled", render: (value) => String(value) },
            {
              title: "Predicate",
              dataIndex: "predicate",
              render: (value) => <pre className="inline-json">{JSON.stringify(value)}</pre>
            },
            ...(role === "admin"
              ? [
                  {
                    title: "Actions",
                    width: 90,
                    render: (_: unknown, row: { id: UUID }) => (
                      <ConfirmAction
                        title="Delete this rule?"
                        onConfirm={() => removeRule.mutate(row.id)}
                      >
                        <Button size="small" danger loading={removeRule.isPending}>
                          Delete
                        </Button>
                      </ConfirmAction>
                    )
                  }
                ]
              : [])
          ]}
        />
      </Space>
    </Drawer>
  );
};

const SimulatorDrawer = ({ preset, onClose }: { preset: PresetRead | null; onClose: () => void }) => {
  const { api, token } = useAuth();
  const simulation = useQuery({
    queryKey: ["preset-simulation", preset?.id, token],
    queryFn: () => simulatePreset(api, preset!.id),
    enabled: Boolean(preset)
  });

  return (
    <Drawer title={`Simulator: ${preset?.name ?? ""}`} open={Boolean(preset)} onClose={onClose} width={720} destroyOnHidden>
      <Space direction="vertical" size={12} className="full-width">
        <StatSummary
          matched={simulation.data?.matched_count}
          size={simulation.data?.total_size_gb}
          smallFiles={simulation.data?.small_files_count}
        />
        <Table<SimulationSample>
          size="small"
          rowKey="id"
          loading={simulation.isLoading}
          dataSource={simulation.data?.sample ?? []}
          columns={[
            { title: "Table", render: (_, row) => schemaTableName(row) },
            { title: "Grade", dataIndex: "grade" },
            { title: "Size", dataIndex: "total_size_gb", align: "right", render: (value) => formatGb(value) },
            { title: "Small files", dataIndex: "small_files_count", align: "right", render: (value) => formatNumber(value) }
          ]}
        />
      </Space>
    </Drawer>
  );
};

const StatSummary = ({ matched, size, smallFiles }: { matched?: number; size?: number; smallFiles?: number }) => (
  <div className="stat-strip">
    <div><Typography.Text type="secondary">Matched</Typography.Text><strong>{formatNumber(matched)}</strong></div>
    <div><Typography.Text type="secondary">Total size</Typography.Text><strong>{formatGb(size)}</strong></div>
    <div><Typography.Text type="secondary">Small files</Typography.Text><strong>{formatNumber(smallFiles, true)}</strong></div>
  </div>
);

const RoutingDrawer = ({ preset, onClose }: { preset: PresetRead | null; onClose: () => void }) => {
  const { api, token } = useAuth();
  const [tableId, setTableId] = useState<string | undefined>();
  const tables = useQuery({
    queryKey: ["routing-table-picker", token],
    queryFn: () => listTables(api, { limit: 200, offset: 0 }),
    enabled: Boolean(preset)
  });
  const routing = useMutation({
    mutationFn: () => previewRouting(api, { table_id: tableId!, preset_id: preset!.id }),
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <Drawer
      title={`Adaptive routing: ${preset?.name ?? ""}`}
      open={Boolean(preset)}
      onClose={onClose}
      width={720}
      destroyOnHidden
    >
      <Space direction="vertical" size={12} className="full-width">
        <Typography.Text type="secondary">
          Preview how the governor would rank engines for this preset on a specific table.
        </Typography.Text>
        <Select
          showSearch
          allowClear
          placeholder="Select table"
          optionFilterProp="label"
          style={{ width: "100%" }}
          loading={tables.isLoading}
          options={(tables.data?.items ?? []).map((table) => ({
            value: table.id,
            label: qualifiedTableName(table)
          }))}
          value={tableId}
          onChange={setTableId}
        />
        <Button
          type="primary"
          disabled={!tableId}
          loading={routing.isPending}
          onClick={() => routing.mutate()}
        >
          Preview routing
        </Button>
        {routing.data ? (
          <>
            <Alert
              type="info"
              showIcon
              message={`Selected: ${routing.data.selected_destination_id}`}
              description={routing.data.selected_reason}
            />
            <Table
              size="small"
              rowKey="destination_id"
              pagination={false}
              dataSource={routing.data.ranked_destinations}
              columns={[
                { title: "Engine", dataIndex: "engine", width: 100 },
                { title: "Destination", dataIndex: "destination_id", ellipsis: true },
                {
                  title: "Score",
                  dataIndex: "score",
                  width: 80,
                  align: "right" as const,
                  render: (value: number) => value.toFixed(3)
                },
                { title: "Reason", dataIndex: "reason", ellipsis: true }
              ]}
            />
          </>
        ) : null}
      </Space>
    </Drawer>
  );
};
