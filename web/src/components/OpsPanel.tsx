import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getDeepHealth, getMetricsText, getShallowHealth, triggerGitOpsWebhook } from "../api/client";
import { EmptyState, QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Descriptions } from "./ui/descriptions";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";

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
    onError: (error) => toast.error((error as Error).message)
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
      toast.success("GitOps webhook processed");
    },
    onError: (error) => toast.error((error as Error).message)
  });

  return (
    <div className="flex w-full flex-col gap-4">
      <p className="tab-intro text-sm text-muted-foreground">
        Platform observability and GitOps hooks for admins. These endpoints are intended for operators and monitoring
        — not day-to-day maintenance workflows.
      </p>

      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Liveness</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <QueryErrorAlert error={shallowHealth.error} title="Liveness check failed" />
            {shallowHealth.data ? (
              <>
                <Badge variant="success">{shallowHealth.data.status}</Badge>
                <Button variant="ghost" size="sm" asChild>
                  <a href="/healthz" target="_blank" rel="noreferrer">
                    <ExternalLink className="size-3.5" />
                    Open /healthz
                  </a>
                </Button>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Checking…</p>
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Deep health</CardTitle>
            <Button
              size="sm"
              loading={deepHealth.isFetching}
              onClick={() => void deepHealth.refetch()}
            >
              <RefreshCw className="size-3.5" />
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            <QueryErrorAlert error={deepHealth.error} title="Deep health check failed" />
            {deepHealth.data ? (
              <>
                <Badge variant={deepHealth.data.status === "ok" ? "success" : "warning"}>
                  {deepHealth.data.status}
                </Badge>
                <Descriptions
                  bordered
                  size="small"
                  column={1}
                  items={Object.entries(deepHealth.data.checks).map(([name, check]) => ({
                    label: name,
                    children: (
                      <span className="inline-flex items-center gap-2">
                        <Badge variant={check.status === "ok" ? "success" : "danger"}>{check.status}</Badge>
                        {check.detail ? <span className="text-muted-foreground">{check.detail}</span> : null}
                      </span>
                    )
                  }))}
                />
                <Button variant="ghost" size="sm" asChild>
                  <a href="/healthz/deep" target="_blank" rel="noreferrer">
                    <ExternalLink className="size-3.5" />
                    Open JSON
                  </a>
                </Button>
              </>
            ) : deepHealth.isLoading ? (
              <p className="text-sm text-muted-foreground">Checking dependencies…</p>
            ) : (
              <EmptyState title="Health unavailable" />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-1">
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>Prometheus metrics</CardTitle>
            <div className="flex gap-2">
              <Button size="sm" loading={loadMetrics.isPending} onClick={() => loadMetrics.mutate()}>
                Preview
              </Button>
              <Button size="sm" asChild>
                <a href="/metrics" target="_blank" rel="noreferrer">
                  <ExternalLink className="size-3.5" />
                  Open scrape URL
                </a>
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Exposes registered Prometheus metrics at <code>/metrics</code> for scraping. Preview loads the first
              lines only.
            </p>
            {metricsPreview ? (
              <pre className="inline-json mt-3">{metricsPreview}</pre>
            ) : (
              <EmptyState
                title="No preview loaded"
                description="Click Preview or open the scrape URL in a new tab."
              />
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-3">
          <CardHeader>
            <CardTitle>GitOps policy webhook</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Simulates a Git push webhook to reload YAML policies from <code>CRYO_GITOPS_REPO_PATH</code>. Requires
              GitOps repo configured on the API. If <code>CRYO_GITOPS_WEBHOOK_SECRET</code> is set, pass it below.
            </p>
            <Input
              placeholder="X-Cryo-GitOps-Secret (optional)"
              value={gitopsSecret}
              onChange={(e) => setGitopsSecret(e.target.value)}
              autoComplete="off"
            />
            <Textarea
              rows={8}
              value={gitopsPayload}
              onChange={(e) => setGitopsPayload(e.target.value)}
              className="mono-input"
            />
            <Button
              variant="primary"
              loading={gitops.isPending}
              onClick={() => {
                try {
                  JSON.parse(gitopsPayload);
                  gitops.mutate();
                } catch {
                  toast.error("Payload must be valid JSON.");
                }
              }}
            >
              Trigger webhook
            </Button>
            {gitopsResult ? (
              <Alert variant="success" title="Webhook response" description={<pre className="inline-json">{gitopsResult}</pre>} />
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};
