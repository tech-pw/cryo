import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef, RowSelectionState } from "@tanstack/react-table";
import { Archive, Check, CircleAlert, CircleHelp, Copy, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../../api/client";
import type { components } from "../../../api/openapi";
import { useAuth } from "../../../lib/auth";
import { formatGb, formatNumber, formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { QueryErrorAlert } from "../../common";
import { Alert } from "../../ui/alert";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Card } from "../../ui/card";
import { Checkbox } from "../../ui/checkbox";
import { DataTable } from "../../ui/data-table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../../ui/dialog";
import { Segmented } from "../../ui/segmented";
import { Textarea } from "../../ui/textarea";

type Schemas = components["schemas"];
type ArchiveSummary = Schemas["ArchiveSummaryRead"];
type ArchivePartition = Schemas["ArchivePartitionRead"];
type ArchivePartitionPage = Schemas["ArchivePartitionPage"];
type Preflight = Schemas["ArchivePreflightRead"];
type ArchiveJob = Schemas["ArchiveJobRead"];
type ArchiveJobList = Schemas["ArchiveJobList"];
type Action = "archive" | "restore";
type Target = "INTELLIGENT_TIERING" | "STANDARD_IA" | "GLACIER_IR";

const PAGE_SIZE = 10;
const ARCHIVABLE = new Set(["auto", "cool", "archive_instant"]);
const CLASS_LABELS: Record<string, string> = {
  STANDARD: "Standard",
  INTELLIGENT_TIERING: "Intelligent-Tiering",
  STANDARD_IA: "Standard-IA",
  GLACIER_IR: "Glacier Instant Retrieval"
};
const SUGGESTION_TARGET: Record<string, Target> = {
  auto: "INTELLIGENT_TIERING",
  cool: "STANDARD_IA",
  archive_instant: "GLACIER_IR"
};
const TARGETS: { value: Target; label: string; hint: string }[] = [
  { value: "INTELLIGENT_TIERING", label: "Intelligent-Tiering", hint: "No retrieval fee. S3 moves files down after 30 days unread." },
  { value: "STANDARD_IA", label: "Standard-IA", hint: "About 45% cheaper. $0.01 per GB read, 30-day minimum." },
  { value: "GLACIER_IR", label: "Glacier IR", hint: "About 83% cheaper. $0.03 per GB read, 90-day minimum." }
];
const REASON_LABELS: Record<string, string> = {
  no_signal: "No write date",
  too_young: "Too recent",
  not_quiet: "Still written",
  recent_reads: "Read recently",
  read_timing_unknown: "Read time unknown",
  read_volume_unknown: "Read volume unknown",
  no_read_evidence: "No read evidence",
  zero_reads: "Unread",
  no_policy: "No archive policy",
  offline_target_refused: "Policy target is offline",
  small_file_backlog: "Compact first",
  avg_file_below_8mb: "Compact first",
  no_compaction_signal: "Compaction unknown"
};
const JOB_TONE: Record<string, "success" | "warning" | "danger" | "secondary"> = {
  succeeded: "success",
  partial: "warning",
  failed: "danger",
  queued: "secondary",
  running: "secondary"
};

const money = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value)
    ? "Not estimated"
    : new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      }).format(value);

const classLabel = (value: string | null | undefined) => CLASS_LABELS[value ?? "STANDARD"] ?? value ?? "Standard";

const suggestionText = (row: ArchivePartition) => {
  if (!ARCHIVABLE.has(row.suggestion)) return row.suggestion === "hot" ? "Keep in Standard" : "Not yet";
  return classLabel(SUGGESTION_TARGET[row.suggestion]);
};

const reasonText = (row: ArchivePartition) => {
  const labels = [...new Set(row.reason_codes.map((code) => REASON_LABELS[code] ?? code.replace(/_/g, " ")))];
  return labels.length ? labels.join(" · ") : "Cold";
};

const canArchive = (row: ArchivePartition) => !row.current_class && ARCHIVABLE.has(row.suggestion);

const usePreflight = (tableId: string) => {
  const { api } = useAuth();
  return useMutation({
    mutationFn: (body: { action: Action; target_class?: Target; partition_keys: string[]; all_eligible: boolean }) =>
      apiFetch<Preflight>(`/tables/${tableId}/tiering/preflight`, api, { method: "POST", body })
  });
};

