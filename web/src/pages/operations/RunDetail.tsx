import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock3, Info, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Link, useNavigate, useParams } from "react-router-dom";
import { cancelRun, diagnoseRunFailure, forceAdmitJob, getRun, getRunJobs, listRuns } from "../../api/client";
import type { JobRead, RunRead } from "../../api/types";
import { PageHeader, StatStrip } from "../../components/common";
import { ConfirmAction } from "../../components/confirm";
import { ConsoleShell } from "../../components/operations/ConsoleShell";
import {
  OperationPipeline,
  maintenanceOpsToSteps,
  scanPipelineSteps
} from "../../components/operations/OperationPipeline";
import { RunDurationBar } from "../../components/operations/RunDurationBar";
import { RunHistoryGrid } from "../../components/operations/RunHistoryGrid";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle
} from "../../components/ui/sheet";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { MetricGrid, MetricTile } from "../../components/ui/metric-grid";
import { RunLanes } from "../../components/charts";
import { StatusTag, RunKindTag } from "../../components/status";
import { useRunLiveEvents, isTerminalRunStatus } from "../../hooks/useRunLiveEvents";
import { useShellOptional } from "../../components/shell/ShellContext";
import { useAuth } from "../../lib/auth";
import { SCAN_ONLY_REASON, VIEWER_REASON } from "../../lib/permissions";
import { useWriteControl } from "../../lib/useWriteControl";
import {
  OVER_BUDGET_TOOLTIP,
  batchCounts,
  budgetLine,
  failureRows,
  filesTransition,
  isBatchPipeline,
  laneRows,
  rewrittenBytes,
  sizeTransition,
  type FailureRow,
  type LaneRow
} from "../../lib/batchSummary";
import { cn } from "../../lib/cn";
import { formatEngineLabel } from "../../lib/engines";
import { formatNumber, formatTime } from "../../lib/format";
import {
  formatDuration,
  isScanRun,
  runDurationMs,
  shortRunId,
  summaryNumber,
  summaryText,
  realizedSavingsLabel,
  targetLabel,
  targetTableId
} from "../../lib/operations";
import "./runDetail.css";

const STUCK_STATUSES = new Set(["queued", "deferred"]);
const STALL_THRESHOLD_MS = 3 * 60 * 1000;
const ACTIVE_RUN_STATUSES = new Set(["accepted", "queued", "running", "admitted"]);
// Mirrors the cancellable job statuses in cryo/api/routes.py::cancel_run.
const CANCELLABLE_JOB_STATUSES = new Set(["queued", "admitted", "running", "deferred"]);
const JOB_PAGE_SIZE = 50;
const JOB_CHIP_STATUSES = ["failed", "succeeded", "skipped", "deferred"] as const;
const CHIP_STATUS_SET = new Set<string>(JOB_CHIP_STATUSES);
const IN_PROGRESS_JOB_STATUSES = new Set(["running", "admitted", "queued", "deferred"]);

const tableRef = (job: JobRead) => {
  const planRef = job.plan?.table_ref;
  if (typeof planRef === "string" && planRef) return planRef;
  return job.schema_name && job.table_name ? `${job.schema_name}.${job.table_name}` : job.table_id;
};

const planOperationNames = (plan: JobRead["plan"]): string[] => {
  if (!plan) return [];
  const names: string[] = [];
  const ops = plan.operations;
  if (Array.isArray(ops)) {
    for (const op of ops) {
      if (typeof op === "string" && op) names.push(op);
      else if (op && typeof op === "object" && "type" in op) {
        const type = (op as { type?: unknown }).type;
        if (typeof type === "string" && type) names.push(type);
      }
    }
  }
  const single = plan.operation;
  if (typeof single === "string" && single) names.push(single);
  return names;
};

const jobHasOperation = (job: JobRead, op: string) => planOperationNames(job.plan).includes(op);

