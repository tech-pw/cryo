import { Cloud, FlaskConical, Link2, Plus, RefreshCw, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getAwsUsage, listSources, testAwsIntegration } from "../../api/client";
import type { AWSUsageResponse, SourceRead, UUID } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatCurrency } from "../../lib/format";
import { OverviewPage } from "../../components/layout/OverviewPage";
import { EmptyState, HintLabel, StatStrip } from "../../components/common";
import { AwsIntegrationPanel } from "../../components/AwsIntegrationPanel";
import {
  AwsStatusBadge,
  useAwsSourceStatuses,
  type AwsSourceStatus
} from "../../components/AwsStatusBadge";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";

interface PerSourceState {
  integrationStatus: AwsSourceStatus;
  usage?: AWSUsageResponse;
  usageStatus: "idle" | "loading" | "ok" | "error";
}

const SourceCard = ({
  source,
  state,
  onManage
}: {
  source: SourceRead;
  state: PerSourceState;
  onManage: () => void;
}) => {
  const usage = state.usage;
  const status = state.integrationStatus;
  const connected = status.kind === "connected";
  return (
    <Card className="aws-source-card">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Cloud className="size-4" aria-hidden="true" />
          <span>{source.name}</span>
          <Badge variant="secondary">{source.type}</Badge>
        </CardTitle>
        <AwsStatusBadge status={status} />
      </CardHeader>
      <CardContent>
        {connected ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-5 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Bucket</p>
                <p className="font-medium">{status.bucket}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Region</p>
                <p className="font-medium">{status.region}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Bucket size</p>
                <p className="font-medium">
                  {state.usageStatus === "loading" ? "…" : usage ? formatBytesGb(usage.total_size_bytes) : "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">
                  <HintLabel
                    label="$ / GB-month"
                    tooltip="Real regional price from the free AWS Price List API. Falls back to a Cryo heuristic when unavailable."
                  />
                </p>
                <p className="font-medium">
                  {usage?.price_per_gb_month != null ? formatCurrency(usage.price_per_gb_month) : "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Objects</p>
                <p className="font-medium">
                  {usage?.object_count != null ? usage.object_count.toLocaleString() : "—"}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={onManage}>
                Manage
              </Button>
              <Link to="/connections">
                <Button size="sm" variant="secondary">
                  <Link2 className="size-3.5" aria-hidden="true" />
                  Open in Connections
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Connect this lakehouse&apos;s S3 bucket to pull free CloudWatch storage metrics and real regional
              pricing.
            </p>
            <Button size="sm" variant="primary" onClick={onManage}>
              <Plus className="size-3.5" aria-hidden="true" />
              Connect AWS
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export const AwsIntegrationsTab = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [managingSourceId, setManagingSourceId] = useState<UUID | null>(null);

  const sources = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api),
    retry: 1
  });

  const sourceIds = useMemo(() => (sources.data ?? []).map((s) => s.id), [sources.data]);
  const awsStatuses = useAwsSourceStatuses(sourceIds);

  const usageIds = useMemo(
    () =>
      sourceIds.filter(
        (id) =>
          awsStatuses[id]?.kind === "connected" &&
          (awsStatuses[id] as Extract<AwsSourceStatus, { kind: "connected" }>).enabled
      ),
    [sourceIds, awsStatuses]
  );

  const usageResults = useQuery({
    queryKey: ["aws-usages-bulk", api.token, usageIds],
    queryFn: async () => {
      const pairs = await Promise.all(
        usageIds.map(async (id) => {
          try {
            const usage = await getAwsUsage(api, id);
            return [id, { usage, status: "ok" as const }];
          } catch (err) {
            return [id, { usage: undefined, status: "error" as const, error: (err as Error).message }];
          }
        })
      );
      return Object.fromEntries(pairs) as Record<
        UUID,
        { usage?: AWSUsageResponse; status: "ok" | "error"; error?: string }
      >;
    },
    enabled: usageIds.length > 0,
    refetchInterval: 60_000
  });

  const perSource: Record<UUID, PerSourceState> = useMemo(() => {
    const map: Record<UUID, PerSourceState> = {};
    sourceIds.forEach((id) => {
      const status = awsStatuses[id] ?? { kind: "loading" };
      const usageEntry = usageResults.data?.[id];
      map[id] = {
        integrationStatus: status,
        usage: usageEntry?.usage,
        usageStatus: usageResults.isLoading
          ? "loading"
          : !usageEntry
            ? "idle"
            : usageEntry.status === "ok"
              ? "ok"
              : "error"
      };
    });
    return map;
  }, [sourceIds, awsStatuses, usageResults.data, usageResults.isLoading]);

  const managedSource = useMemo(
    () => (sources.data ?? []).find((s) => s.id === managingSourceId) ?? null,
    [sources.data, managingSourceId]
  );

  const connectedSources = useMemo(
    () =>
      Object.entries(perSource).filter(
        ([, state]) =>
          state.integrationStatus.kind === "connected" &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>).enabled
      ),
    [perSource]
  );

  const totalBytes = useMemo(
    () => connectedSources.reduce((sum, [, state]) => sum + (state.usage?.total_size_bytes ?? 0), 0),
    [connectedSources]
  );

  const failingAuth = useMemo(
    () =>
      Object.values(perSource).filter(
        (state) =>
          state.integrationStatus.kind === "connected" &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>).enabled &&
          (state.integrationStatus as Extract<AwsSourceStatus, { kind: "connected" }>).lastTestOk === false
      ),
    [perSource]
  );

  const refreshing = useMutation({
    mutationFn: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sources"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usage"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usages-bulk"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration-badge"] })
      ]);
    },
    onSuccess: () => toast.success("Refreshed"),
    onError: (error) => toast.error((error as Error).message)
  });

  const testAll = useMutation({
    mutationFn: async () => {
      const connectedIds = sourceIds.filter((id) => {
        const s = awsStatuses[id];
        return s?.kind === "connected" && (s as Extract<AwsSourceStatus, { kind: "connected" }>).enabled;
      });
      if (connectedIds.length === 0) {
        throw new Error("No AWS integrations to test");
      }
      const results = await Promise.all(
        connectedIds.map((id) =>
          testAwsIntegration(api, id)
            .then((res) => ({ id, ok: res.ok, error: res.error ?? null }))
            .catch((err) => ({ id, ok: false, error: (err as Error).message }))
        )
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["aws-integration"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-integration-badge"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usage"] }),
        queryClient.invalidateQueries({ queryKey: ["aws-usages-bulk"] })
      ]);
      const failed = results.filter((r) => !r.ok);
      if (failed.length === 0) {
        return { okCount: results.length, failCount: 0 };
      }
      throw new Error(
        `${failed.length} of ${results.length} connections failed: ${failed
          .map((f) => f.error)
          .filter(Boolean)
          .slice(0, 2)
          .join("; ")}`
      );
    },
    onSuccess: (summary) =>
      toast.success(`All ${summary.okCount} connection${summary.okCount === 1 ? "" : "s"} OK`),
    onError: (error) => toast.error((error as Error).message)
  });

  const stats = [
    {
      label: "Connected lakehouses",
      value: connectedSources.length,
      tooltip: "Lakehouses with an enabled AWS integration."
    },
    {
      label: "Tracked S3 size",
      value: formatBytesGb(totalBytes),
      tooltip: "Sum of BucketSizeBytes across all connected buckets."
    },
    {
      label: "Auth failures",
      value: failingAuth.length,
      tooltip: "Integrations whose most recent STS test failed."
    },
    {
      label: "Unconfigured lakehouses",
      value: Math.max((sources.data?.length ?? 0) - connectedSources.length, 0),
      tooltip: "Lakehouses without an AWS integration configured."
    }
  ];

  return (
    <OverviewPage
      title="AWS integrations"
      subtitle="Connect S3 buckets to lakehouses so Cryo can show real CloudWatch storage metrics and AWS Price List rates — free tier only, no AWS charges."
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={refreshing.isPending} onClick={() => refreshing.mutate()}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Refresh
          </Button>
          <Button
            variant="primary"
            loading={testAll.isPending}
            onClick={() => testAll.mutate()}
            disabled={connectedSources.length === 0 || role === "viewer"}
          >
            <Zap className="size-3.5" aria-hidden="true" />
            Test all
          </Button>
        </div>
      }
      loading={sources.isLoading}
      error={sources.error}
      errorTitle="Failed to load lakehouses"
      kpis={<StatStrip items={stats} />}
      preContent={
        failingAuth.length > 0 ? (
          <Alert
            variant="error"
            className="page-alert"
            title={`${failingAuth.length} integration${failingAuth.length === 1 ? "" : "s"} failing auth`}
            description="The most recent STS connection test failed for these buckets. Credentials may be rotated or revoked — open Manage to re-test."
          />
        ) : null
      }
    >
      <div className="space-y-3">
        <Alert
          variant="info"
          title="Free-tier AWS APIs only"
          description="Cryo only calls GetMetricStatistics (not GetMetricData), the AWS Price List API, and sts:GetCallerIdentity — all free. See cryo/integrations/aws_s3_metrics.py for the full policy."
        />

        {sources.data && sources.data.length === 0 ? (
          <EmptyState
            title="No lakehouses yet"
            description="Create a lakehouse source first, then come back to connect its AWS bucket."
            action={
              <Link to="/connections">
                <Button variant="primary">
                  <Plus className="size-3.5" aria-hidden="true" />
                  Add a lakehouse
                </Button>
              </Link>
            }
          />
        ) : sources.isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-32 w-full rounded-[var(--radius-default)]" />
            ))}
          </div>
        ) : (
          <div className="aws-source-grid">
            {(sources.data ?? []).map((source) => {
              const state = perSource[source.id] ?? {
                integrationStatus: { kind: "loading" },
                usageStatus: "idle"
              };
              return (
                <SourceCard
                  key={source.id}
                  source={source}
                  state={state}
                  onManage={() => setManagingSourceId(source.id)}
                />
              );
            })}
          </div>
        )}
      </div>

      <Sheet open={managingSourceId !== null} onOpenChange={(open) => !open && setManagingSourceId(null)}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2">
              {managedSource ? (
                <>
                  <Cloud className="size-4" aria-hidden="true" />
                  AWS integration · {managedSource.name}
                </>
              ) : (
                "AWS integration"
              )}
            </SheetTitle>
          </SheetHeader>
          {managedSource ? (
            <AwsIntegrationPanel sourceId={managedSource.id} />
          ) : (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
              <FlaskConical className="size-8 opacity-60" aria-hidden="true" />
              <p className="font-medium text-foreground">Pick a lakehouse</p>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </OverviewPage>
  );
};
