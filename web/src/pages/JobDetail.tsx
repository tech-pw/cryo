import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Clock,
  Code,
  Copy,
  Download,
  Filter,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  WrapText
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  diagnoseRunFailure,
  forceAdmitJob,
  getLatestRunDiagnosis,
  getJob,
  getJobDetailDashboard,
  getResourcePrediction,
  getRun,
  submitDiagnosisFeedback,
  type JobDetailDashboard
} from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { StatusTag } from "../components/status";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { MetricTile } from "../components/ui/stat";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { formatDateTime, formatDuration } from "../lib/format";
import { engineTagColor, formatEngineLabel, isNativeStatement } from "../lib/engines";
import { isScanRun, shortRunId } from "../lib/operations";
import { cn } from "../lib/cn";
import "./jobDetail.css";
import { useRunWorkflow } from "./operations/useRunWorkflow";

interface JobDetail extends JobDetailDashboard {}

const formatJobDuration = (seconds: number | null) => {
  if (!seconds) return "—";
  return formatDuration(seconds * 1000);
};

export const formatJobFileCount = (count: number | null | undefined): string =>
  count == null || Number.isNaN(count) ? "Not measured" : String(count);

const STUCK_STATUSES = new Set(["queued", "deferred"]);

const JobBreadcrumbs = ({
  runId,
  jobId,
  workflowLink,
  workflowName
}: {
  runId: string;
  jobId: string;
  workflowLink: string;
  workflowName: string;
}) => (
  <nav aria-label="Breadcrumb" className="mb-3">
    <ol className="m-0 flex list-none flex-wrap gap-2 p-0">
      <li>
        <Link to="/runs">Runs</Link>
      </li>
      <li aria-hidden>›</li>
      <li>
        <Link to={workflowLink}>{workflowName}</Link>
      </li>
      <li aria-hidden>›</li>
      <li>
        <Link to={`/runs/${runId}`}>Run {shortRunId(runId)}</Link>
      </li>
      <li aria-hidden>›</li>
      <li>
        <span aria-current="page">Job {shortRunId(jobId)}</span>
      </li>
    </ol>
  </nav>
);

const DetailSkeleton = () => (
  <div className="space-y-4">
    <Skeleton className="h-32 w-full" />
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton key={index} className="h-24 w-full" />
      ))}
    </div>
  </div>
);

const DescriptionTable = ({
  items
}: {
  items: Array<{ label: string; value: React.ReactNode }>;
}) => (
  <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
    {items.map((item) => (
      <div key={item.label} className="rounded-[var(--radius-sm)] border border-border px-3 py-2">
        <dt className="text-xs text-muted-foreground">{item.label}</dt>
        <dd className="mt-1 font-medium">{item.value}</dd>
      </div>
    ))}
  </dl>
);

export const ArtifactBlock = ({
  label,
  content,
  fileName
}: {
  label: string;
  content: string;
  fileName: string;
}) => {
  const [wrap, setWrap] = useState(true);

  const copy = () => {
    if (!navigator.clipboard) {
      toast.error("Clipboard access is unavailable");
      return;
    }
    void navigator.clipboard.writeText(content).then(
      () => toast.success(`${label} copied`),
      () => toast.error(`Could not copy ${label.toLowerCase()}`)
    );
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button size="sm" variant="secondary" onClick={copy}>
          <Copy aria-hidden="true" />
          Copy
        </Button>
        <Button size="sm" variant="secondary" onClick={download}>
          <Download aria-hidden="true" />
          Download
        </Button>
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={wrap}
          onClick={() => setWrap((current) => !current)}
        >
          <WrapText aria-hidden="true" />
          {wrap ? "Scroll" : "Wrap"}
        </Button>
      </div>
      <pre
        tabIndex={0}
        aria-label={label}
        className={cn(
          "m-0 max-w-full overflow-x-auto rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)] p-3 text-[11px]",
          wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
        )}
      >
        {content}
      </pre>
    </div>
  );
};

const engineBadgeVariant = (engine: string) => {
  const color = engineTagColor(engine);
  if (color === "green") return "success";
  if (color === "red") return "danger";
  if (color === "orange") return "warning";
  return "secondary";
};