const inferPipelineFromJobs = (jobs: JobRead[], operations: string[]) => {
  const completed: string[] = [];
  const partial: string[] = [];
  const skipped: string[] = [];
  let active: string | null = null;
  let failed: string | null = null;
  for (const op of operations) {
    const opJobs = jobs.filter((job) => jobHasOperation(job, op));
    if (opJobs.some((job) => job.status === "failed")) {
      failed = op;
      break;
    }
    if (opJobs.some((job) => IN_PROGRESS_JOB_STATUSES.has(job.status))) {
      active = op;
      break;
    }
    if (opJobs.length === 0) continue;
    if (opJobs.every((job) => job.status === "skipped")) {
      skipped.push(op);
      continue;
    }
    const settled = opJobs.every(
      (job) => job.status === "succeeded" || job.status === "partial" || job.status === "skipped"
    );
    if (!settled) continue;
    if (opJobs.some((job) => job.status === "partial")) partial.push(op);
    else if (opJobs.some((job) => job.status === "succeeded")) completed.push(op);
  }
  return { completed, partial, skipped, active, failed };
};

const chipLabel = (status: string) => status.charAt(0).toUpperCase() + status.slice(1);

const jobStatusChips = (jobs: JobRead[]): Array<{ status: string; count: number }> => {
  const counts = new Map<string, number>();
  for (const job of jobs) {
    const status = job.status.toLowerCase();
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  const named = JOB_CHIP_STATUSES.map((status) => ({ status, count: counts.get(status) ?? 0 }));
  const extras = [...counts.keys()]
    .filter((status) => !CHIP_STATUS_SET.has(status))
    .sort()
    .map((status) => ({ status, count: counts.get(status) ?? 0 }));
  return [...named, ...extras];
};

const failedFirst = (jobs: JobRead[]): JobRead[] => {
  const failed: JobRead[] = [];
  const rest: JobRead[] = [];
  for (const job of jobs) {
    if (job.status.toLowerCase() === "failed" || job.status.toLowerCase() === "partial") failed.push(job);
    else rest.push(job);
  }
  return failed.length === 0 ? jobs : [...failed, ...rest];
};

const matchesTableQuery = (job: JobRead, query: string) => {
  if (!query) return true;
  return `${tableRef(job)} ${job.table_name ?? ""} ${job.schema_name ?? ""}`.toLowerCase().includes(query);
};

const DescriptionGrid = ({
  items
}: {
  items: Array<{ label: string; value: React.ReactNode }>;
}) => (
  <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
    {items.map((item) => (
      <div key={item.label}>
        <dt className="text-xs text-muted-foreground">{item.label}</dt>
        <dd className="mt-0.5 font-medium">{item.value}</dd>
      </div>
    ))}
  </dl>
);

const RunOutcomeSummary = ({
  run,
  jobs,
  summary,
  duration,
  savingsLabel,
  possiblyStalled,
  scan
}: {
  run?: RunRead;
  jobs: JobRead[];
  summary: RunRead["summary"];
  duration: string;
  savingsLabel: string | null;
  possiblyStalled: boolean;
  scan: boolean;
}) => {
  const status = run?.status?.toLowerCase() ?? "unknown";
  const counts = jobs.reduce(
    (acc, job) => {
      const key = job.status.toLowerCase();
      acc.total += 1;
      if (key === "succeeded" || key === "skipped") acc.completed += 1;
      if (key === "failed" || key === "partial") acc.attention += 1;
      if (IN_PROGRESS_JOB_STATUSES.has(key)) acc.pending += 1;
      return acc;
    },
    { total: 0, completed: 0, attention: 0, pending: 0 }
  );
  const outcome =
    status === "succeeded"
      ? { label: "Completed", icon: CheckCircle2, tone: "success", copy: "The recorded work finished successfully." }
      : status === "failed" || status === "partial" || status === "partial_failed"
        ? { label: "Needs attention", icon: AlertTriangle, tone: "danger", copy: "Open the failed jobs below to see the exact table and error." }
        : status === "cancelled"
          ? { label: "Cancelled", icon: AlertTriangle, tone: "warning", copy: "This run stopped before all queued work completed." }
          : status === "running" || status === "admitted"
            ? { label: "In progress", icon: LoaderCircle, tone: "primary", copy: "Work is still moving through the pipeline; live updates appear below." }
            : status === "queued" || status === "accepted" || status === "deferred"
              ? { label: "Waiting to run", icon: Clock3, tone: "warning", copy: "The run is recorded and waiting for admission or worker capacity." }
              : { label: "Status unavailable", icon: Info, tone: "muted", copy: "The run exists, but its outcome has not been recorded yet." };
  const OutcomeIcon = outcome.icon;
  const scanRecords = scan && typeof summary?.discovered === "number" ? summary.discovered : counts.total;
  const scanCompleted = scan
    ? (typeof summary?.scanned === "number" ? summary.scanned : 0) +
      (typeof summary?.skipped === "number" ? summary.skipped : 0)
    : counts.completed;
  const scanAttention = scan && typeof summary?.failed === "number" ? summary.failed : counts.attention;
  const scanPending = scan ? Math.max(0, scanRecords - scanCompleted - scanAttention) : counts.pending;
  const evidenceValue = savingsLabel
    ?.replace(/^Freed\s+/i, "")
    .replace(/\s+\(.+\)$/, "");

  return (
    <section className="run-outcome-card" aria-labelledby="run-outcome-title">
      <div className="run-outcome-card__header">
        <div>
          <p className="run-outcome-card__eyebrow">{scan ? "Scan outcome" : "Maintenance outcome"}</p>
          <h2 id="run-outcome-title">{outcome.label}</h2>
          <p>{outcome.copy}</p>
        </div>
        <span className={`run-outcome-card__icon run-outcome-card__icon--${outcome.tone}`} aria-hidden>
          <OutcomeIcon className={status === "running" || status === "admitted" ? "animate-spin" : undefined} />
        </span>
      </div>
      <dl className="run-outcome-card__metrics">
        <div><dt>{scan ? "Records" : "Jobs"}</dt><dd>{formatNumber(scanRecords)}</dd></div>
        <div><dt>Completed</dt><dd>{formatNumber(scanCompleted)}</dd></div>
        <div><dt>Attention · pending</dt><dd>{formatNumber(scanAttention)} · {formatNumber(scanPending)}</dd></div>
        <div><dt>Duration</dt><dd>{duration}</dd></div>
        {evidenceValue ? <div><dt>Storage freed</dt><dd>{evidenceValue}</dd></div> : null}
      </dl>
      {possiblyStalled ? (
        <div className="run-outcome-card__notice" role="status">
          <AlertTriangle className="size-4" aria-hidden />
          <span>No live update for more than three minutes. Check the live log or cancel the run if work is stuck.</span>
        </div>
      ) : null}
    </section>
  );
};

const laneColumns: ColumnDef<LaneRow, unknown>[] = [
  { id: "lane", header: "Lane", cell: ({ row }) => row.original.lane },
  { id: "vcpu", header: "vCPU", cell: ({ row }) => row.original.vcpu },
  { id: "memory", header: "Memory", cell: ({ row }) => row.original.memory },
  { id: "tables", header: "Tables", cell: ({ row }) => row.original.tables }
];

const failureColumns: ColumnDef<FailureRow, unknown>[] = [
  {
    id: "table",
    header: "Table",
    cell: ({ row }) => <span className="break-all">{row.original.table}</span>
  },
  {
    id: "error",
    header: "Error",
    cell: ({ row }) => {
      const error = row.original.error;
      const preview = error.length > 96 ? `${error.slice(0, 96)}…` : error;
      return (
        <details className="min-w-0">
          <summary className="cursor-pointer break-all font-mono text-xs">{preview}</summary>
          <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">{error}</pre>
        </details>
      );
    }
  }
];

const BatchRunSummary = ({ summary }: { summary: NonNullable<RunRead["summary"]> }) => {
  const counts = batchCounts(summary);
  const lanes = laneRows(summary);
  const failures = failureRows(summary);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>Batch summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <section aria-label="Batch summary" className="space-y-3">
          <MetricGrid columns={5}>
            <MetricTile label="Selected" value={counts.selected} />
            <MetricTile label="Succeeded" value={counts.succeeded} />
            <MetricTile label="Failed" value={counts.failed} />
            <MetricTile label="Skipped" value={counts.skipped} />
            <MetricTile
              label="Over budget"
              value={counts.overBudget}
              hint={
                <span
                  title={OVER_BUDGET_TOOLTIP}
                  aria-label={OVER_BUDGET_TOOLTIP}
                  className="inline-flex cursor-help text-muted-foreground"
                >
                  <Info className="size-3.5" aria-hidden />
                </span>
              }
            />
          </MetricGrid>
          <MetricGrid columns={3}>
            <MetricTile label="Files" value={filesTransition(summary)} />
            <MetricTile label="Size" value={sizeTransition(summary)} />
            <MetricTile label="Rewritten" value={rewrittenBytes(summary)} />
          </MetricGrid>
          <div className="space-y-1">
            <h3 className="text-sm font-medium">Lanes</h3>
            {lanes == null ? (
              <p className="text-sm text-muted-foreground">Unknown</p>
            ) : (
              <DataTable
                columns={laneColumns}
                data={lanes}
                density="compact"
                getRowId={(row) => row.id}
                emptyTitle="No lanes"
                emptyDescription="This run recorded no lanes."
                tableClassName="table-fixed"
              />
            )}
          </div>
          <p className="text-sm">
            Budget <span>{budgetLine(summary)}</span>
          </p>
          <div className="space-y-1">
            <h3 className="text-sm font-medium">Failures</h3>
            {failures == null ? (
              <p className="text-sm text-muted-foreground">Unknown</p>
            ) : (
              <DataTable
                columns={failureColumns}
                data={failures}
                density="compact"
                getRowId={(row) => row.id}
                emptyTitle="No failures"
                emptyDescription="This run recorded no table failures."
                tableClassName="table-fixed"
              />
            )}
          </div>
        </section>
      </CardContent>
    </Card>
  );
};

