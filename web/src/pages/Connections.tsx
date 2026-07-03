import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Drawer, Form, Input, InputNumber, Select, Space, Switch, Table, Tabs, Typography, message } from "antd";
import { useState } from "react";
import type { ReactNode } from "react";
import {
  createDestination,
  createSource,
  listDestinations,
  listSources,
  patchDestination,
  patchSource,
  testDestination
} from "../api/client";
import type { DestinationRead, DestinationWrite, SourceRead, SourceWrite } from "../api/types";
import { EmptyState, PageHeader } from "../components/common";
import { HealthBadge, StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";

export const Connections = () => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const [sourceDrawer, setSourceDrawer] = useState<SourceRead | "new" | null>(null);
  const [destinationDrawer, setDestinationDrawer] = useState<DestinationRead | "new" | null>(null);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);

  const sources = useQuery({ queryKey: ["sources", token], queryFn: () => listSources(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });

  const saveSource = useMutation({
    mutationFn: (body: SourceWrite) =>
      sourceDrawer && sourceDrawer !== "new" ? patchSource(api, sourceDrawer.id, body) : createSource(api, body),
    onSuccess: () => {
      message.success("Source saved");
      setSourceDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const saveDestination = useMutation({
    mutationFn: (body: DestinationWrite) =>
      destinationDrawer && destinationDrawer !== "new"
        ? patchDestination(api, destinationDrawer.id, body)
        : createDestination(api, body),
    onSuccess: () => {
      message.success("Destination saved");
      setDestinationDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["destinations"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const probe = useMutation({
    mutationFn: (destination: DestinationRead) => testDestination(api, destination.id),
    onSuccess: setTestResult,
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <div>
      <PageHeader
        title="Connections"
        subtitle="Read-side Iceberg catalogs are separate from write-side maintenance destinations."
      />
      {testResult ? (
        <Alert
          className="page-alert"
          type={testResult.ok ? "success" : "warning"}
          showIcon
          message={testResult.ok ? "Destination capacity check passed" : "Destination capacity check returned warnings"}
          description={<pre className="inline-json">{JSON.stringify(testResult, null, 2)}</pre>}
          closable
          onClose={() => setTestResult(null)}
        />
      ) : null}
      <Tabs
        items={[
          {
            key: "sources",
            label: "Sources",
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setSourceDrawer("new")}>
                  New source
                </Button>
                <Table<SourceRead>
                  size="small"
                  rowKey="id"
                  loading={sources.isLoading}
                  dataSource={sources.data ?? []}
                  columns={[
                    { title: "Name", dataIndex: "name", render: (value) => <Typography.Text strong>{value}</Typography.Text> },
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
                  locale={{ emptyText: <EmptyState title="No sources" description="Add a catalog to scan Iceberg tables." /> }}
                />
              </Space>
            )
          },
          {
            key: "destinations",
            label: "Destinations",
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setDestinationDrawer("new")}>
                  New destination
                </Button>
                <Table<DestinationRead>
                  size="small"
                  rowKey="id"
                  loading={destinations.isLoading}
                  dataSource={destinations.data ?? []}
                  columns={[
                    { title: "Name", dataIndex: "name", render: (value) => <Typography.Text strong>{value}</Typography.Text> },
                    { title: "Engine", dataIndex: "engine" },
                    {
                      title: "Enabled",
                      dataIndex: "enabled",
                      render: (value) => <HealthBadge healthy={value}>{value ? "Enabled" : "Disabled"}</HealthBadge>
                    },
                    {
                      title: "Off-peak IST",
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
                  locale={{ emptyText: <EmptyState title="No destinations" description="Add Trino or Spark execution targets for maintenance." /> }}
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
    </div>
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
}) => (
  <ConnectionDrawer
    title={value && value !== "new" ? "Edit source" : "New source"}
    open={Boolean(value)}
    onClose={onClose}
    initial={{
      name: value && value !== "new" ? value.name : "",
      type: value && value !== "new" ? value.type : "rest",
      config: JSON.stringify(value && value !== "new" ? value.config : { uri: "http://localhost:8181", warehouse: "warehouse" }, null, 2),
      secret_ref: value && value !== "new" ? value.secret_ref : null,
      enabled: value && value !== "new" ? value.enabled : true
    }}
    fields={<Form.Item name="type" label="Catalog type"><Select options={["rest", "hive", "glue", "polaris", "nessie"].map((item) => ({ label: item, value: item }))} /></Form.Item>}
    saving={saving}
    onSave={onSave}
  />
);

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
}) => (
  <ConnectionDrawer
    title={value && value !== "new" ? "Edit destination" : "New destination"}
    open={Boolean(value)}
    onClose={onClose}
    initial={{
      name: value && value !== "new" ? value.name : "",
      engine: value && value !== "new" ? value.engine : "trino",
      config: JSON.stringify(value && value !== "new" ? value.config : { coordinators: ["http://localhost:8080"], user: "cryo", catalog: "iceberg" }, null, 2),
      secret_ref: value && value !== "new" ? value.secret_ref : null,
      enabled: value && value !== "new" ? value.enabled : true,
      offpeak_start: value && value !== "new" ? value.offpeak_start : 0,
      offpeak_end: value && value !== "new" ? value.offpeak_end : 7
    }}
    fields={
      <>
        <Form.Item name="engine" label="Engine"><Select options={[{ label: "Trino", value: "trino" }, { label: "Spark EMR", value: "spark_emr" }]} /></Form.Item>
        <Space>
          <Form.Item name="offpeak_start" label="Off-peak start"><InputNumber min={0} max={23} /></Form.Item>
          <Form.Item name="offpeak_end" label="Off-peak end"><InputNumber min={0} max={23} /></Form.Item>
        </Space>
      </>
    }
    saving={saving}
    onSave={onSave}
  />
);

const ConnectionDrawer = <T extends { name: string; config: Record<string, unknown>; enabled: boolean }>({
  title,
  open,
  onClose,
  initial,
  fields,
  saving,
  onSave
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  initial: Record<string, unknown>;
  fields: ReactNode;
  saving: boolean;
  onSave: (body: T) => void;
}) => (
  <Drawer title={title} open={open} onClose={onClose} width={580} destroyOnHidden>
    <Form
      layout="vertical"
      initialValues={initial}
      onFinish={(values) => {
        try {
          onSave({ ...values, config: JSON.parse(values.config || "{}") } as T);
        } catch {
          message.error("Config must be valid JSON.");
        }
      }}
    >
      <Form.Item name="name" label="Name" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      {fields}
      <Form.Item name="config" label="Config JSON" rules={[{ required: true }]}>
        <Input.TextArea rows={8} className="mono-input" />
      </Form.Item>
      <Form.Item name="secret_ref" label="Secret reference">
        <Input placeholder="Encrypted secret reference, never raw credentials" />
      </Form.Item>
      <Form.Item name="enabled" label="Enabled" valuePropName="checked">
        <Switch />
      </Form.Item>
      <Button type="primary" htmlType="submit" loading={saving}>
        Save
      </Button>
    </Form>
  </Drawer>
);
