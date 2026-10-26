import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getDeepHealth, getMetricsText, getShallowHealth, triggerGitOpsWebhook } from "../api/client";
import { EmptyState, QueryErrorAlert } from "./common";
import { PrometheusPreview } from "./PrometheusPreview";
import { useAuth } from "../lib/auth";
import { Alert } from "./ui/alert";
import { Link } from "react-router-dom";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
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
    queryFn: () => getDeepHealth(api),
    refetchInterval: 60_000
  });
  const shallowHealth = useQuery({
    queryKey: ["shallow-health"],
    queryFn: () => getShallowHealth(),
    refetchInterval: 60_000
  });

  const loadMetrics = useMutation({
    mutationFn: () => getMetricsText(api, 80),
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
      const record = result && typeof result === "object" ? (result as Record<string, unknown>) : {};
      const status = typeof record.status === "string" ? record.status : "";
      const synced = Number(record.policies_synced ?? 0);
      const updated = Number(record.tables_updated ?? 0);
      const loaded = Number(record.policies_loaded ?? 0);
      if (status === "ok" && (synced > 0 || updated > 0)) {
        toast.success(`Synced ${synced} policies, updated ${updated} tables`);
      } else if (status === "ok") {
        toast.warning(loaded === 0 ? "GitOps webhook loaded no policies" : "GitOps webhook processed nothing");
      } else if (status === "partial") {
        toast.warning(`Synced ${synced} of ${loaded} policies`);
      } else if (status === "conflict") {
        toast.error("GitOps policies not applied");
      } else {
        toast.error(status ? `GitOps webhook ${status}` : "GitOps webhook did not apply");
      }
    },
    onError: (error) => toast.error((error as Error).message)
  });

  return (
    <div className="flex w-full flex-col gap-4">
      <p className="tab-intro text-sm text-muted-foreground">
        Platform observability and GitOps hooks for admins. These endpoints are intended for operators and monitoring
        — not day-to-day maintenance workflows. To hold maintenance, open{" "}
        <Link to="/runs?tab=holds" className="text-primary underline-offset-2 hover:underline">
          Holds
        </Link>
        .
      </p>

      <div className="grid items-stretch gap-3 md:grid-cols-2">
        <Card className="h-full">
          <CardHeader className="space-y-2 p-3">
            <div className="flex h-5 items-center gap-2">
              <CardTitle className="whitespace-nowrap">Liveness</CardTitle>
              {shallowHealth.data ? <Badge variant="success">{shallowHealth.data.status}</Badge> : null}
            </div>
            <div className="flex h-7 items-center">
              {shallowHealth.data ? (
                <Button variant="ghost" size="sm" className="-ml-2 h-7" asChild>
                  <a href="/healthz" target="_blank" rel="noreferrer">
                    <ExternalLink className="size-3.5" />
                    Open /healthz
                  </a>
                </Button>
              ) : (
                <p className="text-sm text-muted-foreground">Checking…</p>
              )}
            </div>
          </CardHeader>
          {shallowHealth.error ? (
            <CardContent className="px-3 pb-3 pt-0">
              <QueryErrorAlert error={shallowHealth.error} title="Liveness check failed" />
            </CardContent>
          ) : null}
        </Card>

        <Card className="h-full">
          <CardHeader className="space-y-2 p-3">
            <div className="flex h-5 items-center gap-2">
              <CardTitle className="whitespace-nowrap">Deep health</CardTitle>
              {deepHealth.data ? (
                <Badge variant={deepHealth.data.status === "ok" ? "success" : "warning"}>
                  {deepHealth.data.status}
                </Badge>
              ) : null}
            </div>
            <div className="flex h-7 items-center gap-1">
              <Button
                size="sm"
                className="h-7"
                loading={deepHealth.isFetching}
                onClick={() => void deepHealth.refetch()}
              >
                <RefreshCw className="size-3.5" />
                Refresh
              </Button>
            </div>
          </CardHeader>
          <CardContent className="px-3 pb-3 pt-0">
            <QueryErrorAlert error={deepHealth.error} title="Deep health check failed" />
            {deepHealth.data ? (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(deepHealth.data.checks).map(([name, check]) => (
                  <div
                    key={name}
                    className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] px-2.5"
                  >
                    <span className="text-xs font-medium text-muted-foreground">{name}</span>
                    <Badge variant={check.status === "ok" ? "success" : "danger"}>{check.status}</Badge>
                    {check.detail ? <span className="text-xs text-muted-foreground">{check.detail}</span> : null}
                  </div>
                ))}
              </div>
            ) : deepHealth.isLoading ? (
              <p className="text-sm text-muted-foreground">Checking dependencies…</p>
            ) : (
              <EmptyState title="Health unavailable" />
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-3">
          <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 p-3">
            <CardTitle>Prometheus metrics</CardTitle>
            <Button size="sm" loading={loadMetrics.isPending} onClick={() => loadMetrics.mutate()}>
              Preview
            </Button>
          </CardHeader>
          <CardContent className="px-3 pb-3 pt-0">
            <p className="text-sm text-muted-foreground">
              Prometheus scrapes <code>/metrics</code> on the API Service inside the cluster. The web UI does not
              expose it. Preview loads the first lines only.
            </p>
            {metricsPreview ? (
              <PrometheusPreview text={metricsPreview} />
            ) : (
              <EmptyState
                title="No preview loaded"
                description="Click Preview to load the first lines."
              />
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-3">
          <CardHeader className="p-3 pb-2">
            <CardTitle>GitOps policy webhook</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-3 pb-3 pt-0">
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
              <Alert
                variant={gitopsResult.includes('"status": "ok"') && !gitopsResult.includes('"policies_synced": 0') ? "success" : "warning"}
                title="Webhook response"
                description={<pre className="inline-json">{gitopsResult}</pre>}
              />
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};
