import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Empty, Form, Input, Modal, Skeleton, Space, Table, Tag, Typography, message } from "antd";
import {
  activateSkillset,
  createSkillset,
  listLlmObservations,
  listSkillsets,
  promoteSkillset,
  rollbackSkillset
} from "../api/client";
import { useAuth } from "../lib/auth";
import type { SkillsetRead } from "../api/types";
import { useMemo, useState } from "react";

interface CreateSkillsetFormValues {
  name: string;
  version: string;
  prompts: string;
  tools: string;
  activate: boolean;
}

const DEFAULT_CREATE_VALUES: CreateSkillsetFormValues = {
  name: "iceberg_advisor",
  version: "",
  prompts: "{\n  \"system\": \"\"\n}",
  tools: "[]",
  activate: false
};

export const Skillsets = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [source, setSource] = useState<SkillsetRead | null>(null);
  const [form] = Form.useForm<{ target_version: string }>();
  const [createForm] = Form.useForm<CreateSkillsetFormValues>();

  const query = useQuery({
    queryKey: ["skillsets"],
    queryFn: () => listSkillsets(api)
  });
  const observations = useQuery({
    queryKey: ["llm-observations", "skillsets"],
    queryFn: () => listLlmObservations(api, { skillset_name: "iceberg_advisor", limit: 100 })
  });

  const activate = useMutation({
    mutationFn: (row: SkillsetRead) => activateSkillset(api, row.name, { version: row.version }),
    onSuccess: () => {
      message.success("Activated skillset version");
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });
  const rollback = useMutation({
    mutationFn: (row: SkillsetRead) => rollbackSkillset(api, row.name, { version: row.version }),
    onSuccess: () => {
      message.success("Rollback applied");
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });
  const promote = useMutation({
    mutationFn: (args: { name: string; sourceVersion: string; targetVersion: string }) =>
      promoteSkillset(api, args.name, {
        source_version: args.sourceVersion,
        target_version: args.targetVersion,
        activate: true
      }),
    onSuccess: () => {
      message.success("Candidate promoted and activated");
      setPromoteOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });
  const create = useMutation({
    mutationFn: (args: { name: string; version: string; prompts: string; tools: string; activate: boolean }) =>
      createSkillset(api, {
        name: args.name.trim(),
        version: args.version.trim(),
        prompts: JSON.parse(args.prompts),
        tools: JSON.parse(args.tools),
        activate: args.activate
      }),
    onSuccess: () => {
      message.success("Skillset candidate created");
      setCreateOpen(false);
      createForm.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  if (query.isLoading) return <Skeleton active />;
  if (query.isError) {
    return (
      <Card>
        <Typography.Text type="danger">Failed to load skillsets: {(query.error as Error).message}</Typography.Text>
      </Card>
    );
  }

  const rows = query.data?.skillsets ?? [];
  const usageSummary = useMemo(() => {
    const items = observations.data?.items ?? [];
    const totalTokens = items.reduce((acc, row) => acc + (row.total_tokens ?? 0), 0);
    const denials = items.filter((row) => row.budget_denied).length;
    const avgLatency = items.length
      ? Math.round(
          items.reduce((acc, row) => acc + (row.latency_ms ?? 0), 0) / Math.max(items.length, 1)
        )
      : 0;
    return { totalTokens, denials, avgLatency, calls: items.length };
  }, [observations.data?.items]);

  const canWrite = role === "admin";

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Space align="start" style={{ width: "100%", justifyContent: "space-between" }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>AI Skillsets</Typography.Title>
          <Typography.Text type="secondary">
            Versioned prompt + tool definitions used by the Table Detail AI insights. Outcomes are recorded per recommendation; acceptance rate
            drives A/B rollouts (Schneider et al., 2026).
          </Typography.Text>
        </div>
        <Button
          type="primary"
          disabled={!canWrite}
          onClick={() => {
            createForm.setFieldsValue(DEFAULT_CREATE_VALUES);
            setCreateOpen(true);
          }}
        >
          Create candidate
        </Button>
      </Space>
      <Card size="small">
        <Space size={20} wrap>
          <Typography.Text type="secondary">
            LLM calls: <Typography.Text>{usageSummary.calls.toLocaleString()}</Typography.Text>
          </Typography.Text>
          <Typography.Text type="secondary">
            Tokens: <Typography.Text>{usageSummary.totalTokens.toLocaleString()}</Typography.Text>
          </Typography.Text>
          <Typography.Text type="secondary">
            Avg latency: <Typography.Text>{usageSummary.avgLatency} ms</Typography.Text>
          </Typography.Text>
          <Typography.Text type={usageSummary.denials > 0 ? "warning" : "secondary"}>
            Budget denials: <Typography.Text>{usageSummary.denials}</Typography.Text>
          </Typography.Text>
        </Space>
      </Card>
      <Card>
        {rows.length === 0 ? (
          <Empty description="No skillsets registered. The built-in iceberg_advisor skillset will seed on first use." />
        ) : (
          <Table
            dataSource={rows}
            rowKey={(s) => s.id}
            size="small"
            pagination={false}
            columns={[
              { title: "Name", dataIndex: "name", key: "name", render: (v) => <Typography.Text code>{v}</Typography.Text> },
              { title: "Version", dataIndex: "version", key: "version" },
              {
                title: "Status",
                dataIndex: "is_active",
                key: "active",
                render: (v) => (v ? <Tag color="success">active</Tag> : <Tag>inactive</Tag>)
              },
              {
                title: "Acceptance rate",
                dataIndex: "metrics",
                key: "acceptance",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.acceptance_rate;
                  return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
                }
              },
              {
                title: "Confidence",
                dataIndex: "metrics",
                key: "confidence",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.confidence;
                  return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
                }
              },
              {
                title: "Avg score",
                dataIndex: "metrics",
                key: "score",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.avg_score;
                  return typeof v === "number" ? v.toFixed(2) : "—";
                }
              },
              {
                title: "Outcomes",
                dataIndex: "metrics",
                key: "outcomes",
                render: (metrics: Record<string, unknown> | null | undefined) => {
                  if (!metrics) return "—";
                  const v = metrics.outcomes_count ?? metrics.sample_count;
                  return typeof v === "number" ? v : "—";
                }
              },
              {
                title: "Created",
                dataIndex: "created_at",
                key: "created_at",
                render: (v) => (v ? new Date(v).toLocaleString() : "—")
              },
              {
                title: "Lifecycle",
                key: "actions",
                render: (_, row) => (
                  <Space>
                    <Button
                      size="small"
                      disabled={!canWrite || row.is_active}
                      loading={activate.isPending}
                      onClick={() => activate.mutate(row)}
                    >
                      Activate
                    </Button>
                    <Button
                      size="small"
                      disabled={!canWrite || row.is_active}
                      loading={rollback.isPending}
                      onClick={() => rollback.mutate(row)}
                    >
                      Rollback
                    </Button>
                    <Button
                      size="small"
                      disabled={!canWrite}
                      onClick={() => {
                        setSource(row);
                        setPromoteOpen(true);
                        form.setFieldsValue({ target_version: "" });
                      }}
                    >
                      Promote
                    </Button>
                  </Space>
                )
              }
            ]}
          />
        )}
      </Card>
      <Modal
        title="Promote candidate version"
        open={promoteOpen}
        onCancel={() => {
          setPromoteOpen(false);
          setSource(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={promote.isPending}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            if (!source) return;
            promote.mutate({
              name: source.name,
              sourceVersion: source.version,
              targetVersion: values.target_version.trim()
            });
          }}
        >
          <Typography.Paragraph type="secondary">
            Source candidate: <Typography.Text code>{source?.name}@{source?.version}</Typography.Text>
          </Typography.Paragraph>
          <Form.Item
            label="Target promoted version"
            name="target_version"
            rules={[
              { required: true, message: "Enter a semantic version" },
              { pattern: /^\d+\.\d+\.\d+$/, message: "Use semver format, e.g. 1.2.0" }
            ]}
          >
            <Input placeholder="1.2.0" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="Create skillset candidate"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        onOk={() => createForm.submit()}
        confirmLoading={create.isPending}
        width={640}
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={DEFAULT_CREATE_VALUES}
          onFinish={(values) => create.mutate(values)}
        >
          <Typography.Paragraph type="secondary">
            Registers a new, inactive candidate version. Use Activate or Promote from the table once
            it's ready.
          </Typography.Paragraph>
          <Form.Item
            label="Skillset name"
            name="name"
            rules={[{ required: true, message: "Enter a skillset name" }]}
          >
            <Input placeholder="iceberg_advisor" />
          </Form.Item>
          <Form.Item
            label="Version"
            name="version"
            rules={[
              { required: true, message: "Enter a semantic version" },
              { pattern: /^\d+\.\d+\.\d+$/, message: "Use semver format, e.g. 1.2.0" }
            ]}
          >
            <Input placeholder="1.2.0" />
          </Form.Item>
          <Form.Item
            label="Prompts (JSON object of name -> template)"
            name="prompts"
            rules={[
              { required: true, message: "Enter a JSON object" },
              {
                validator: (_, value: string) => {
                  try {
                    const parsed = JSON.parse(value);
                    if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
                      return Promise.reject(new Error("Prompts must be a JSON object"));
                    }
                    return Promise.resolve();
                  } catch {
                    return Promise.reject(new Error("Invalid JSON"));
                  }
                }
              }
            ]}
          >
            <Input.TextArea rows={6} className="mono-textarea" spellCheck={false} />
          </Form.Item>
          <Form.Item
            label="Tools (JSON array)"
            name="tools"
            rules={[
              { required: true, message: "Enter a JSON array" },
              {
                validator: (_, value: string) => {
                  try {
                    const parsed = JSON.parse(value);
                    if (!Array.isArray(parsed)) {
                      return Promise.reject(new Error("Tools must be a JSON array"));
                    }
                    return Promise.resolve();
                  } catch {
                    return Promise.reject(new Error("Invalid JSON"));
                  }
                }
              }
            ]}
          >
            <Input.TextArea rows={4} className="mono-textarea" spellCheck={false} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
};
