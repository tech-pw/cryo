import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Input, Row, Space, Tag, Typography, message } from "antd";
import { LinkOutlined, ReloadOutlined } from "@ant-design/icons";
import { useState } from "react";
import { getDeepHealth, getMetricsText, getShallowHealth, triggerGitOpsWebhook } from "../api/client";
import { EmptyState, QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";

const DEFAULT_GITOPS_PAYLOAD = `{
  "ref": "refs/heads/main",
  "repository": {
    "name": "cryo-policies"
  }
}`;

export const OpsPanel = () => {
  const { api } = useAuth();
  const [gitopsPayload, setGitopsPayload] = useState(DEFAULT_GITOPS_PAYLOAD);
  const [gitopsSecret, setGitopsSecret] = useState("");
  const [gitopsResult, setGitopsResult] = useState<string | null>(null);
  const [metricsPreview, setMetricsPreview] = useState<string | null>(null);

  const deepHealth = useQuery({
    queryKey: ["deep-health"],
    queryFn: () => getDeepHealth(),
    refetchInterval: 60_000
  });
  const shallowHealth = useQuery({
    queryKey: ["shallow-health"],
    queryFn: () => getShallowHealth(),
    refetchInterval: 60_000
  });

  const loadMetrics = useMutation({
    mutationFn: () => getMetricsText(80),
    onSuccess: setMetricsPreview,
    onError: (error) => message.error((error as Error).message)
  });

  const gitops = useMutation({
    mutationFn: () => {
      const payload = JSON.parse(gitopsPayload) as Record<string, unknown>;
      return triggerGitOpsWebhook(api, payload, {
        gitopsSecret: gitopsSecret.trim() || undefined
      });
    },
    onSuccess: (result) => {
      setGitopsResult(JSON.stringify(result, null, 2));
      message.success("GitOps webhook processed");
    },
    onError: (error) => message.error((error as Error).message)
  });

  return (
    <Space orientation="vertical" size={16} className="full-width">
      <Typography.Paragraph type="secondary" className="tab-intro">
        Platform observability and GitOps hooks for admins. These endpoints are intended for operators
        and monitoring — not day-to-day maintenance workflows.
      </Typography.Paragraph>

      <Row gutter={[12, 12]}>
        <Col xs={24} md={8}>
          <Card title="Liveness">
            <QueryErrorAlert error={shallowHealth.error} title="Liveness check failed" />
            {shallowHealth.data ? (
              <Space orientation="vertical" size={8}>
                <Tag color="success">{shallowHealth.data.status}</Tag>
                <Button type="link" href="/healthz" target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
                  Open /healthz
                </Button>
              </Space>
            ) : (
              <Typography.Text type="secondary">Checking…</Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} md={16}>
          <Card
            title="Deep health"
            extra={
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={deepHealth.isFetching}
                onClick={() => void deepHealth.refetch()}
              >
                Refresh
              </Button>
            }
          >
            <QueryErrorAlert error={deepHealth.error} title="Deep health check failed" />
            {deepHealth.data ? (
              <Space orientation="vertical" size={8} className="full-width">
                <Tag color={deepHealth.data.status === "ok" ? "success" : "warning"}>
                  {deepHealth.data.status}
                </Tag>
                <Descriptions size="small" column={1} bordered>
                  {Object.entries(deepHealth.data.checks).map(([name, check]) => (
                    <Descriptions.Item key={name} label={name}>
                      <Space>
                        <Tag color={check.status === "ok" ? "success" : "error"}>{check.status}</Tag>
                        {check.detail ? (
                          <Typography.Text type="secondary">{check.detail}</Typography.Text>
                        ) : null}
                      </Space>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
                <Button type="link" href="/healthz/deep" target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
                  Open JSON
                </Button>
              </Space>
            ) : deepHealth.isLoading ? (
              <Typography.Text type="secondary">Checking dependencies…</Typography.Text>
            ) : (
              <EmptyState title="Health unavailable" />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title="Prometheus metrics"
            extra={
              <Space>
                <Button size="small" loading={loadMetrics.isPending} onClick={() => loadMetrics.mutate()}>
                  Preview
                </Button>
                <Button size="small" href="/metrics" target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
                  Open scrape URL
                </Button>
              </Space>
            }
          >
            <Typography.Paragraph type="secondary">
              Exposes registered Prometheus metrics at <Typography.Text code>/metrics</Typography.Text> for
              scraping. Preview loads the first lines only.
            </Typography.Paragraph>
            {metricsPreview ? (
              <pre className="inline-json">{metricsPreview}</pre>
            ) : (
              <EmptyState
                title="No preview loaded"
                description="Click Preview or open the scrape URL in a new tab."
              />
            )}
          </Card>
        </Col>

        <Col xs={24}>
          <Card title="GitOps policy webhook">
            <Space orientation="vertical" size={12} className="full-width">
              <Typography.Paragraph type="secondary">
                Simulates a Git push webhook to reload YAML policies from{" "}
                <Typography.Text code>CRYO_GITOPS_REPO_PATH</Typography.Text>. Requires GitOps repo
                configured on the API. If <Typography.Text code>CRYO_GITOPS_WEBHOOK_SECRET</Typography.Text>{" "}
                is set, pass it below.
              </Typography.Paragraph>
              <Input
                placeholder="X-Cryo-GitOps-Secret (optional)"
                value={gitopsSecret}
                onChange={(e) => setGitopsSecret(e.target.value)}
                autoComplete="off"
              />
              <Input.TextArea
                rows={8}
                value={gitopsPayload}
                onChange={(e) => setGitopsPayload(e.target.value)}
                className="mono-input"
              />
              <Button
                type="primary"
                loading={gitops.isPending}
                onClick={() => {
                  try {
                    JSON.parse(gitopsPayload);
                    gitops.mutate();
                  } catch {
                    message.error("Payload must be valid JSON.");
                  }
                }}
              >
                Trigger webhook
              </Button>
              {gitopsResult ? (
                <Alert
                  type="success"
                  showIcon
                  title="Webhook response"
                  description={<pre className="inline-json">{gitopsResult}</pre>}
                />
              ) : null}
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
};