const StatusIcon = ({ status }: { status: string }) =>
  status === "ok" || status === "pass" ? (
    <Check size={14} aria-label="OK" className="td-archive-ok" />
  ) : status === "missing" || status === "block" ? (
    <X size={14} aria-label="Missing" className="td-archive-bad" />
  ) : status === "warn" ? (
    <CircleAlert size={14} aria-label="Warning" className="td-archive-warn" />
  ) : (
    <CircleHelp size={14} aria-label="Not checked" className="td-archive-unknown" />
  );

const PolicyBlock = ({ policy }: { policy: string }) => {
  const [copied, setCopied] = useState(false);
  return (
    <div className="td-archive-policy">
      <div className="td-archive-policy-head">
        <span>IAM policy for the source's S3 role</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            void navigator.clipboard?.writeText(policy);
            setCopied(true);
          }}
        >
          <Copy size={14} aria-hidden /> {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre>{policy}</pre>
    </div>
  );
};

interface ConfirmProps {
  tableId: string;
  action: Action;
  keys: string[];
  allEligible: boolean;
  defaultTarget: Target;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted: () => void;
}

const ArchiveDialog = ({ tableId, action, keys, allEligible, defaultTarget, open, onOpenChange, onStarted }: ConfirmProps) => {
  const { api } = useAuth();
  const [target, setTarget] = useState<Target>(defaultTarget);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const preflight = usePreflight(tableId);
  const body = useMemo(
    () => ({
      action,
      target_class: action === "archive" ? target : undefined,
      partition_keys: allEligible ? [] : keys,
      all_eligible: allEligible
    }),
    [action, target, keys, allEligible]
  );
  const { mutate: runPreflight } = preflight;
  useEffect(() => {
    if (open) runPreflight(body);
  }, [open, body, runPreflight]);

  const apply = useMutation({
    mutationFn: () =>
      apiFetch<ArchiveJob>(`/tables/${tableId}/tiering/apply`, api, {
        method: "POST",
        body: { ...body, reason: reason.trim(), confirm: confirmed }
      }),
    onSuccess: () => {
      onStarted();
      onOpenChange(false);
    }
  });

  const data = preflight.data;
  const restoring = action === "restore";
  const ready =
    data?.can_apply === true && reason.trim().length >= 3 && (!data.requires_confirmation || confirmed);
  const count = allEligible ? data?.partitions_selected ?? 0 : keys.length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="td-archive-dialog">
        <DialogHeader>
          <DialogTitle>
            {restoring ? "Restore" : "Archive"} {formatNumber(count)} partition{count === 1 ? "" : "s"}
          </DialogTitle>
          <DialogDescription>
            {restoring
              ? "Copies each data file back to S3 Standard on the same key. Nothing in the table's metadata changes."
              : "Copies each data file onto its own key with a cheaper storage class. Paths and metadata don't change, so every query keeps working."}
          </DialogDescription>
        </DialogHeader>

        {restoring ? null : (
          <div className="td-archive-targets" role="radiogroup" aria-label="Storage class">
            {TARGETS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={target === option.value}
                className="td-archive-target"
                data-selected={target === option.value || undefined}
                onClick={() => setTarget(option.value)}
              >
                <strong>{option.label}</strong>
                <span>{option.hint}</span>
              </button>
            ))}
          </div>
        )}

        {preflight.isPending ? <p className="td-cost-muted">Checking cost, bucket settings and permissions…</p> : null}
        <QueryErrorAlert error={preflight.error} title="Couldn't check this archive" />

        {data ? (
          <>
            <dl className="td-archive-costs">
              <div>
                <dt>Data</dt>
                <dd>{formatGb(data.gb_estimate)}</dd>
                <p>{formatNumber(data.object_estimate)} files</p>
              </div>
              <div>
                <dt>One-time copy cost</dt>
                <dd>{money(data.request_cost_usd)}</dd>
                <p>S3 request charges</p>
              </div>
              <div>
                <dt>Storage / month</dt>
                <dd data-tone={data.storage_change_usd_month < 0 ? "good" : "bad"}>
                  {data.storage_change_usd_month < 0 ? "−" : "+"}
                  {money(Math.abs(data.storage_change_usd_month))}
                </dd>
                <p>{restoring ? "Back at the Standard rate" : `At the ${data.target_label} rate`}</p>
              </div>
              {restoring ? (
                <div>
                  <dt>Exit fees</dt>
                  <dd>{money(data.retrieval_cost_usd + data.early_delete_usd)}</dd>
                  <p>Retrieval and minimum-duration charges</p>
                </div>
              ) : null}
            </dl>

            {data.missing_permissions.length ? (
              <Alert
                variant="error"
                title="The source's S3 credentials need more permissions"
                description={
                  <>
                    Cryo reuses the lakehouse connection's S3 credentials. Add{" "}
                    <strong>{data.missing_permissions.join(", ")}</strong> on bucket{" "}
                    <code>{data.bucket}</code>, then check again.
                  </>
                }
              />
            ) : null}
            {data.iam_policy ? <PolicyBlock policy={data.iam_policy} /> : null}

            <div className="td-archive-checks">
              <section aria-label="Bucket checks">
                <h4>Bucket {data.bucket ? <code>{data.bucket}</code> : null}</h4>
                <ul>
                  {data.guardrails.map((item) => (
                    <li key={item.name}>
                      <StatusIcon status={item.status} />
                      <span>{item.detail}</span>
                    </li>
                  ))}
                </ul>
              </section>
              <section aria-label="Permissions">
                <h4>Permissions</h4>
                <ul>
                  {data.permissions.map((item) => (
                    <li key={item.action}>
                      <StatusIcon status={item.status} />
                      <span>
                        <code>{item.action}</code> {item.purpose.toLowerCase()}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            {data.blockers
              .filter((text) => !text.startsWith("The source's S3 credentials"))
              .map((text) => (
                <Alert key={text} variant="error" title={text} />
              ))}
            {data.warnings.map((text) => (
              <Alert key={text} variant="warning" title={text} />
            ))}
            {data.requires_confirmation ? (
              <label className="td-archive-confirm">
                <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(value === true)} />
                <span>I know these partitions aren't read. Use {data.target_label} anyway.</span>
              </label>
            ) : null}
            <label className="td-archive-reason">
              <span>Reason (kept in the audit log)</span>
              <Textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={2}
                maxLength={500}
                placeholder={restoring ? "Backfill needs 2024 data" : "Partitions older than a year, unread"}
              />
            </label>
          </>
        ) : null}
        <QueryErrorAlert error={apply.error} title={restoring ? "Couldn't start the restore" : "Couldn't start the archive"} />
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!ready} loading={apply.isPending} onClick={() => apply.mutate()}>
            {restoring ? "Restore to Standard" : `Archive to ${classLabel(target)}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const JobRow = ({ job }: { job: ArchiveJob }) => {
  const active = (job.status === "queued" || job.status === "running") && !job.stale;
  const done = job.objects_moved + job.objects_skipped + job.objects_failed;
  const pct = job.objects_total ? Math.round((done / job.objects_total) * 100) : 0;
  return (
    <li className="td-archive-job">
      <div className="td-archive-job-head">
        <Badge variant={job.stale ? "warning" : JOB_TONE[job.status] ?? "secondary"}>
          {job.stale ? "stalled" : job.status}
        </Badge>
        <strong>
          {job.action === "restore" ? "Restore" : "Archive"} {job.partitions} partition{job.partitions === 1 ? "" : "s"} →{" "}
          {job.target_label}
        </strong>
        <span className="td-cost-muted">
          {job.actor} · {formatTime(job.created_at)}
        </span>
      </div>
      {active ? (
        <div className="td-cost-track" aria-label={`${pct}% done`}>
          <span style={{ width: `${pct}%` }} />
        </div>
      ) : null}
      <p className="td-cost-muted">
        {formatNumber(job.objects_moved)} moved ({formatGb(job.gb_moved)}) · {formatNumber(job.objects_skipped)} skipped ·{" "}
        {formatNumber(job.objects_failed)} failed{job.objects_total ? ` of ${formatNumber(job.objects_total)}` : ""} · “{job.reason}”
      </p>
      {job.errors.length ? (
        <details>
          <summary>{job.errors.length} error{job.errors.length === 1 ? "" : "s"}</summary>
          <ul>
            {job.errors.map((error) => (
              <li key={error}>
                <code>{error}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </li>
  );
};

export const ArchivePanel = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const access = useWriteControl("archivePartitions");
  const [page, setPage] = useState(0);
  const [view, setView] = useState<"candidates" | "archived">("candidates");
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [dialog, setDialog] = useState<{ action: Action; allEligible: boolean } | null>(null);

  const summary = useQuery({
    queryKey: ["archive-summary", tableId, token],
    queryFn: () => apiFetch<ArchiveSummary>(`/tables/${tableId}/tiering`, api),
    enabled: Boolean(tableId),
    retry: false
  });
  const partitions = useQuery({
    queryKey: ["archive-partitions", tableId, token, view, page],
    queryFn: () =>
      apiFetch<ArchivePartitionPage>(`/tables/${tableId}/tiering/partitions`, api, {
        query: { limit: PAGE_SIZE, offset: page * PAGE_SIZE, state: view === "archived" ? "archived" : "standard" }
      }),
    enabled: Boolean(tableId),
    retry: false
  });
  const jobs = useQuery({
    queryKey: ["archive-jobs", tableId, token],
    queryFn: () => apiFetch<ArchiveJobList>(`/tables/${tableId}/tiering/jobs`, api),
    enabled: Boolean(tableId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.items.some((job) => (job.status === "queued" || job.status === "running") && !job.stale)
        ? 3000
        : false
  });
  const running = jobs.data?.items.some((job) => (job.status === "queued" || job.status === "running") && !job.stale);
  useEffect(() => {
    if (running === false) {
      void queryClient.invalidateQueries({ queryKey: ["archive-summary", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["archive-partitions", tableId] });
    }
  }, [running, queryClient, tableId]);

  const all = partitions.data?.items ?? [];
  const rows = all;
  const selectedKeys = Object.keys(selection).filter((key) => selection[key]);
  const evidence = summary.data?.read_evidence === "trusted";
  const defaultTarget: Target = evidence
    ? ((summary.data?.target_class as Target | null) ?? "INTELLIGENT_TIERING")
    : "INTELLIGENT_TIERING";
  const readyCount = summary.data ? Math.max(0, summary.data.eligible_count - (summary.data.archived_partitions ?? 0)) : 0;

  const columns = useMemo<ColumnDef<ArchivePartition, unknown>[]>(
    () => [
      {
        id: "partition",
        header: "Partition",
        cell: ({ row }) => <code className="td-archive-key">{row.original.partition_value || row.original.partition_key}</code>
      },
      {
        id: "age",
        header: "Age",
        cell: ({ row }) => (row.original.age_days == null ? "—" : `${formatNumber(row.original.age_days)} d`)
      },
      {
        id: "size",
        header: "Size",
        cell: ({ row }) => (row.original.size_gb == null ? "—" : formatGb(row.original.size_gb))
      },
      view === "archived"
        ? {
            id: "class",
            header: "Class",
            cell: ({ row }) => (
              <span>
                {classLabel(row.original.current_class)}
                <span className="td-archive-sub">since {formatTime(row.original.moved_at)}</span>
              </span>
            )
          }
        : {
            id: "suggestion",
            header: "Cryo suggests",
            cell: ({ row }) => (
              <span>
                <span data-archivable={canArchive(row.original) || undefined} className="td-archive-suggest">
                  {suggestionText(row.original)}
                </span>
                <span className="td-archive-sub">{reasonText(row.original)}</span>
              </span>
            )
          },
      {
        id: "saving",
        header: "Saving / month",
        cell: ({ row }) => money(row.original.net_saving_usd_month)
      }
    ],
    [view]
  );

  return (
    <Card id="archive" role="region" aria-labelledby="archive-heading" className="td-cost-card td-archive-card">
      <header className="td-archive-header">
        <div>
          <h3 id="archive-heading">
            <Archive size={16} aria-hidden /> Archive cold partitions
          </h3>
          <p>
            Move partitions nobody reads to a cheaper S3 class, in place. Queries keep working. Restore copies them back to
            Standard.
          </p>
        </div>
        {access.hidden ? null : (
          <Button
            size="sm"
            disabled={!access.allowed || readyCount === 0 || Boolean(running)}
            title={access.reason ?? (running ? "A job is already running" : undefined)}
            onClick={() => setDialog({ action: "archive", allEligible: true })}
          >
            Archive all suggested…
          </Button>
        )}
      </header>

      <QueryErrorAlert error={summary.error} title="Failed to load archive advice" />
      {summary.data ? (
        <dl className="td-archive-summary">
          <div>
            <dt>Ready to archive</dt>
            <dd>
              {formatNumber(readyCount)}
              <span> of {formatNumber(summary.data.partition_count)} partitions</span>
            </dd>
          </div>
          <div>
            <dt>Projected saving / month</dt>
            <dd>{evidence ? money(summary.data.projected_savings_usd_month) : "Needs read evidence"}</dd>
          </div>
          <div>
            <dt>Archived</dt>
            <dd>
              {formatNumber(summary.data.archived_partitions)}
              <span> partitions · {formatGb(summary.data.archived_gb)}</span>
            </dd>
          </div>
          <div>
            <dt>Read evidence</dt>
            <dd className="td-archive-evidence" data-trusted={evidence || undefined}>
              {evidence ? "From S3 access logs" : "None yet"}
            </dd>
            {evidence ? null : <p>Until access logs arrive, Cryo only suggests Intelligent-Tiering, which has no read fee.</p>}
          </div>
        </dl>
      ) : null}

      <div className="td-archive-toolbar">
        <Segmented
          size="small"
          aria-label="Partitions"
          value={view}
          onChange={(next) => {
            setView(next);
            setPage(0);
            setSelection({});
          }}
          options={[
            { value: "candidates", label: "In Standard" },
            { value: "archived", label: `Archived${summary.data?.archived_partitions ? ` (${summary.data.archived_partitions})` : ""}` }
          ]}
        />
        {selectedKeys.length && !access.hidden ? (
          <div className="td-archive-selection">
            <span>{selectedKeys.length} selected</span>
            <Button
              size="sm"
              variant={view === "archived" ? "secondary" : "primary"}
              disabled={!access.allowed || Boolean(running)}
              title={access.reason ?? undefined}
              onClick={() => setDialog({ action: view === "archived" ? "restore" : "archive", allEligible: false })}
            >
              {view === "archived" ? (
                <>
                  <RotateCcw size={14} aria-hidden /> Restore…
                </>
              ) : (
                "Archive…"
              )}
            </Button>
          </div>
        ) : null}
      </div>

      <QueryErrorAlert error={partitions.error} title="Failed to load archive partitions" />
      {partitions.isError ? null : (
        <DataTable
          columns={columns}
          data={rows}
          density="compact"
          loading={partitions.isLoading}
          showDensityToggle={false}
          getRowId={(row) => row.partition_key}
          rowSelection={access.hidden ? undefined : selection}
          onRowSelectionChange={access.hidden ? undefined : setSelection}
          enableRowSelection={
            access.hidden ? false : (row) => (view === "archived" ? Boolean(row.original.current_class) : canArchive(row.original))
          }
          emptyTitle={view === "archived" ? "Nothing archived yet" : "No partitions in the latest scan"}
          emptyDescription={
            view === "archived" ? "Archived partitions show here with their class." : "Run a scan so partition ages are recorded."
          }
          pagination={
            partitions.data && partitions.data.total > PAGE_SIZE
              ? { pageIndex: page, pageSize: PAGE_SIZE, totalRows: partitions.data.total, onPageChange: setPage }
              : undefined
          }
        />
      )}

      {jobs.data?.items.length ? (
        <section className="td-archive-jobs" aria-label="Archive jobs">
          <h4>Recent jobs</h4>
          <ul>
            {jobs.data.items.slice(0, 5).map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </ul>
        </section>
      ) : null}

      {dialog ? (
        <ArchiveDialog
          tableId={tableId}
          action={dialog.action}
          allEligible={dialog.allEligible}
          keys={selectedKeys}
          defaultTarget={defaultTarget}
          open
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
          onStarted={() => {
            setSelection({});
            void queryClient.invalidateQueries({ queryKey: ["archive-jobs", tableId] });
          }}
        />
      ) : null}
    </Card>
  );
};
