import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  getScannerConfig,
  getScannerStatus,
  getSourceNamespaces,
  getSourceNamespaceTables,
  listRuns,
  listSources,
  patchScannerConfig,
  triggerScan
} from "../../api/client";
import type { RunRead, ScannerConfigUpdate, SourceRead } from "../../api/types";
import { CronInput } from "../../components/CronInput";
import { ConfirmAction } from "../../components/confirm";
import { StatStrip } from "../../components/common";
import { OperationPipeline, scanPipelineSteps } from "../../components/operations/OperationPipeline";
import { toastScanResult } from "../../lib/operations";
import { MiniRunHistoryBar } from "../../components/operations/RunHistoryGrid";
import { describeCronSchedule, nextCronRun } from "../../components/operations/scheduleSummary";
import { StatusTag } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { useAuth } from "../../lib/auth";
import { validateCronExpression } from "../../lib/cron";
import {
  clearAllLakehousePendingScans,
  readConnectionScanNotice,
  readPendingLakehouseScan,
  type ConnectionScanNotice
} from "../../lib/connectionScanPrompt";
import { formatNumber, formatTime } from "../../lib/format";
import { shortRunId, summaryNumber } from "../../lib/operations";
import { WorkflowDetailShell, type WorkflowTabKey } from "./WorkflowDetailShell";

type ScanScope = "all" | "source" | "schemas" | "tables";

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

const ScopeSegmented = ({
  value,
  onChange
}: {
  value: ScanScope;
  onChange: (value: ScanScope) => void;
}) => (
  <div className="grid grid-cols-4 gap-1 rounded-[var(--radius-default)] border border-border p-1">
    {SCOPE_OPTIONS.map((option) => (
      <Button
        key={option.value}
        type="button"
        size="sm"
        variant={value === option.value ? "primary" : "ghost"}
        className="w-full"
        onClick={() => onChange(option.value)}
      >
        {option.label}
      </Button>
    ))}
  </div>
);

