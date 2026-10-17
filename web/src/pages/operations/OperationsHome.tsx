import { MoreHorizontal, RefreshCw, Sparkles } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getScannerStatus, listPendingApprovals, listPresets, listRuns } from "../../api/client";
import type { PresetRead, RunRead } from "../../api/types";
import { PageHeader, QueryErrorAlert } from "../../components/common";
import { describeCronSchedule } from "../../components/operations/scheduleSummary";
import { StatusTag, RunKindTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatDuration, formatNumber, formatPercent, formatTime } from "../../lib/format";
import { formatPresetSummary } from "../../lib/presetForm";
import {
  ACTIVE_RUN_STATUSES,
  isScanRun,
  realizedSavingsLabel,
  runDurationMs,
  shortRunId,
  summaryNumber,
  summaryText,
  targetLabel
} from "../../lib/operations";
import { ActionsTab } from "./ActionsTab";
import { DecisionFunnelStrip } from "./DecisionFunnelStrip";
import { EngineLoadStrip } from "./EngineLoadStrip";
import {
  latestNightlyRunPath,
  NIGHTLY_SCHEDULE_LABEL,
  NIGHTLY_WORKFLOW_NAME,
  nightlyBatchRuns,
  NO_PRESETS_COPY
} from "./nightlyBatch";
import { DurationCell, RunDurationBars, RunStateCounts, RunStateLabel, RunTimeline } from "./RunBars";
import { countByTone, formatRelative, successRate } from "./runState";
import "./operationsHome.css";

const ControlRoom = lazy(() => import("../ControlRoom").then((m) => ({ default: m.ControlRoom })));

const RUN_FILTER_VALUES = new Set(["all", "failed", "active"]);
const isRunFilter = (value: string | null): value is "all" | "failed" | "active" =>
  value !== null && RUN_FILTER_VALUES.has(value);

type PulseTone = "neutral" | "active" | "danger" | "success";

const PulseTile = ({
  label,
  value,
  context,
  tone = "neutral",
  selected,
  onClick,
  to
}: {
  label: string;
  value: ReactNode;
  context?: ReactNode;
  tone?: PulseTone;
  selected?: boolean;
  onClick?: () => void;
  to?: string;
}) => {
  const body = (
    <>
      <span className="ops-pulse__label">
        <span className="ops-pulse__dot" aria-hidden="true" />
        {label}
      </span>
      <span className="ops-pulse__value">{value}</span>
      {context ? <span className="ops-pulse__context">{context}</span> : null}
    </>
  );
  const className = cn("ops-pulse__tile", `ops-pulse__tile--${tone}`, selected && "is-selected");
  if (to) {
    return (
      <Link to={to} className={className}>
        {body}
      </Link>
    );
  }
  return (
    <button type="button" className={className} aria-pressed={selected} onClick={onClick}>
      {body}
    </button>
  );
};

const FilterCount = ({ value }: { value: number }) => (
  <span aria-hidden="true" className="ops-filter-count">
    {formatNumber(value)}
  </span>
);

type WorkflowRow = {
  key: string;
  name: string;
  description: string;
  schedule: string;
  scheduleCron: string | null;
  enabled: boolean;
  runs: RunRead[];
  to: string | null;
  isScanner: boolean;
  isNightly: boolean;
  preset?: PresetRead;
};

const HISTORY_COLUMN_VISIBILITY = { kind: false };

