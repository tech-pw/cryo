import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Link, useNavigate, useParams } from "react-router-dom";
import { cancelRun, diagnoseRunFailure, forceAdmitJob, getRun, getRunJobs, listRuns } from "../../api/client";
import type { JobRead } from "../../api/types";
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
import { StatusTag, RunKindTag } from "../../components/status";
import { useRunLiveEvents, isTerminalRunStatus } from "../../hooks/useRunLiveEvents";
import { useAuth } from "../../lib/auth";
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

const STUCK_STATUSES = new Set(["queued", "deferred"]);
const STALL_THRESHOLD_MS = 3 * 60 * 1000;
const ACTIVE_RUN_STATUSES = new Set(["accepted", "queued", "running", "admitted"]);

const tableRef = (job: JobRead) => {
  const planRef = job.plan?.table_ref;
  if (typeof planRef === "string" && planRef) return planRef;
  return job.schema_name && job.table_name ? `${job.schema_name}.${job.table_name}` : job.table_id;
};

const inferPipelineFromJobs = (jobs: JobRead[], operations: string[]) => {
  const completed: string[] = [];
  let active: string | null = null;
  let failed: string | null = null;
  for (const op of operations) {
    const opJobs = jobs.filter((job) => {
      const planOps = job.plan?.operations;
      if (Array.isArray(planOps)) return planOps.includes(op);
      const opName = job.plan?.operation;
      return opName === op;
    });
    if (opJobs.some((j) => j.status === "failed")) {
      failed = op;
      break;
    }
    if (opJobs.some((j) => ["running", "admitted", "queued"].includes(j.status))) {
      active = op;
      break;
    }
    if (opJobs.length > 0 && opJobs.every((j) => j.status === "succeeded")) {
      completed.push(op);
    }
  }
  return { completed, active, failed };
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

export const RunDetail = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token } = useAuth();
  const [logJob, setLogJob] = useState<JobRead | null>(null);

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
    mutationFn: () => cancelRun(api, runId!),
    onSuccess: () => {
      toast.success("Run cancelled");
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
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
      toast.success("Job re-queued for admission.");
      void queryClient.invalidateQueries({ queryKey: ["run-jobs", runId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const jobRows = useMemo(() => snapshot?.jobs ?? jobs.data ?? [], [snapshot?.jobs, jobs.data]);
  const runFinished = runTerminal || isTerminalRunStatus(runData?.status);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (runFinished) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [runFinished]);
  const duration = formatDuration(runDurationMs(runData));
  const workflowName = scan ? "Scanner" : targetLabel(runData ?? { id: "", kind: "", status: "", created_at: "" });
  const workflowLink = scan ? "/operations/scanner" : runData?.preset_id ? `/operations/presets/${runData.preset_id}` : "/operations";

  const pipelineSteps = useMemo(() => {
    if (scan) return scanPipelineSteps(runData?.status);
    const ops = ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"];
    const { completed, active, failed } = inferPipelineFromJobs(jobRows, ops);
    return maintenanceOpsToSteps(ops, active, completed, failed);
  }, [jobRows, runData?.status, scan]);

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
        else if (status === "failed" || status === "partial_failed") setAnnouncement("Run failed.");
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
        size: 240,
        cell: ({ row }) => (
          <Link to={`/tables/${row.original.table_id}`} className="identity-stack font-semibold hover:underline">
            {tableRef(row.original)}
          </Link>
        )
      },
      {
        id: "status",
        header: "Status",
        size: 120,
        cell: ({ row }) => <StatusTag status={row.original.status} />
      },
      {
        id: "timeline",
        header: "Timeline",
        size: 160,
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
        size: 120,
        cell: ({ row }) => formatEngineLabel(row.original.destination_engine) || row.original.destination_name || "—"
      },
      {
        id: "started",
        header: "Started",
        size: 140,
        cell: ({ row }) => formatTime(row.original.started_at)
      },
      {
        id: "actions",
        header: "",
        size: 160,
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            {STUCK_STATUSES.has(row.original.status) && !row.original.force_admit ? (
              <ConfirmAction title="Bypass scheduling gates?" onConfirm={() => forceAdmit.mutate(row.original.id)}>
                <Button size="sm" variant="secondary" loading={forceAdmit.isPending}>
                  Force run
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
    <div>
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
        rootCrumb={{ label: "Operations", to: "/operations" }}
        breadcrumbs={
          <nav aria-label="Breadcrumb" style={{ marginBottom: 12 }}>
            <ol className="flex list-none flex-wrap gap-2 p-0 m-0">
              <li>
                <Link to="/operations">Operations</Link>
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
            {runData?.status === "failed" ? (
              <Button variant="secondary" loading={diagnose.isPending} onClick={() => diagnose.mutate()}>
                Diagnose
              </Button>
            ) : null}
            {!runFinished ? (
              <ConfirmAction
                title="Cancel this run?"
                description="Queued jobs will be stopped; jobs already running may finish."
                onConfirm={() => cancel.mutate()}
              >
                <Button variant="danger" loading={cancel.isPending}>
                  Cancel
                </Button>
              </ConfirmAction>
            ) : null}
            <Button variant="secondary" onClick={() => navigate("/operations?tab=runs")}>
              All runs
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex flex-col gap-3">
          <Card>
            <CardContent className="pt-4">
              <OperationPipeline steps={pipelineSteps} title={scan ? "Scan pipeline" : "Maintenance pipeline"} />
            </CardContent>
          </Card>

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
                        runData?.status === "failed" ? "bg-[var(--color-status-danger)]" : "bg-primary"
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

          <Card>
            <CardHeader className="pb-2">
              <CardTitle>Jobs</CardTitle>
            </CardHeader>
            <CardContent className="p-0 pb-2">
              <DataTable
                columns={jobColumns}
                data={jobRows}
                density="compact"
                loading={jobs.isLoading && !snapshot?.jobs}
                getRowId={(row) => row.id}
                className="border-0"
              />
            </CardContent>
          </Card>

          <ConsoleShell
            title="Live log"
            items={messages}
            wsOpen={wsOpen}
            wsExhausted={wsExhausted}
            runTerminal={runFinished}
            reconnectAttempt={reconnectAttempt}
            onManualReconnect={manualReconnect}
            formatTime={formatTime}
            downloadName={`run-${shortRunId(runId)}-log`}
          />
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
              {jobLogMessages.length === 0 ? (
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