const ScanScopeForm = ({
  canOperate,
  hasActiveRun,
  onQueued
}: {
  canOperate: boolean;
  hasActiveRun: boolean;
  onQueued: (runId: string) => void;
}) => {
  const { api } = useAuth();
  const [scope, setScope] = useState<ScanScope>("all");
  const [sourceId, setSourceId] = useState<string | undefined>();
  const [schemaNames, setSchemaNames] = useState<string[]>([]);
  const [tableIds, setTableIds] = useState<string[]>([]);
  const [unscannedTableSelection, setUnscannedTableSelection] = useState<string[]>([]);
  const needsSource = scope === "source" || scope === "schemas" || scope === "tables";

  const sources = useQuery({
    queryKey: ["sources-for-scan"],
    queryFn: () => listSources(api),
    enabled: needsSource
  });
  const sourceNamespaces = useQuery({
    queryKey: ["source-namespaces-for-scan", sourceId],
    queryFn: () => getSourceNamespaces(api, sourceId as string),
    enabled: Boolean(sourceId) && (scope === "schemas" || scope === "tables")
  });
  const singleNarrowedSchema = scope === "tables" && schemaNames.length === 1 ? schemaNames[0] : undefined;
  const namespaceTables = useQuery({
    queryKey: ["source-namespace-tables-for-scan", sourceId, singleNarrowedSchema],
    queryFn: () => getSourceNamespaceTables(api, sourceId as string, singleNarrowedSchema as string),
    enabled: Boolean(sourceId) && Boolean(singleNarrowedSchema)
  });

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
      toastScanResult(result);
      onQueued(result.run_id);
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const canSubmit =
    scope === "all" ||
    (scope === "source" && Boolean(sourceId)) ||
    (scope === "schemas" && Boolean(sourceId) && schemaNames.length > 0) ||
    (scope === "tables" && (tableIds.length > 0 || unscannedTableSelection.length > 0));

  const selectedSourceName = (sources.data ?? []).find((s: SourceRead) => s.id === sourceId)?.name;
  const selectedTableCount = tableIds.length + unscannedTableSelection.length;

  const scopeDescription = useMemo(() => {
    if (scope === "all") return "the entire catalog (every configured lakehouse)";
    if (scope === "source") return selectedSourceName ? `lakehouse "${selectedSourceName}"` : "the selected lakehouse";
    if (scope === "schemas") {
      const schemaText = schemaNames.length > 0 ? schemaNames.join(", ") : "the selected schemas";
      return `${schemaNames.length} schema${schemaNames.length === 1 ? "" : "s"} (${schemaText}) in ${selectedSourceName ?? "the selected lakehouse"}`;
    }
    return `${selectedTableCount} table${selectedTableCount === 1 ? "" : "s"} in ${selectedSourceName ?? "the selected lakehouse"}`;
  }, [scope, schemaNames, selectedSourceName, selectedTableCount]);

  const confirmDescription = (
    <div className="space-y-1.5 text-sm">
      <p>
        This queues a <strong>full scan</strong> (not incremental) of {scopeDescription}.
      </p>
      {hasActiveRun && scope === "all" ? (
        <p className="text-destructive">
          A scan is already running. Another full scan cannot start until it finishes.
        </p>
      ) : null}
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label>Scope</Label>
        <ScopeSegmented value={scope} onChange={setScope} />
      </div>
      {needsSource ? (
        <div className="space-y-1.5">
          <Label htmlFor="scan-source">Lakehouse</Label>
          <select
            id="scan-source"
            className="flex h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
            value={sourceId ?? ""}
            disabled={sources.isLoading}
            onChange={(event) => {
              const value = event.target.value || undefined;
              setSourceId(value);
              setSchemaNames([]);
              setTableIds([]);
              setUnscannedTableSelection([]);
            }}
          >
            <option value="">Select a lakehouse</option>
            {(sources.data ?? []).map((s: SourceRead) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {scope === "schemas" && sourceId ? (
        <fieldset className="space-y-1.5">
          <legend className="text-sm font-medium">Schemas</legend>
          {sourceNamespaces.isError ? (
            <p role="alert" className="text-sm text-destructive">Could not load schemas.</p>
          ) : (
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-[var(--radius-default)] border border-border p-2">
              {(sourceNamespaces.data?.namespaces ?? []).map((name) => (
                <label key={name} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={schemaNames.includes(name)}
                    onChange={(event) =>
                      setSchemaNames((current) =>
                        event.target.checked ? [...current, name] : current.filter((item) => item !== name)
                      )
                    }
                  />
                  {name}
                </label>
              ))}
              {sourceNamespaces.isSuccess && (sourceNamespaces.data?.namespaces ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No schemas found for this lakehouse.</p>
              ) : null}
            </div>
          )}
        </fieldset>
      ) : null}
      {scope === "tables" && sourceId ? (
        <div className="space-y-3">
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">Schema</legend>
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-[var(--radius-default)] border border-border p-2">
              {(sourceNamespaces.data?.namespaces ?? []).map((name) => (
                <label key={name} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="scan-schema"
                    checked={schemaNames[0] === name && schemaNames.length === 1}
                    onChange={() => {
                      setSchemaNames([name]);
                      setUnscannedTableSelection([]);
                    }}
                  />
                  {name}
                </label>
              ))}
            </div>
          </fieldset>
          {singleNarrowedSchema ? (
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium">Tables</legend>
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-[var(--radius-default)] border border-border p-2">
                {(namespaceTables.data?.tables ?? []).map((name) => (
                  <label key={name} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={unscannedTableSelection.includes(name)}
                      onChange={(event) =>
                        setUnscannedTableSelection((current) =>
                          event.target.checked ? [...current, name] : current.filter((item) => item !== name)
                        )
                      }
                    />
                    {name}
                  </label>
                ))}
                {namespaceTables.isSuccess && (namespaceTables.data?.tables ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No tables found in this schema.</p>
                ) : null}
              </div>
            </fieldset>
          ) : null}
        </div>
      ) : null}
      <ConfirmAction
        title="Trigger this scan?"
        description={confirmDescription}
        onConfirm={() => trigger.mutate()}
      >
        <Button
          variant="primary"
          className="w-full"
          loading={trigger.isPending}
          disabled={!canOperate || !canSubmit || (hasActiveRun && scope === "all")}
        >
          Trigger scan
        </Button>
      </ConfirmAction>
    </div>
  );
};

const ScannerRunsTab = () => {
  const { api, token } = useAuth();
  const runs = useQuery({
    queryKey: ["scan-runs", token],
    queryFn: () => listRuns(api, { kind: "scan", limit: 100 })
  });

  const columns = useMemo<ColumnDef<RunRead, unknown>[]>(
    () => [
      {
        id: "run",
        header: "Run",
        cell: ({ row }) => (
          <Link to={`/operations/runs/${row.original.id}`} className="font-mono text-sm text-primary hover:underline">
            {shortRunId(row.original.id)}
          </Link>
        )
      },
      { id: "target", header: "Target", cell: ({ row }) => targetLabel(row.original) },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <StatusTag status={row.original.status} />
      },
      {
        id: "scanned",
        header: "Scanned",
        cell: ({ row }) => formatNumber(summaryNumber(row.original.summary, "scanned"))
      },
      {
        id: "failed",
        header: "Failed",
        cell: ({ row }) => formatNumber(summaryNumber(row.original.summary, "failed"))
      },
      {
        id: "created",
        header: "Created",
        cell: ({ row }) => formatTime(row.original.created_at)
      }
    ],
    []
  );

  return (
    <DataTable
      columns={columns}
      data={runs.data ?? []}
      density="compact"
      loading={runs.isLoading}
      getRowId={(row) => row.id}
      emptyTitle="No scan runs yet"
      emptyDescription="Trigger a scan from the Config tab."
    />
  );
};

const ScannerOverviewTab = () => {
  const { api, token } = useAuth();
  const status = useQuery({
    queryKey: ["scanner-status", token],
    queryFn: () => getScannerStatus(api),
    refetchInterval: 15000
  });
  const runs = useQuery({
    queryKey: ["scan-runs-overview", token],
    queryFn: () => listRuns(api, { kind: "scan", limit: 20 })
  });
  const activeRun = status.data?.active_run;
  const latest = runs.data?.[0];

  const [liveMessage, setLiveMessage] = useState("");
  const prevActiveRunIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (status.isLoading) return;
    const prevId = prevActiveRunIdRef.current;
    const currentId = activeRun?.id ?? null;
    if (prevId !== undefined && prevId !== currentId) {
      if (currentId) setLiveMessage(`Scan ${shortRunId(currentId)} started.`);
      else if (prevId) setLiveMessage(`Scan ${shortRunId(prevId)} finished.`);
    }
    prevActiveRunIdRef.current = currentId;
  }, [activeRun?.id, status.isLoading]);

  return (
    <div className="flex flex-col gap-3">
      <span className="sr-only" role="status" aria-live="polite">
        {liveMessage}
      </span>
      {status.isError ? (
        <Alert
          variant="error"
          title="Couldn't load scanner status"
          description={
            <div className="flex flex-wrap items-center gap-2">
              <span>{(status.error as Error)?.message ?? "Unknown error."}</span>
              <Button size="sm" variant="secondary" onClick={() => void status.refetch()}>
                Retry
              </Button>
            </div>
          }
        />
      ) : null}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Scanner status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <StatStrip
            items={[
              {
                label: "Tables with stats",
                value: `${formatNumber(status.data?.tables_with_stats)} / ${formatNumber(status.data?.total_tables)}`
              },
              { label: "Queue depth", value: formatNumber(status.data?.queue_depth ?? null) },
              { label: "Next scheduled scan", value: formatTime(status.data?.next_scan_at) }
            ]}
          />
          {activeRun ? (
            <Alert
              variant="info"
              title={`Active scan ${shortRunId(activeRun.id)}`}
              description={
                <span className="inline-flex flex-wrap items-center gap-2">
                  <Link to={`/operations/runs/${activeRun.id}`} className="font-mono text-primary hover:underline">
                    Open run
                  </Link>
                  <StatusTag status={activeRun.status} />
                </span>
              }
            />
          ) : null}
        </CardContent>
      </Card>
      {latest ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Last run</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2">
              <Link to={`/operations/runs/${latest.id}`} className="font-mono text-sm text-primary hover:underline">
                {shortRunId(latest.id)}
              </Link>
              <StatusTag status={latest.status} />
              <MiniRunHistoryBar runs={runs.data ?? []} />
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
};

const ScannerGraphTab = () => {
  const { api, token } = useAuth();
  const status = useQuery({
    queryKey: ["scanner-status-graph", token],
    queryFn: () => getScannerStatus(api)
  });
  const steps = scanPipelineSteps(status.data?.active_run?.status ?? status.data?.latest_run?.status);
  return <OperationPipeline steps={steps} title="Scan fan-out" />;
};

const ScannerConfigTab = () => {
  const { api, role, token } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const canOperate = role !== "viewer";

  const config = useQuery({ queryKey: ["scanner-config", token], queryFn: () => getScannerConfig(api) });
  const status = useQuery({ queryKey: ["scanner-status-config", token], queryFn: () => getScannerStatus(api) });

  const [formValues, setFormValues] = useState<ScannerConfigUpdate>({
    enabled: true,
    schedule_cron: "",
    num_chunks: 8,
    max_concurrent_runs: 1
  });
  const [cronError, setCronError] = useState<string | null>(null);

  useEffect(() => {
    if (config.data) setFormValues(config.data);
  }, [config.data]);

  const saveConfig = useMutation({
    mutationFn: (values: ScannerConfigUpdate) => patchScannerConfig(api, values),
    onSuccess: () => {
      toast.success("Scanner schedule saved");
      void queryClient.invalidateQueries({ queryKey: ["scanner-config"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const handleSave = (event: React.FormEvent) => {
    event.preventDefault();
    const error = validateCronExpression(formValues.schedule_cron, { allowEmpty: false });
    if (error) {
      setCronError(error);
      return;
    }
    setCronError(null);
    saveConfig.mutate(formValues);
  };

  const liveCronError = validateCronExpression(formValues.schedule_cron, { allowEmpty: false });
  const scheduleSummary = !liveCronError ? describeCronSchedule(formValues.schedule_cron) : null;
  const nextRun = !liveCronError ? nextCronRun(formValues.schedule_cron) : null;

  const configLoadFailed = config.isError || status.isError;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {configLoadFailed ? (
        <Alert
          className="lg:col-span-2"
          variant="error"
          title="Couldn't load scanner configuration"
          description={
            <div className="flex flex-wrap items-center gap-2">
              <span>
                {(config.error as Error)?.message ?? (status.error as Error)?.message ?? "Unknown error."}
              </span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  void config.refetch();
                  void status.refetch();
                }}
              >
                Retry
              </Button>
            </div>
          }
        />
      ) : null}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Trigger a scan</CardTitle>
        </CardHeader>
        <CardContent>
          <ScanScopeForm
            canOperate={canOperate}
            hasActiveRun={Boolean(status.data?.active_run)}
            onQueued={(runId) => {
              void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
              navigate(`/operations/runs/${runId}`);
            }}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Schedule</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSave}>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="scanner-enabled">Scheduled scans</Label>
              <Switch
                id="scanner-enabled"
                checked={formValues.enabled}
                disabled={!canOperate}
                onCheckedChange={(checked) => setFormValues((prev) => ({ ...prev, enabled: checked }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="scanner-cron">Cron schedule</Label>
              <CronInput
                value={formValues.schedule_cron ?? ""}
                onChange={(value) => {
                  setFormValues((prev) => ({ ...prev, schedule_cron: value }));
                  setCronError(validateCronExpression(value, { allowEmpty: false }));
                }}
              />
              {cronError ? (
                <p className="text-xs text-destructive" role="alert">
                  {cronError}
                </p>
              ) : scheduleSummary ? (
                <p className="text-xs text-muted-foreground">
                  {scheduleSummary}
                  {nextRun ? ` · Next run: ${formatTime(nextRun.toISOString())}` : ""}
                </p>
              ) : null}
              {status.data?.next_scan_at ? (
                <p className="text-xs text-muted-foreground">
                  Scanner-reported next scan: {formatTime(status.data.next_scan_at)}
                </p>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="scanner-chunks">Chunks</Label>
                <Input
                  id="scanner-chunks"
                  type="number"
                  min={1}
                  max={128}
                  disabled={!canOperate}
                  value={formValues.num_chunks}
                  onChange={(event) =>
                    setFormValues((prev) => ({ ...prev, num_chunks: Number(event.target.value) }))
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="scanner-concurrency">Max concurrency</Label>
                <Input
                  id="scanner-concurrency"
                  type="number"
                  min={1}
                  max={4}
                  disabled={!canOperate}
                  value={formValues.max_concurrent_runs ?? 1}
                  onChange={(event) =>
                    setFormValues((prev) => ({ ...prev, max_concurrent_runs: Number(event.target.value) }))
                  }
                />
              </div>
            </div>
            <Button type="submit" variant="primary" loading={saveConfig.isPending} disabled={!canOperate}>
              Save schedule
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
};

const ScannerGridTab = () => {
  const { api, token } = useAuth();
  const runs = useQuery({
    queryKey: ["scan-grid-runs", token],
    queryFn: () => listRuns(api, { kind: "scan", limit: 8 })
  });
  const shards = ["Discover", "Assign", "Scan", "Finalize"];

  const columns = useMemo<ColumnDef<{ key: string; shard: string }, unknown>[]>(() => {
    const base: ColumnDef<{ key: string; shard: string }, unknown>[] = [
      { id: "phase", header: "Phase", cell: ({ row }) => row.original.shard }
    ];
    const runCols = (runs.data ?? []).map(
      (run): ColumnDef<{ key: string; shard: string }, unknown> => ({
        id: run.id,
        header: shortRunId(run.id),
        size: 90,
        cell: () => (
          <Link to={`/operations/runs/${run.id}`}>
            <StatusTag status={run.status} />
          </Link>
        )
      })
    );
    return [...base, ...runCols];
  }, [runs.data]);

  const data = shards.map((shard) => ({ key: shard, shard }));

  return <DataTable columns={columns} data={data} density="compact" getRowId={(row) => row.key} />;
};

const VALID_SCANNER_TABS = new Set(["overview", "grid", "runs", "config"]);

export const ScannerDetail = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { api, role } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [connectionNotice, setConnectionNotice] = useState<ConnectionScanNotice | null>(() =>
    readConnectionScanNotice(location.state) ?? readPendingLakehouseScan()
  );

  const rawTab = searchParams.get("tab");
  const activeTab = (rawTab && VALID_SCANNER_TABS.has(rawTab) ? rawTab : "overview") as WorkflowTabKey;

  const scanNoticeSource = useMutation({
    mutationFn: (sourceId: string) =>
      triggerScan(api, { scope: "source", source_id: sourceId, full_scan: true, trigger: "web-scanner" }),
    onSuccess: (result) => {
      toastScanResult(result);
      clearAllLakehousePendingScans();
      setConnectionNotice(null);
      navigate(`/operations/runs/${result.run_id}`, { replace: true, state: {} });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  return (
    <div>
      {connectionNotice ? (
        <Alert
          className="page-alert mb-3"
          variant="success"
          title={`Lakehouse "${connectionNotice.name}" is connected`}
          description={
            <ConfirmAction
              title={`Scan "${connectionNotice.name}" now?`}
              onConfirm={() => scanNoticeSource.mutate(connectionNotice.id)}
            >
              <Button variant="primary" loading={scanNoticeSource.isPending} disabled={role === "viewer"}>
                Scan this lakehouse now
              </Button>
            </ConfirmAction>
          }
          onDismiss={() => setConnectionNotice(null)}
        />
      ) : null}
      <WorkflowDetailShell
        title="Scanner"
        subtitle="Catalog discovery and health statistics across your lakehouses."
        hiddenTabs={["graph"]}
        activeTab={activeTab}
        onTabChange={(tab) => {
          setSearchParams(
            (previous) => {
              const next = new URLSearchParams(previous);
              if (tab === "overview") next.delete("tab");
              else next.set("tab", tab);
              return next;
            },
            { replace: true }
          );
        }}
        overview={<ScannerOverviewTab />}
        grid={<ScannerGridTab />}
        graph={<ScannerGraphTab />}
        runs={<ScannerRunsTab />}
        config={<ScannerConfigTab />}
      />
    </div>
  );
};
