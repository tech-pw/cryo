import { Plus, RefreshCw } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getScannerStatus, listPresets, listRuns } from "../../api/client";
import type { PresetRead, RunRead } from "../../api/types";
import { PageHeader, StatStrip } from "../../components/common";
import { MiniRunHistoryBar } from "../../components/operations/RunHistoryGrid";
import { StatusTag, RunKindTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatNumber, formatTime } from "../../lib/format";
import { formatPresetSummary } from "../../lib/presetForm";
import {
  ACTIVE_RUN_STATUSES,
  shortRunId,
  targetLabel
} from "../../lib/operations";

const RUN_FILTER_VALUES = new Set(["all", "failed", "active"]);
const isRunFilter = (value: string | null): value is "all" | "failed" | "active" =>
  value !== null && RUN_FILTER_VALUES.has(value);

const HealthDot = ({ ok, label }: { ok?: boolean; label: string }) => (
  <span className="inline-flex items-center gap-1.5">
    <span
      className={cn("size-2 rounded-full", ok ? "bg-[var(--color-status-success)]" : "bg-[var(--color-status-warning)]")}
      aria-hidden
    />
    <span>{label}</span>
  </span>
);

type WorkflowRow = {
  key: string;
  name: string;
  description: string;
  schedule: string;
  enabled: boolean;
  runs: RunRead[];
  to: string;
  isScanner: boolean;
  preset?: PresetRead;
};

const RUNS_PAGE_SIZE = 12;