const plural = (count: number, noun: string) => `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;

const runResultLabel = (run: RunRead): string => {
  const savings = realizedSavingsLabel(run);
  if (savings) return savings;
  const summary = run.summary;
  if (!summary) return "—";
  const succeeded = summaryNumber(summary, "succeeded");
  const failed = summaryNumber(summary, "failed");
  const partial = summaryNumber(summary, "partial");
  if (succeeded === 0 && failed === 0 && partial === 0) return "—";
  const parts = [`${formatNumber(succeeded)} succeeded`];
  if (failed > 0) parts.push(`${formatNumber(failed)} failed`);
  if (partial > 0) parts.push(`${formatNumber(partial)} partial`);
  return parts.join(" · ");
};

const runTargetLabel = (run: RunRead): string => {
  const explicit = summaryText(run.summary, "target_label");
  if (explicit) return explicit;
  const selected = summaryNumber(run.summary, "selected");
  if (selected > 0) return plural(selected, "table");
  return targetLabel(run);
};

const WorkflowMenu = ({ row }: { row: WorkflowRow }) => {
  if (!row.to) return null;
  const extra = row.isScanner
    ? { label: "Trigger", to: "/runs/scanner?tab=config" }
    : row.isNightly
      ? null
      : { label: "Edit", to: `${row.to}?tab=config` };
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="size-7 px-0"
          aria-label={`${row.name} actions`}
          onClick={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-auto p-0">
        <div className="ops-row-menu" onClick={(event) => event.stopPropagation()}>
          <Link to={row.to}>Open</Link>
          {extra ? <Link to={extra.to}>{extra.label}</Link> : null}
        </div>
      </PopoverContent>
    </Popover>
  );
};

const RUNS_PAGE_SIZE = 12;

export const OperationsHome = () => {
  const { api, token, role } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab =
    tabParam === "runs" || tabParam === "history"
      ? "runs"
      : tabParam === "actions" || tabParam === "holds"
        ? tabParam
        : "workflows";
  const runFilterParam = searchParams.get("runFilter");
  const runFilter = isRunFilter(runFilterParam) ? runFilterParam : "all";
  const runsPageParam = Number.parseInt(searchParams.get("runsPage") ?? "0", 10);
  const runsPage = Number.isFinite(runsPageParam) && runsPageParam >= 0 ? runsPageParam : 0;

  const setRunFilter = (filter: "all" | "failed" | "active") => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", "history");
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
  const pendingApprovals = useQuery({
    queryKey: ["pending-approvals", token],
    queryFn: () => listPendingApprovals(api),
    retry: 1
  });

  const runs = allRuns.data ?? [];
  const failed24h = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return runs.filter((run) => run.status === "failed" && Date.parse(run.created_at) >= cutoff);
  }, [runs]);
  const activeRuns = useMemo(() => runs.filter((run) => ACTIVE_RUN_STATUSES.has(run.status)), [runs]);
  const pulse = useMemo(() => {
    const now = Date.now();
    const day = runs.filter((run) => Date.parse(run.created_at) >= now - 864e5);
    const week = runs.filter((run) => Date.parse(run.created_at) >= now - 7 * 864e5);
    const dayCounts = countByTone(day);
    const weekCounts = countByTone(week);
    return {
      queued: countByTone(activeRuns).queued,
      succeeded24h: dayCounts.succeeded,
      finished24h: dayCounts.succeeded + dayCounts.failed + dayCounts.partial,
      rate7d: successRate(week),
      finished7d: weekCounts.succeeded + weekCounts.failed + weekCounts.partial
    };
  }, [activeRuns, runs]);

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
    if (runFilter === "failed") return failed24h;
    if (runFilter === "active") return activeRuns;
    return runs;
  }, [activeRuns, failed24h, runFilter, runs]);

  const paginatedRuns = useMemo(() => {
    const start = runsPage * RUNS_PAGE_SIZE;
    return filteredRuns.slice(start, start + RUNS_PAGE_SIZE);
  }, [filteredRuns, runsPage]);
  const longestOnPage = useMemo(
    () => Math.max(0, ...paginatedRuns.map((run) => runDurationMs(run) ?? 0)),
    [paginatedRuns]
  );

  const nightlyRuns = useMemo(() => nightlyBatchRuns(runs), [runs]);
  const noPresets = presets.isSuccess && (presets.data ?? []).length === 0;
  const overdueScans = useMemo(() => {
    const nextScan = scannerStatus.data?.next_scan_at;
    if (!nextScan || scannerStatus.data?.active_run) return 0;
    const due = Date.parse(nextScan);
    return Number.isFinite(due) && due < Date.now() ? 1 : 0;
  }, [scannerStatus.data]);

  const workflowRows = useMemo<WorkflowRow[]>(() => {
    const scannerCron = scannerStatus.data?.config?.schedule_cron ?? null;
    const nightlyRow: WorkflowRow = {
      key: "nightly",
      name: NIGHTLY_WORKFLOW_NAME,
      description: "Fleet Iceberg maintenance",
      schedule: NIGHTLY_SCHEDULE_LABEL,
      scheduleCron: null,
      enabled: true,
      runs: nightlyRuns,
      to: latestNightlyRunPath(nightlyRuns),
      isScanner: false,
      isNightly: true
    };
    const scannerRow: WorkflowRow = {
      key: "scanner",
      name: "Scanner",
      description: "Catalog discovery and health statistics",
      schedule: describeCronSchedule(scannerCron) ?? scannerCron ?? "—",
      scheduleCron: scannerCron,
      enabled: scannerStatus.data?.config?.enabled ?? false,
      runs: (scanRuns.data ?? []).filter(isScanRun),
      to: "/operations/scanner",
      isScanner: true,
      isNightly: false
    };
    const presetRows = (presets.data ?? []).map((preset: PresetRead) => ({
      key: preset.id,
      name: preset.name,
      description: preset.description ?? formatPresetSummary(preset),
      schedule: describeCronSchedule(preset.schedule_cron) ?? preset.schedule_cron ?? "—",
      scheduleCron: preset.schedule_cron ?? null,
      enabled: preset.enabled,
      runs: runsByPreset.get(preset.id) ?? [],
      to: `/operations/presets/${preset.id}`,
      isScanner: false as const,
      isNightly: false as const,
      preset
    }));
    return [nightlyRow, scannerRow, ...presetRows];
  }, [nightlyRuns, presets.data, runsByPreset, scanRuns.data, scannerStatus.data]);

  const presetNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const preset of presets.data ?? []) map.set(preset.id, preset.name);
    return map;
  }, [presets.data]);

  const workflowNameFor = (run: RunRead): { label: string; to: string | null } => {
    if (isScanRun(run)) return { label: "Scanner", to: "/operations/scanner" };
    if (run.preset_id) {
      return {
        label: presetNameById.get(run.preset_id) ?? "Preset",
        to: `/operations/presets/${run.preset_id}`
      };
    }
    if (summaryText(run.summary, "pipeline") === "batch") {
      return { label: NIGHTLY_WORKFLOW_NAME, to: `/runs/${run.id}` };
    }
    return { label: targetLabel(run), to: null };
  };

  const runColumns = useMemo<ColumnDef<RunRead, unknown>[]>(
    () => [
      {
        id: "status",
        header: "State",
        size: 112,
        cell: ({ row }) => <RunStateLabel status={row.original.status} />
      },
      {
        id: "run",
        header: "Run",
        size: 200,
        cell: ({ row }) => {
          const workflow = workflowNameFor(row.original);
          return (
            <div className="ops-run-identity">
              <Link
                to={`/runs/${row.original.id}`}
                className="ops-run-identity__name text-primary hover:underline"
                onClick={(event) => event.stopPropagation()}
              >
                {workflow.label}
              </Link>
              <span className="ops-run-identity__id">{shortRunId(row.original.id)}</span>
            </div>
          );
        }
      },
      {
        id: "started",
        header: "Started",
        size: 132,
        cell: ({ row }) => (
          <span className="ops-started" title={formatTime(row.original.created_at)}>
            <span>{formatRelative(row.original.created_at)}</span>
            <span className="ops-started__abs">{formatTime(row.original.created_at)}</span>
          </span>
        )
      },
      {
        id: "duration",
        header: "Duration",
        size: 150,
        cell: ({ row }) => <DurationCell run={row.original} longest={longestOnPage} />
      },
      { id: "target", header: "Target", cell: ({ row }) => runTargetLabel(row.original) },
      {
        id: "result",
        header: "Result",
        size: 220,
        cell: ({ row }) => <span className="ops-result-line">{runResultLabel(row.original)}</span>
      },
      {
        id: "trigger",
        header: "Trigger",
        size: 100,
        cell: ({ row }) => <Badge variant="outline">{row.original.trigger ?? "manual"}</Badge>
      },
      {
        id: "kind",
        header: "Kind",
        size: 110,
        cell: ({ row }) => <RunKindTag kind={row.original.kind} />
      }
    ],
    [longestOnPage, presetNameById]
  );

  const workflowColumns = useMemo<ColumnDef<WorkflowRow, unknown>[]>(
    () => [
      {
        id: "workflow",
        header: "Workflow",
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5">
              {row.original.to ? (
                <Link
                  to={row.original.to}
                  className="font-semibold text-primary hover:underline"
                  onClick={(e) => e.stopPropagation()}
                >
                  {row.original.name}
                </Link>
              ) : (
                <span className="font-semibold">{row.original.name}</span>
              )}
              {row.original.isScanner || row.original.isNightly ? (
                <Badge variant="secondary">System</Badge>
              ) : row.original.enabled ? null : (
                <Badge variant="outline">Paused</Badge>
              )}
            </div>
            <span className="ops-workflow-desc">{row.original.description}</span>
          </div>
        )
      },
      {
        id: "schedule",
        header: "Schedule",
        size: 168,
        cell: ({ row }) => (
          <div className="ops-schedule-cell">
            <span className="ops-schedule-cell__label">{row.original.schedule}</span>
            {row.original.scheduleCron && row.original.scheduleCron !== row.original.schedule ? (
              <span className="ops-schedule-cell__cron">{row.original.scheduleCron}</span>
            ) : null}
          </div>
        )
      },
      {
        id: "runs",
        header: "Runs",
        size: 150,
        cell: ({ row }) => <RunStateCounts runs={row.original.runs.slice(0, 25)} />
      },
      {
        id: "lastRun",
        header: "Last run",
        size: 120,
        cell: ({ row }) => {
          const latest = row.original.runs[0];
          if (!latest) return <span className="text-muted-foreground">—</span>;
          return (
            <Link
              to={`/runs/${latest.id}`}
              className="ops-started hover:underline"
              title={formatTime(latest.created_at)}
              onClick={(event) => event.stopPropagation()}
            >
              <span>{formatRelative(latest.created_at)}</span>
              <span className="ops-started__abs">{formatDuration(runDurationMs(latest))}</span>
            </Link>
          );
        }
      },
      {
        id: "recent",
        header: "Recent runs",
        size: 150,
        cell: ({ row }) => <RunDurationBars runs={row.original.runs} />
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
        size: 48,
        cell: ({ row }) => <WorkflowMenu row={row.original} />
      }
    ],
    []
  );

  return (
    <div className="operations-home-page">
      <PageHeader
        title="Runs"
        subtitle="What ran, what failed, and what is held?"
        breadcrumbs={false}
        actions={
          <>
            <Button variant="ghost" asChild>
              <Link to="/smart-maintenance">
                <Sparkles className="size-4" aria-hidden />
                Smart maintenance
              </Link>
            </Button>
            {role !== "viewer" ? (
              <Button variant="secondary" asChild>
                <Link to="/operations/scanner">
                  <RefreshCw className="size-4" aria-hidden />
                  Scanner
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

      <span className="sr-only" role="status" aria-live="polite">
        {liveMessage}
      </span>
      <section className="ops-pulse" aria-label="Fleet pulse">
        <PulseTile
          label="Running now"
          tone={activeRuns.length ? "active" : "neutral"}
          selected={tab === "runs" && runFilter === "active"}
          value={allRuns.isError ? "—" : formatNumber(activeRuns.length)}
          context={allRuns.isError ? "Runs unavailable" : pulse.queued ? `${formatNumber(pulse.queued)} waiting in queue` : "Nothing queued"}
          onClick={() => setRunFilter("active")}
        />
        <PulseTile
          label="Failed runs · 24h"
          tone={failed24h.length ? "danger" : "neutral"}
          selected={tab === "runs" && runFilter === "failed"}
          value={allRuns.isError ? "—" : formatNumber(failed24h.length)}
          context={failed24h[0] ? `Latest ${formatRelative(failed24h[0].created_at)}` : "No failures"}
          onClick={() => setRunFilter("failed")}
        />
        <PulseTile
          label="Succeeded · 24h"
          tone={pulse.succeeded24h ? "success" : "neutral"}
          value={allRuns.isError ? "—" : formatNumber(pulse.succeeded24h)}
          context={`of ${formatNumber(pulse.finished24h)} finished`}
          onClick={() => setRunFilter("all")}
        />
        <PulseTile
          label="Success rate · 7d"
          value={allRuns.isError || pulse.rate7d === null ? "—" : formatPercent(pulse.rate7d * 100)}
          context={`${formatNumber(pulse.finished7d)} finished runs`}
          onClick={() => setRunFilter("all")}
        />
        <PulseTile
          label="Scanner"
          to="/operations/scanner"
          tone={overdueScans ? "danger" : scannerStatus.data?.active_run ? "active" : "neutral"}
          value={
            scannerStatus.isError
              ? "Unavailable"
              : scannerStatus.data?.active_run
                ? "Running"
                : overdueScans
                  ? "Overdue"
                  : "Idle"
          }
          context={`${formatNumber(scannerStatus.data?.tables_with_stats)} / ${formatNumber(scannerStatus.data?.total_tables)} tables with stats · queue ${formatNumber(scannerStatus.data?.queue_depth ?? null)}`}
        />
      </section>

      <div className="flex flex-col gap-3">

        <section className="ops-workspace-card" aria-label="Run operations workspace">
          <Tabs
            value={tab}
            onValueChange={(key) =>
              setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set("tab", key === "runs" ? "history" : key);
                return next;
              })
            }
          >
            <TabsList className="ops-workspace-tabs">
              <TabsTrigger value="workflows">Workflows</TabsTrigger>
              <TabsTrigger value="runs">History</TabsTrigger>
              <TabsTrigger value="actions">Actions</TabsTrigger>
              <TabsTrigger value="holds">
                Holds
                {pendingApprovals.isSuccess && pendingApprovals.data.length > 0 ? (
                  <span className="ops-hold-count">{pendingApprovals.data.length}</span>
                ) : null}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="workflows">
            <div className="space-y-2">
              {noPresets ? <p className="text-sm text-muted-foreground">{NO_PRESETS_COPY}</p> : null}
              <DataTable
                columns={workflowColumns}
                data={workflowRows}
                density="compact"
                getRowId={(row) => row.key}
                emptyTitle="No workflows"
                emptyDescription="Scanner and maintenance presets appear here."
              />
            </div>
            </TabsContent>

            <TabsContent value="runs">
            <div className="flex flex-col gap-3">
              <div className="ops-filter-row" role="group" aria-label="Filter runs">
                <Button
                  size="sm"
                  variant={runFilter === "all" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("all")}
                >
                  All
                  <FilterCount value={runs.length} />
                </Button>
                <Button
                  size="sm"
                  variant={runFilter === "active" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("active")}
                >
                  Active
                  <FilterCount value={activeRuns.length} />
                </Button>
                <Button
                  size="sm"
                  variant={runFilter === "failed" ? "primary" : "secondary"}
                  onClick={() => setRunFilter("failed")}
                >
                  Failed · 24h
                  <FilterCount value={failed24h.length} />
                </Button>
              </div>
              {allRuns.isError ? null : <RunTimeline runs={filteredRuns} />}
              {allRuns.isError ? (
                <QueryErrorAlert error={allRuns.error} title="Failed to load runs" />
              ) : (
              <DataTable
                columns={runColumns}
                data={paginatedRuns}
                density="compact"
                loading={allRuns.isLoading}
                columnVisibility={HISTORY_COLUMN_VISIBILITY}
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
              )}
            </div>
            </TabsContent>

            <TabsContent value="actions">
              <ActionsTab />
            </TabsContent>
            <TabsContent value="holds">
              <Suspense fallback={<p className="text-sm text-muted-foreground">Loading holds…</p>}>
                <ControlRoom />
              </Suspense>
            </TabsContent>
          </Tabs>
        </section>

        <div className="ops-context-grid">
          <EngineLoadStrip />
          <DecisionFunnelStrip />
        </div>
      </div>
    </div>
  );
};