export const RunDetail = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const cancelAccess = useWriteControl("cancelRun");
  const shell = useShellOptional();
  const [logJob, setLogJob] = useState<JobRead | null>(null);
  const [tableQuery, setTableQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [jobPage, setJobPage] = useState(0);

  const { messages, snapshot, wsOpen, wsExhausted, runTerminal, reconnectAttempt, manualReconnect } =
    useRunLiveEvents(runId ?? "", token);

  const run = useQuery({
    queryKey: ["run", runId, token],
    queryFn: () => getRun(api, runId!),
    enabled: Boolean(runId),
    refetchInterval: wsOpen ? false : 5000
  });
  const jobs = useQuery({
    queryKey: ["run-jobs", runId, token],
    queryFn: () => getRunJobs(api, runId!),
    enabled: Boolean(runId),
    retry: 1,
    refetchInterval: wsOpen ? false : 5000
  });

  const runData = snapshot?.run ?? run.data;
  const scan = isScanRun(runData);
  const workflowRuns = useQuery({
    queryKey: ["workflow-runs", runData?.preset_id, scan, token],
    queryFn: () =>
      scan
        ? listRuns(api, { kind: "scan", limit: 25 })
        : runData?.preset_id
          ? listRuns(api, { limit: 25 }).then((items) => items.filter((item) => item.preset_id === runData.preset_id))
          : listRuns(api, { limit: 25 }),
    enabled: Boolean(runData)
  });

  const cancel = useMutation({
    mutationFn: (_runAlreadyFinished: boolean) => cancelRun(api, runId!),
    onSuccess: (_run, runAlreadyFinished) => {
      toast.success(runAlreadyFinished ? "Remaining jobs cancelled" : "Run cancelled");
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["run-jobs", runId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const diagnose = useMutation({
    mutationFn: () => diagnoseRunFailure(api, runId!),
    onSuccess: (result) =>
      toast.info(
        `${result.diagnosis_class.replaceAll("_", " ")} (${Math.round(result.confidence * 100)}%): ${result.summary ?? "No summary"}`,
        { duration: 6000 }
      ),
    onError: (error) => toast.error((error as Error).message)
  });
  const forceAdmit = useMutation({
    mutationFn: (jobId: string) => forceAdmitJob(api, jobId),
    onSuccess: () => {
      toast.success("Scheduling gates bypassed. The job still waits for a free slot.");
      void queryClient.invalidateQueries({ queryKey: ["run-jobs", runId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const jobRows = useMemo(() => snapshot?.jobs ?? jobs.data ?? [], [snapshot?.jobs, jobs.data]);
  const runFinished = runTerminal || isTerminalRunStatus(runData?.status);
  const statusChips = useMemo(() => jobStatusChips(jobRows), [jobRows]);
  const filteredJobs = useMemo(() => {
    const query = tableQuery.trim().toLowerCase();
    const matched = jobRows.filter((job) => {
      if (statusFilter && job.status.toLowerCase() !== statusFilter) return false;
      return matchesTableQuery(job, query);
    });
    return failedFirst(matched);
  }, [jobRows, statusFilter, tableQuery]);
  const jobPageCount = Math.max(1, Math.ceil(filteredJobs.length / JOB_PAGE_SIZE));
  const safeJobPage = Math.min(jobPage, jobPageCount - 1);
  const pageJobs = filteredJobs.slice(safeJobPage * JOB_PAGE_SIZE, (safeJobPage + 1) * JOB_PAGE_SIZE);
  useEffect(() => {
    setTableQuery("");
    setStatusFilter(null);
    setJobPage(0);
  }, [runId]);
  const cancellableJobs = useMemo(
    () => jobRows.filter((job) => CANCELLABLE_JOB_STATUSES.has(job.status)).length,
    [jobRows]
  );
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (runFinished) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [runFinished]);
  const duration = formatDuration(runDurationMs(runData));
  const workflowName = scan ? "Scanner" : targetLabel(runData ?? { id: "", kind: "", status: "", created_at: "" });
  const workflowLink = scan ? "/runs/scanner" : runData?.preset_id ? `/runs/workflows/${runData.preset_id}` : "/runs";

  const pipelineSteps = useMemo(() => {
    if (scan) return scanPipelineSteps(runData?.status);
    const ops = ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"];
    const { completed, active, failed, partial, skipped } = inferPipelineFromJobs(jobRows, ops);
    return maintenanceOpsToSteps(ops, active, completed, failed, {
      absentLabel: runFinished ? "Not recorded" : "Waiting",
      partialOps: partial,
      skippedOps: skipped
    });
  }, [jobRows, runData?.status, runFinished, scan]);

  const jobLogMessages = useMemo(() => {
    if (!logJob) return [];
    return messages.filter((item) => item.job_id === logJob.id);
  }, [logJob, messages]);

  const summary = runData?.summary;
  const scanPercent = useMemo(() => {
    if (!scan) return 0;
    const assigned = summaryNumber(summary, "assigned");
    const discovered = summaryNumber(summary, "discovered");
    const completed =
      summaryNumber(summary, "scanned") + summaryNumber(summary, "skipped") + summaryNumber(summary, "failed");
    const denominator = Math.max(assigned, discovered);
    return denominator > 0 ? Math.min(100, Math.round((completed / denominator) * 100)) : 0;
  }, [scan, summary]);

  const lastActivityAt = useMemo(() => {
    const latestMessageTs = messages[0]?.ts ? Date.parse(messages[0].ts) : NaN;
    const runUpdatedTs = runData?.created_at ? Date.parse(runData.created_at) : NaN;
    const candidates = [latestMessageTs, runUpdatedTs].filter((ts) => Number.isFinite(ts));
    return candidates.length ? Math.max(...candidates) : null;
  }, [messages, runData?.created_at]);

  const isActive = ACTIVE_RUN_STATUSES.has((runData?.status ?? "").toLowerCase()) && !runFinished;
  const msSinceActivity = lastActivityAt != null ? now - lastActivityAt : null;
  const possiblyStalled = isActive && msSinceActivity != null && msSinceActivity > STALL_THRESHOLD_MS;

  // Restrained live region: announce status transitions and scan-progress milestones only, never
  // every log line or poll tick (per audit R11/R16/R21).
  const [announcement, setAnnouncement] = useState("");
  const prevStatusRef = useRef<string | undefined>(undefined);
  const prevMilestoneRef = useRef(0);
  useEffect(() => {
    const status = (runData?.status ?? "").toLowerCase();
    const prev = prevStatusRef.current;
    if (status && status !== prev) {
      if (prev !== undefined) {
        if (status === "running" || status === "admitted") setAnnouncement("Run is now running.");
        else if (status === "succeeded") setAnnouncement("Run succeeded.");
        else if (status === "failed" || status === "partial" || status === "partial_failed") {
          setAnnouncement("Run needs attention.");
        }
        else if (status === "cancelled") setAnnouncement("Run cancelled.");
      }
      prevStatusRef.current = status;
    }
  }, [runData?.status]);
  useEffect(() => {
    if (!scan) return;
    const milestones = [25, 50, 75, 100];
    const crossed = milestones.filter((m) => scanPercent >= m && prevMilestoneRef.current < m);
    if (crossed.length) {
      prevMilestoneRef.current = Math.max(...crossed);
      setAnnouncement(`Scan ${Math.max(...crossed)}% complete.`);
    }
  }, [scan, scanPercent]);

  const jobColumns = useMemo<ColumnDef<JobRead, unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        cell: ({ row }) => (
          <Link
            to={`/tables/${row.original.table_id}`}
            className="identity-stack break-all font-semibold hover:underline"
          >
            {tableRef(row.original)}
          </Link>
        )
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <StatusTag status={row.original.status} />
      },
      {
        id: "timeline",
        header: "Timeline",
        cell: ({ row }) => (
          <RunDurationBar
            startedAt={row.original.started_at}
            finishedAt={row.original.finished_at}
            runStartedAt={runData?.created_at}
            runFinishedAt={runData?.finished_at}
            status={row.original.status}
          />
        )
      },
      {
        id: "engine",
        header: "Engine",
        cell: ({ row }) => (
          <span className="break-words">
            {formatEngineLabel(row.original.destination_engine) || row.original.destination_name || "—"}
          </span>
        )
      },
      {
        id: "started",
        header: "Started",
        cell: ({ row }) => <span className="whitespace-normal">{formatTime(row.original.started_at)}</span>
      },
      {
        id: "actions",
        header: "Detail",
        cell: ({ row }) => (
          <div className="flex min-w-0 w-full flex-wrap items-center gap-1">
            {STUCK_STATUSES.has(row.original.status) && !row.original.force_admit ? (
              <ConfirmAction
                title="Bypass scheduling gates?"
                description="Skips cooldown, off-peak, benefit score, and this run's admission cutoff. A free engine slot is still required. Table locks and safety checks still apply."
                onConfirm={() => forceAdmit.mutate(row.original.id)}
                disabled={!cancelAccess.allowed}
              >
                <Button size="sm" variant="secondary" loading={forceAdmit.isPending} disabled={!cancelAccess.allowed} title={cancelAccess.reason ?? undefined}>
                  Bypass gates
                </Button>
              </ConfirmAction>
            ) : null}
            <Button size="sm" variant="secondary" onClick={() => setLogJob(row.original)}>
              Logs
            </Button>
            <Button size="sm" variant="ghost" asChild>
              <Link to={`/jobs/${row.original.id}`}>Detail</Link>
            </Button>
          </div>
        )
      }
    ],
    [forceAdmit, runData?.created_at, runData?.finished_at]
  );

  if (!runId) {
    return <Alert variant="error" title="Missing run id" />;
  }

  const savingsLabel = realizedSavingsLabel(runData);

  return (
    <div className="run-detail-page">
      <div aria-live="polite" className="sr-only">
        {announcement}
      </div>
      <PageHeader
        title={`Run ${shortRunId(runId)}`}
        subtitle={
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <StatusTag status={runData?.status} />
            <RunKindTag kind={runData?.kind} />
            <span>{targetLabel(runData ?? { id: runId, kind: "", status: "", created_at: "" })}</span>
            <span>Duration {duration}</span>
            <span>Started {formatTime(runData?.created_at)}</span>
            {savingsLabel ? <span>{savingsLabel}</span> : null}
            {possiblyStalled ? (
              <span
                className="rounded px-1.5 py-0.5 text-xs font-medium text-[var(--color-status-warning)]"
                title="No live update or log line for over 3 minutes while the run is active."
              >
                No update in {formatDuration(msSinceActivity)} — may be stalled
              </span>
            ) : null}
          </div>
        }
        rootCrumb={{ label: "Runs", to: "/runs" }}
        breadcrumbs={
          <nav aria-label="Breadcrumb" style={{ marginBottom: 12 }}>
            <ol className="flex list-none flex-wrap gap-2 p-0 m-0">
              <li>
                <Link to="/runs">Runs</Link>
              </li>
              <li aria-hidden>›</li>
              <li>
                <Link to={workflowLink}>{workflowName}</Link>
              </li>
              <li aria-hidden>›</li>
              <li aria-current="page">Run {shortRunId(runId)}</li>
            </ol>
          </nav>
        }
        actions={
          <div className="flex items-center gap-2">
            {runData?.status === "failed" ||
            runData?.status === "partial" ||
            runData?.status === "partial_failed" ? (
              <Button
                variant="secondary"
                onClick={() => {
                  if (!runId) return;
                  shell?.openAssistant("What failed in this run?", { run_id: runId });
                }}
              >
                Ask a follow-up
              </Button>
            ) : null}
            {runData?.status === "failed" || runData?.status === "partial" ? (
              <Button variant="secondary" loading={diagnose.isPending} onClick={() => diagnose.mutate()}>
                Diagnose
              </Button>
            ) : null}
            {runData && !runFinished ? (
              <ConfirmAction
                title="Cancel this run?"
                description="Marks this run cancelled and skips jobs not yet claimed. A Trino or Spark statement already running is not stopped. A native job is asked to stop."
                onConfirm={() => cancel.mutate(false)}
                disabled={!cancelAccess.allowed}
              >
                <Button
                  variant="danger"
                  loading={cancel.isPending}
                  disabled={!cancelAccess.allowed}
                  title={cancelAccess.reason ?? undefined}
                >
                  Cancel
                </Button>
              </ConfirmAction>
            ) : cancellableJobs > 0 ? (
              <ConfirmAction
                title="Cancel the remaining jobs?"
                description={`This run keeps its recorded outcome. ${formatNumber(cancellableJobs)} ${cancellableJobs === 1 ? "job" : "jobs"} still queued, admitted, deferred, or running will be marked skipped. An engine statement already running may still finish.`}
                onConfirm={() => cancel.mutate(true)}
                disabled={!cancelAccess.allowed}
              >
                <Button
                  variant="danger"
                  loading={cancel.isPending}
                  disabled={!cancelAccess.allowed}
                  title={cancelAccess.reason ?? undefined}
                >
                  Cancel remaining jobs ({formatNumber(cancellableJobs)})
                </Button>
              </ConfirmAction>
            ) : null}
            {role === "viewer" ? <span className="text-xs text-muted-foreground">{VIEWER_REASON}</span> : null}
            {cancelAccess.reason === SCAN_ONLY_REASON ? (
              <span className="text-xs text-muted-foreground">{SCAN_ONLY_REASON}</span>
            ) : null}
            <Button variant="secondary" onClick={() => navigate("/runs?tab=history")}>
              All runs
            </Button>
          </div>
        }
      />

      <RunOutcomeSummary
        run={runData}
        jobs={jobRows}
        summary={summary}
        duration={duration}
        savingsLabel={savingsLabel}
        possiblyStalled={possiblyStalled}
        scan={scan}
      />

      <div className="run-detail-main-grid grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex flex-col gap-3">
          <Card className="run-pipeline-card">
            <CardContent className="pt-4">
              <OperationPipeline steps={pipelineSteps} title={scan ? "Scan pipeline" : "Maintenance pipeline"} />
            </CardContent>
          </Card>

          {isBatchPipeline(summary) ? <BatchRunSummary summary={summary} /> : null}

          {scan ? (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Scan progress</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Progress</span>
                    <span>{scanPercent}%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
                    <div
                      className={`h-full rounded-full transition-all ${
                        runData?.status === "failed" || runData?.status === "partial" || runData?.status === "partial_failed"
                          ? "bg-[var(--color-status-danger)]"
                          : "bg-primary"
                      }`}
                      style={{ width: `${scanPercent}%` }}
                    />
                  </div>
                </div>
                <StatStrip
                  items={[
                    { label: "Discovered", value: formatNumber(summaryNumber(summary, "discovered")) },
                    { label: "Scanned", value: formatNumber(summaryNumber(summary, "scanned")) },
                    { label: "Skipped", value: formatNumber(summaryNumber(summary, "skipped")) },
                    { label: "Failed", value: formatNumber(summaryNumber(summary, "failed")) }
                  ]}
                />
                <DescriptionGrid
                  items={[
                    { label: "Scope", value: summary?.scope?.toString() ?? "—" },
                    { label: "Trigger", value: runData?.trigger ?? "—" },
                    { label: "Current table", value: summaryText(summary, "current_table") ?? "—" },
                    { label: "Finished", value: formatTime(runData?.finished_at) }
                  ]}
                />
              </CardContent>
            </Card>
          ) : null}

          <Card className="run-jobs-card">
            <CardHeader className="pb-2">
              <CardTitle>Jobs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 p-0 pb-2">
              <div className="flex flex-col gap-2 px-3 pt-3">
                <div role="group" aria-label="Job status" className="flex flex-wrap gap-2">
                  {statusChips.map((chip) => {
                    const selected = statusFilter === chip.status;
                    return (
                      <button
                        key={chip.status}
                        type="button"
                        data-testid="job-status-chip"
                        data-count={chip.count}
                        aria-pressed={selected}
                        onClick={() => {
                          setStatusFilter((current) => (current === chip.status ? null : chip.status));
                          setJobPage(0);
                        }}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2 py-1 text-xs font-medium",
                          chip.status === "failed"
                            ? "border-transparent bg-[color-mix(in_srgb,var(--color-status-danger)_14%,transparent)] text-[var(--color-status-danger)]"
                            : "border-border bg-[var(--color-surface-sunken)] text-foreground",
                          selected && "ring-2 ring-ring"
                        )}
                      >
                        <span>{chipLabel(chip.status)}</span>
                        <span className="tabular-nums">{formatNumber(chip.count)}</span>
                      </button>
                    );
                  })}
                </div>
                <Input
                  aria-label="Filter by table name"
                  placeholder="Filter by table name"
                  value={tableQuery}
                  onChange={(event) => {
                    setTableQuery(event.target.value);
                    setJobPage(0);
                  }}
                  className="h-8 max-w-xs"
                />
              </div>
              <RunLanes
                jobs={pageJobs.map((job) => ({
                  id: job.id,
                  table: tableRef(job),
                  engine: job.destination_engine || job.destination_name || "Unknown",
                  status: job.status,
                  startedAt: job.started_at ?? null,
                  finishedAt: job.finished_at ?? null
                }))}
              />
              <DataTable
                columns={jobColumns}
                data={pageJobs}
                density="compact"
                loading={jobs.isLoading && !snapshot?.jobs}
                getRowId={(row) => row.id}
                className="border-0"
                tableClassName="table-fixed"
                emptyTitle={tableQuery.trim() || statusFilter ? "No matching jobs" : "No rows to show"}
                emptyDescription={
                  tableQuery.trim() || statusFilter
                    ? "No jobs match this filter."
                    : "This run has no jobs yet."
                }
                pagination={{
                  pageIndex: safeJobPage,
                  pageSize: JOB_PAGE_SIZE,
                  totalRows: filteredJobs.length,
                  onPageChange: setJobPage
                }}
              />
            </CardContent>
          </Card>

          <div className="run-console-card">
            <ConsoleShell
            title="Live log"
            items={[...messages].reverse()}
            wsOpen={wsOpen}
            wsExhausted={wsExhausted}
            runTerminal={runFinished}
            reconnectAttempt={reconnectAttempt}
            onManualReconnect={manualReconnect}
            formatTime={formatTime}
            downloadName={`run-${shortRunId(runId)}-log`}
            />
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <RunHistoryGrid runs={workflowRuns.data ?? []} loading={workflowRuns.isLoading} />
          {targetTableId(runData ?? { id: runId, kind: "", status: "", created_at: "" }) ? (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Target table</CardTitle>
              </CardHeader>
              <CardContent>
                <Link to={`/tables/${targetTableId(runData!)}`} className="text-sm text-primary hover:underline">
                  Open table detail
                </Link>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      <Sheet open={Boolean(logJob)} onOpenChange={(open) => !open && setLogJob(null)}>
        <SheetContent side="right" className="w-full sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{logJob ? `Job ${shortRunId(logJob.id)}` : "Job logs"}</SheetTitle>
            {logJob ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <StatusTag status={logJob.status} />
                <Button variant="secondary" size="sm" asChild>
                  <Link to={`/jobs/${logJob.id}`}>Full job detail</Link>
                </Button>
              </div>
            ) : null}
          </SheetHeader>
          {logJob ? (
            <div className="mt-4 space-y-3">
              {logJob.error ? <p className="text-sm text-destructive">{logJob.error}</p> : null}
              {jobLogMessages.length === 0 && !logJob.error ? (
                <p className="text-sm text-muted-foreground">No live events for this job yet.</p>
              ) : (
                <ul className="log-list-ul">
                  {jobLogMessages.map((item, idx) => (
                    <li key={idx} className="log-list-item">
                      <code className="text-xs">{formatTime(item.ts)}</code>
                      <span className={item.level === "error" ? "text-destructive" : undefined}>
                        {item.message ?? item.status ?? item.event}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
};