export const OperationsHome = () => {
  const { api, token, role } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "runs" ? "runs" : "workflows";
  const runFilterParam = searchParams.get("runFilter");
  const runFilter = isRunFilter(runFilterParam) ? runFilterParam : "all";
  const runsPageParam = Number.parseInt(searchParams.get("runsPage") ?? "0", 10);
  const runsPage = Number.isFinite(runsPageParam) && runsPageParam >= 0 ? runsPageParam : 0;

  const setRunFilter = (filter: "all" | "failed" | "active") => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", "runs");
      if (filter === "all") {
        next.delete("runFilter");
      } else {
        next.set("runFilter", filter);
      }
      next.delete("runsPage");
      return next;
    });
  };

  const setRunsPage = (page: number) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (page === 0) {
        next.delete("runsPage");
      } else {
        next.set("runsPage", String(page));
      }
      return next;
    });
  };

  const scannerStatus = useQuery({
    queryKey: ["scanner-status", token],
    queryFn: () => getScannerStatus(api),
    refetchInterval: 15000
  });
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const allRuns = useQuery({
    queryKey: ["operations-runs", token],
    queryFn: () => listRuns(api, { limit: 200 }),
    refetchInterval: 10000
  });
  const scanRuns = useQuery({
    queryKey: ["operations-scan-runs", token],
    queryFn: () => listRuns(api, { kind: "scan", limit: 20 })
  });

  const runs = allRuns.data ?? [];
  const failed24h = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return runs.filter((run) => run.status === "failed" && Date.parse(run.created_at) >= cutoff);
  }, [runs]);
  const activeRuns = useMemo(() => runs.filter((run) => ACTIVE_RUN_STATUSES.has(run.status)), [runs]);

  const [liveMessage, setLiveMessage] = useState("");
  const prevCountsRef = useRef<{ active: number; failed: number } | null>(null);
  useEffect(() => {
    if (allRuns.isLoading) return;
    const prev = prevCountsRef.current;
    if (prev) {
      if (activeRuns.length > prev.active) {
        setLiveMessage(`${activeRuns.length} run${activeRuns.length === 1 ? "" : "s"} now active.`);
      } else if (failed24h.length > prev.failed) {
        setLiveMessage(`${failed24h.length} run${failed24h.length === 1 ? "" : "s"} failed in the last 24 hours.`);
      } else if (activeRuns.length === 0 && prev.active > 0) {
        setLiveMessage("All active runs finished.");
      }
    }
    prevCountsRef.current = { active: activeRuns.length, failed: failed24h.length };
  }, [activeRuns.length, allRuns.isLoading, failed24h.length]);

  const runsByPreset = useMemo(() => {
    const map = new Map<string, RunRead[]>();
    for (const run of runs) {
      if (!run.preset_id) continue;
      const list = map.get(run.preset_id) ?? [];
      list.push(run);
      map.set(run.preset_id, list);
    }
    for (const [key, list] of map) {
      map.set(
        key,
        list.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
      );
    }
    return map;
  }, [runs]);

  const filteredRuns = useMemo(() => {
    if (runFilter === "failed") return runs.filter((r) => r.status === "failed");
    if (runFilter === "active") return activeRuns;
    return runs;
  }, [activeRuns, runFilter, runs]);

  const paginatedRuns = useMemo(() => {
    const start = runsPage * RUNS_PAGE_SIZE;
    return filteredRuns.slice(start, start + RUNS_PAGE_SIZE);
  }, [filteredRuns, runsPage]);

  const workflowRows = useMemo<WorkflowRow[]>(() => {
    const scannerRow: WorkflowRow = {
      key: "scanner",
      name: "Scanner",
      description: "Catalog discovery and health statistics",
      schedule: scannerStatus.data?.config?.schedule_cron ?? "—",
      enabled: scannerStatus.data?.config?.enabled ?? false,
      runs: scanRuns.data ?? [],
      to: "/operations/scanner",
      isScanner: true
    };
    const presetRows = (presets.data ?? []).map((preset: PresetRead) => ({
      key: preset.id,
      name: preset.name,
      description: preset.description ?? formatPresetSummary(preset),
      schedule: preset.schedule_cron ?? "—",
      enabled: preset.enabled,
      runs: runsByPreset.get(preset.id) ?? [],
      to: `/operations/presets/${preset.id}`,
      isScanner: false as const,
      preset
    }));
    return [scannerRow, ...presetRows];
  }, [presets.data, runsByPreset, scanRuns.data, scannerStatus.data]);

  const runColumns = useMemo<ColumnDef<RunRead, unknown>[]>(
    () => [
      {
        id: "run",
        header: "Run",
        size: 96,
        cell: ({ row }) => (
          <Link
            to={`/operations/runs/${row.original.id}`}
            className="font-mono text-sm text-primary hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {shortRunId(row.original.id)}
          </Link>
        )
      },
      {
        id: "workflow",
        header: "Workflow",
        cell: ({ row }) =>
          row.original.kind === "scan" ? (
            <Link to="/operations/scanner" onClick={(e) => e.stopPropagation()}>
              Scanner
            </Link>
          ) : row.original.preset_id ? (
            <Link to={`/operations/presets/${row.original.preset_id}`} onClick={(e) => e.stopPropagation()}>
              Preset {shortRunId(row.original.preset_id)}
            </Link>
          ) : (
            targetLabel(row.original)
          )
      },
      { id: "target", header: "Target", cell: ({ row }) => targetLabel(row.original) },
      {
        id: "status",
        header: "Status",
        size: 110,
        cell: ({ row }) => <StatusTag status={row.original.status} />
      },
      {
        id: "kind",
        header: "Kind",
        size: 110,
        cell: ({ row }) => <RunKindTag kind={row.original.kind} />
      },
      {
        id: "trigger",
        header: "Trigger",
        size: 120,
        cell: ({ row }) => row.original.trigger ?? "manual"
      },
      {
        id: "created",
        header: "Created",
        size: 148,
        cell: ({ row }) => formatTime(row.original.created_at)
      }
    ],
    []
  );

  const workflowColumns = useMemo<ColumnDef<WorkflowRow, unknown>[]>(
    () => [
      {
        id: "workflow",
        header: "Workflow",
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5">
              <Link
                to={row.original.to}
                className="font-semibold text-primary hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {row.original.name}
              </Link>
              {row.original.isScanner ? <Badge variant="secondary">System</Badge> : row.original.enabled ? null : <Badge variant="outline">Paused</Badge>}
            </div>
            <span className="secondary-line text-xs text-muted-foreground">{row.original.description}</span>
          </div>
        )
      },
      { id: "schedule", header: "Schedule", size: 140, cell: ({ row }) => row.original.schedule },
      {
        id: "lastRuns",
        header: "Last runs",
        size: 180,
        cell: ({ row }) => <MiniRunHistoryBar runs={row.original.runs} />
      },
      {
        id: "status",
        header: "Status",
        size: 110,
        cell: ({ row }) => {
          const latest = row.original.runs[0];
          return latest ? <StatusTag status={latest.status} /> : <Badge variant="secondary">Not run</Badge>;
        }
      },
      {
        id: "actions",
        header: "",
        size: 160,
        cell: ({ row }) => (
          <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="secondary" asChild>
              <Link to={row.original.to}>Open</Link>
            </Button>
            {row.original.isScanner ? (
              <Button size="sm" variant="primary" asChild>
                <Link to="/operations/scanner">Trigger</Link>
              </Button>
            ) : (
              <Button size="sm" variant="secondary" asChild>
                <Link to={`${row.original.to}?tab=config`}>Edit</Link>
              </Button>
            )}
          </div>
        )
      }
    ],
    []
  );

  return (
    <div>
      <PageHeader
        title="Operations"
        subtitle="Workflows, scans, and maintenance runs in one place."
        rootCrumb={{ label: "Operations", to: "/operations" }}
        actions={
          role !== "viewer" ? (
            <div className="flex items-center gap-2">
              <Button variant="secondary" asChild>
                <Link to="/operations/scanner">
                  <RefreshCw className="size-4" aria-hidden />
                  Scanner
                </Link>
              </Button>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span tabIndex={0}>
                    <Button variant="primary" disabled>
                      <Plus className="size-4" aria-hidden />
                      New preset
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  Preset creation isn&apos;t available in this UI yet. Ask an admin to create presets via the API.
                </TooltipContent>
              </Tooltip>
            </div>
          ) : undefined
        }
      />

      <span className="sr-only" role="status" aria-live="polite">
        {liveMessage}
      </span>

      <div className="flex flex-col gap-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Fleet health</CardTitle>
          </CardHeader>
          <CardContent>
            <StatStrip
              items={[
                {
                  label: "Scanner",
                  value: (
                    <HealthDot
                      ok={!scannerStatus.data?.active_run}
                      label={scannerStatus.data?.active_run ? "Running" : "Idle"}
                    />
                  )
                },
                {
                  label: "Tables with stats",
                  value: `${formatNumber(scannerStatus.data?.tables_with_stats)} / ${formatNumber(scannerStatus.data?.total_tables)}`
                },
                {
                  label: "Failed runs (24h)",
                  value: formatNumber(failed24h.length)
                },
                {
                  label: "Active runs",
                  value: formatNumber(activeRuns.length)
                },
                {
                  label: "Queue depth",
                  value: formatNumber(scannerStatus.data?.queue_depth ?? null)
                }
              ]}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Quick links</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setRunFilter("failed")}
              >
                Failed runs ({failed24h.length})
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setRunFilter("active")}
              >
                Running now ({activeRuns.length})
              </Button>
              <Button size="sm" variant="secondary" asChild>
                <Link to="/operations/scanner">Overdue scans</Link>
              </Button>
              <Button size="sm" variant="secondary" asChild>
                <Link to="/smart-maintenance">Smart maintenance</Link>
              </Button>
            </div>
          </CardContent>
        </Card>

        <Tabs
          value={tab}
          onValueChange={(key) =>
            setSearchParams((prev) => {
              const next = new URLSearchParams(prev);
              if (key === "runs") {
                next.set("tab", "runs");
              } else {
                next.delete("tab");
              }
              return next;
            })
          }
        >
          <TabsList>
            <TabsTrigger value="workflows">Workflows</TabsTrigger>
            <TabsTrigger value="runs">All runs</TabsTrigger>
          </TabsList>

          <TabsContent value="workflows">
            <DataTable
              columns={workflowColumns}
              data={workflowRows}
              density="compact"
              getRowId={(row) => row.key}
              emptyTitle="No workflows"
              emptyDescription="Scanner and maintenance presets appear here."
            />
          </TabsContent>

          <TabsContent value="runs">
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant={runFilter === "all" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("all")}
                >
                  All
                </Button>
                <Button
                  size="sm"
                  variant={runFilter === "failed" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("failed")}
                >
                  Failed
                </Button>
                <Button
                  size="sm"
                  variant={runFilter === "active" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("active")}
                >
                  Active
                </Button>
              </div>
              <DataTable
                columns={runColumns}
                data={paginatedRuns}
                density="compact"
                loading={allRuns.isLoading}
                getRowId={(row) => row.id}
                emptyTitle="No runs yet"
                emptyDescription="Scan and maintenance executions appear here once queued."
                pagination={{
                  pageIndex: runsPage,
                  pageSize: RUNS_PAGE_SIZE,
                  totalRows: filteredRuns.length,
                  onPageChange: setRunsPage
                }}
              />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};