export const JobDetail = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [rawOpen, setRawOpen] = useState(false);

  const { data, isLoading, error, refetch } = useQuery<JobDetail>({
    queryKey: ["job-detail", api.token, jobId],
    queryFn: () => getJobDetailDashboard(api, jobId!),
    enabled: Boolean(jobId)
  });

  const run = useQuery({
    queryKey: ["run", data?.run_id, api.token],
    queryFn: () => getRun(api, data!.run_id),
    enabled: Boolean(data?.run_id)
  });

  const rawJob = useQuery({
    queryKey: ["job-raw", api.token, jobId],
    queryFn: () => getJob(api, jobId!),
    enabled: Boolean(jobId),
    retry: false
  });
  const resourcePrediction = useQuery({
    queryKey: ["resource-prediction", data?.table_id],
    queryFn: () => getResourcePrediction(api, data!.table_id),
    enabled: Boolean(data?.table_id),
    retry: false
  });
  const latestDiagnosis = useQuery({
    queryKey: ["run-diagnosis-latest", data?.run_id],
    queryFn: () => getLatestRunDiagnosis(api, data!.run_id, data!.job_id),
    enabled: Boolean(data?.run_id),
    retry: false
  });
  const diagnose = useMutation({
    mutationFn: () => diagnoseRunFailure(api, data!.run_id, { force: true, jobId: data!.job_id }),
    onSuccess: () => {
      void latestDiagnosis.refetch();
    },
    onError: (err) => toast.error((err as Error).message)
  });
  const diagnosisRecord = latestDiagnosis.data ?? diagnose.data ?? null;
  const diagnosisFeedback = useMutation({
    mutationFn: (feedback: "up" | "down") =>
      submitDiagnosisFeedback(api, diagnosisRecord!.id, { feedback }),
    onSuccess: () => {
      toast.success("Diagnosis feedback recorded");
      void latestDiagnosis.refetch();
    },
    onError: (err) => toast.error((err as Error).message)
  });
  const forceAdmit = useMutation({
    mutationFn: () => forceAdmitJob(api, jobId!),
    onSuccess: () => {
      toast.success("Scheduling gates bypassed. The job still waits for a free slot.");
      void queryClient.invalidateQueries({ queryKey: ["job-detail", api.token, jobId] });
      void queryClient.invalidateQueries({ queryKey: ["job-raw", api.token, jobId] });
    },
    onError: (err) => toast.error((err as Error).message)
  });

  const scan = isScanRun(run.data);
  const { name: workflowName, link: workflowLink } = useRunWorkflow(run.data);

  return (
    <div className="job-detail-page">
      {data ? (
        <JobBreadcrumbs
          runId={data.run_id}
          jobId={data.job_id}
          workflowLink={workflowLink}
          workflowName={workflowName}
        />
      ) : null}

      <PageHeader
        title="Maintenance job detail"
        subtitle={data ? `Job ID: ${data.job_id}` : undefined}
        actions={
          data ? (
            <div className="flex flex-wrap items-center gap-2">
              <StatusTag status={data.status} />
              <Button size="sm" variant="secondary" asChild>
                <Link to={`/tables/${data.table_id}`}>Open table</Link>
              </Button>
              <Button size="sm" variant="primary" asChild>
                <Link to={`/tables/${data.table_id}?tab=maintenance`}>Re-run from table</Link>
              </Button>
            </div>
          ) : undefined
        }
      />

      {data ? (
        <section className="job-outcome-card" aria-labelledby="job-outcome-title">
          <div className="job-outcome-card__header">
            <div>
              <p className="job-outcome-card__eyebrow">Execution decision</p>
              <h2 id="job-outcome-title">
                {data.status === "failed"
                  ? "Needs attention"
                  : STUCK_STATUSES.has(data.status)
                    ? "Waiting for admission"
                    : data.status === "running"
                      ? "In progress"
                      : data.status === "succeeded"
                        ? "Completed"
                        : `Job ${data.status}`}
              </h2>
              <p>
                {data.status === "failed"
                  ? "Use the diagnosis and rendered statement below to resolve this table safely."
                  : STUCK_STATUSES.has(data.status)
                    ? "The scheduler has not admitted this job yet; force run is available when you have verified capacity."
                    : data.status === "running"
                      ? "The worker is executing this operation. Artifacts and timings update as the job completes."
                      : "The recorded job outcome and supporting evidence are shown below."}
              </p>
            </div>
            <StatusTag status={data.status} />
          </div>
          <dl className="job-outcome-card__metrics">
            <div>
              <dt>Table</dt>
              <dd>{data.table_name}</dd>
            </div>
            <div>
              <dt>Operation</dt>
              <dd>{data.operation_type}</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>{formatEngineLabel(data.engine)}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatJobDuration(data.duration_seconds)}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      <AsyncBoundary
        isLoading={isLoading}
        isError={Boolean(error) || (!isLoading && !data)}
        error={error ?? new Error("Job not found")}
        onRetry={() => void refetch()}
        skeleton={<DetailSkeleton />}
      >
        {data ? (
          <>
            {data.error_message ? (
              <Alert
                variant={data.status === "failed" ? "error" : "warning"}
                className="mb-6"
                title={data.status === "failed" ? "Job failed" : `Job ${data.status}`}
                description={
                  <div className="space-y-3">
                    <p>{data.error_message}</p>
                  </div>
                }
              />
            ) : null}
            {STUCK_STATUSES.has(data.status) && !data.force_admit ? (
              <ConfirmAction
                title="Bypass scheduling gates?"
                description={`Skips cooldown, benefit, and off-peak for ${data.table_name} on ${formatEngineLabel(data.engine)}. It does not start the job. Pause, table locks, PII, and a free worker slot still apply.`}
                okText="Bypass gates"
                okButtonProps={{ danger: false }}
                onConfirm={() => forceAdmit.mutate()}
              >
                <Button size="sm" variant="secondary" loading={forceAdmit.isPending}>
                  Bypass gates
                </Button>
              </ConfirmAction>
            ) : null}

            {data.status === "failed" ? (
              <Card className="mb-4">
                <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
                  <CardTitle className="inline-flex items-center gap-2">
                    <Bot className="size-4" aria-hidden />
                    Failure diagnosis
                  </CardTitle>
                  <Button variant="secondary" size="sm" loading={diagnose.isPending} onClick={() => diagnose.mutate()}>
                    Diagnose
                  </Button>
                </CardHeader>
                <CardContent className="space-y-3">
                  {diagnosisRecord &&
                  (!diagnosisRecord.job_id || diagnosisRecord.job_id === data.job_id) ? (
                    <>
                      <p className="font-semibold">
                        {diagnosisRecord.diagnosis_class.replaceAll("_", " ")} (
                        {Math.round(diagnosisRecord.confidence * 100)}% confidence)
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Diagnosed {formatDateTime(diagnosisRecord.diagnosed_at)}
                        {diagnosisRecord.skillset_id
                          ? ` • Skillset ${diagnosisRecord.skillset_id}`
                          : diagnosisRecord.observation_id
                            ? ` • Observation ${diagnosisRecord.observation_id}`
                            : " • Source type not reported"}
                      </p>
                      {diagnosisRecord.summary ? <p className="text-sm">{diagnosisRecord.summary}</p> : null}
                      {diagnosisRecord.runbook_step ? (
                        <Alert variant="info" title="Runbook step" description={diagnosisRecord.runbook_step} />
                      ) : null}
                      {diagnosisRecord.evidence.length ? (
                        <ArtifactBlock
                          label="Diagnosis evidence"
                          content={diagnosisRecord.evidence.map((line) => `- ${line}`).join("\n")}
                          fileName={`job-${data.job_id}-diagnosis-evidence.txt`}
                        />
                      ) : null}
                      {diagnosisRecord.feedback ? (
                        <p role="status" className="text-xs text-muted-foreground">
                          Recorded feedback: {diagnosisRecord.feedback === "up" ? "Helpful" : "Not helpful"}
                        </p>
                      ) : null}
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          size="sm"
                          variant={diagnosisRecord.feedback === "up" ? "primary" : "secondary"}
                          disabled={!diagnosisRecord}
                          loading={diagnosisFeedback.isPending}
                          onClick={() => diagnosisFeedback.mutate("up")}
                        >
                          <ThumbsUp className="size-4" aria-hidden />
                          Helpful
                        </Button>
                        <Button
                          size="sm"
                          variant={diagnosisRecord.feedback === "down" ? "primary" : "secondary"}
                          disabled={!diagnosisRecord}
                          loading={diagnosisFeedback.isPending}
                          onClick={() => diagnosisFeedback.mutate("down")}
                        >
                          <ThumbsDown className="size-4" aria-hidden />
                          Not helpful
                        </Button>
                      </div>
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Run diagnosis uses run/job/audit evidence. Trigger when triaging failed jobs.
                    </p>
                  )}
                </CardContent>
              </Card>
            ) : null}

            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricTile
                label="Duration"
                value={formatJobDuration(data.duration_seconds)}
                hint={<Clock className="size-3.5 text-muted-foreground" aria-hidden />}
              />
              <MetricTile
                label="Input Files"
                value={formatJobFileCount(data.input_files_count)}
                className="[&_p:nth-child(2)]:text-[#1890ff]"
              />
              <MetricTile
                label="Output Files"
                value={formatJobFileCount(data.output_files_count)}
                className="[&_p:nth-child(2)]:text-[var(--color-status-success)]"
              />
              <MetricTile
                label="Effectiveness Ratio"
                value={
                  data.effectiveness_ratio == null || Number.isNaN(data.effectiveness_ratio)
                    ? "Not measured"
                    : data.effectiveness_ratio.toFixed(1)
                }
                suffix={data.effectiveness_ratio == null ? undefined : ":1"}
                className={cn(
                  data.effectiveness_ratio == null
                    ? ""
                    : data.effectiveness_ratio >= 10
                      ? "[&_p:nth-child(2)]:text-[var(--color-status-success)]"
                      : "[&_p:nth-child(2)]:text-[var(--color-status-warning)]"
                )}
              />
            </div>

            {resourcePrediction.data ? (
              <Card className="mb-4">
                <CardHeader className="pb-2">
                  <CardTitle>Resource prediction (shadow mode)</CardTitle>
                </CardHeader>
                <CardContent>
                  <DescriptionTable
                    items={[
                      {
                        label: "Predicted p50",
                        value: formatJobDuration(resourcePrediction.data.predicted_p50_seconds)
                      },
                      {
                        label: "Predicted p90",
                        value: formatJobDuration(resourcePrediction.data.predicted_p90_seconds)
                      },
                      {
                        label: "Recommended CPU",
                        value: `${resourcePrediction.data.recommended_cpu_cores.toFixed(1)} cores`
                      },
                      {
                        label: "Recommended memory",
                        value: `${resourcePrediction.data.recommended_memory_gb.toFixed(1)} GB`
                      },
                      {
                        label: "Confidence",
                        value: `${Math.round(resourcePrediction.data.confidence * 100)}%`
                      },
                      { label: "Samples", value: resourcePrediction.data.samples }
                    ]}
                  />
                </CardContent>
              </Card>
            ) : null}

            <Card className="mb-4">
              <CardHeader className="pb-2">
                <CardTitle>Job Information</CardTitle>
              </CardHeader>
              <CardContent>
                <DescriptionTable
                  items={[
                    {
                      label: "Table",
                      value: (
                        <Button
                          variant="ghost"
                          className="h-auto p-0 text-primary hover:underline"
                          onClick={() => navigate(`/tables/${data.table_id}`)}
                        >
                          {data.table_name}
                        </Button>
                      )
                    },
                    {
                      label: "Parent run",
                      value: (
                        <Link to={`/runs/${data.run_id}`} className="text-primary hover:underline">
                          Run {shortRunId(data.run_id)}
                        </Link>
                      )
                    },
                    {
                      label: "Operation Type",
                      value: <Badge variant="default">{data.operation_type}</Badge>
                    },
                    {
                      label: "Engine",
                      value: (
                        <Badge variant={engineBadgeVariant(data.engine)}>{formatEngineLabel(data.engine)}</Badge>
                      )
                    },
                    { label: "Replica", value: data.replica || "—" },
                    {
                      label: "Started At",
                      value: data.started_at ? formatDateTime(data.started_at) : "—"
                    },
                    {
                      label: "Completed At",
                      value: data.completed_at ? formatDateTime(data.completed_at) : "In progress"
                    }
                  ]}
                />
              </CardContent>
            </Card>

            {data.partition_predicates && data.partition_predicates.length > 0 ? (
              <Card className="mb-4">
                <CardHeader className="pb-2">
                  <CardTitle className="inline-flex items-center gap-2">
                    <Filter className="size-4" aria-hidden />
                    Partition Predicates
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {data.partition_predicates.map((pred, idx) => (
                    <Alert key={idx} variant="info" title={pred} />
                  ))}
                </CardContent>
              </Card>
            ) : null}

            {data.sql_statements && data.sql_statements.length > 0 ? (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="inline-flex items-center gap-2">
                    <Code className="size-4" aria-hidden />
                    {data.sql_statements.some((sql) => isNativeStatement(sql))
                      ? "Rendered statements"
                      : "SQL Statements"}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {data.sql_statements.map((sql, idx) => (
                    <Card key={idx}>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Statement {idx + 1}</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <ArtifactBlock
                          label={`${isNativeStatement(sql) ? "Rendered statement" : "SQL statement"} ${idx + 1}`}
                          content={
                            isNativeStatement(sql)
                              ? `${sql}\n\nSee raw job record for execute payload details.`
                              : sql
                          }
                          fileName={`job-${data.job_id}-statement-${idx + 1}.txt`}
                        />
                      </CardContent>
                    </Card>
                  ))}
                </CardContent>
              </Card>
            ) : null}

            <Card className="mt-4">
              <CardHeader className="pb-2">
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-left"
                  onClick={() => setRawOpen((open) => !open)}
                >
                  <CardTitle>Raw job record (API)</CardTitle>
                  <TriangleAlert className="size-4 text-muted-foreground" aria-hidden />
                </button>
              </CardHeader>
              {rawOpen ? (
                <CardContent>
                  {rawJob.data ? (
                    <pre className="inline-json">{JSON.stringify(rawJob.data, null, 2)}</pre>
                  ) : rawJob.isLoading ? (
                    <Skeleton className="h-24 w-full" />
                  ) : (
                    <Alert variant="warning" title={(rawJob.error as Error)?.message ?? "Unavailable"} />
                  )}
                </CardContent>
              ) : null}
            </Card>
          </>
        ) : null}
      </AsyncBoundary>
    </div>
  );
};
