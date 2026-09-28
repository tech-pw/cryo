import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { getTableRuns } from "../../api/client";
import type { TableRunRead } from "../../api/types";
import {
  getTableMaintenanceStatus,
  type BlockingReasonRead,
  type MaintenanceActionReceipt,
  type MaintenanceStatusRead
} from "../../api/maintenanceStatus";
import { CompactionAnalysis } from "../../components/table-detail/maintenance/CompactionAnalysis";
import { EffectiveSettings } from "../../components/table-detail/maintenance/EffectiveSettings";
import { MaintenanceHistory, RECENT_RUNS_LIMIT } from "../../components/table-detail/maintenance/MaintenanceHistory";
import { QueryErrorAlert } from "../../components/common";
import { StatusTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Card } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { EmptyState } from "../../components/ui/empty-state";
import { useAuth } from "../../lib/auth";
import { auditBytesLabel, auditFilesLabel } from "../../lib/auditFeed";
import { formatDuration, formatTime } from "../../lib/format";
import { tableKeys } from "./tableKeys";

const BYTE_KEYS = new Set([
  "actual_bytes_freed",
  "bytes",
  "bytes_freed",
  "bytes_rewritten",
  "size_bytes"
]);

const PENDING_WORK = new Set(["queued", "admitted", "running", "waiting", "deferred"]);

export const formatMaintenanceValue = (key: string, value: unknown): string => {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (BYTE_KEYS.has(key)) return typeof value === "number" ? auditBytesLabel(value) : "Unknown";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "Unknown";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "(empty)";
    return value.map((item) => (item == null || item === "" ? "Unknown" : String(item))).join(", ");
  }
  return JSON.stringify(value);
};

const receiptBytes = (row: MaintenanceActionReceipt): number | null | undefined => {
  if (row.actual_bytes_freed !== undefined) return row.actual_bytes_freed;
  if (row.bytes_freed !== undefined) return row.bytes_freed;
  return null;
};

const operationLabel = (row: MaintenanceActionReceipt): string => row.action || row.operation || "Unknown";

const eligibilityLabel = (eligible: boolean | null | undefined): string => {
  if (eligible === true) return "Eligible for nightly selection";
  if (eligible === false) return "Not eligible for nightly selection";
  return "Eligibility unknown";
};

export const nowWorkLabel = (status: string): string => {
  const key = status.trim().toLowerCase();
  if (key === "running") return "Running";
  if (key === "queued") return "Queued";
  if (key === "waiting" || key === "admitted") return "Waiting for engine capacity";
  if (key === "deferred") return "Deferred";
  return status || "not reported";
};

const isPendingWork = (status: string): boolean => PENDING_WORK.has(status.trim().toLowerCase());

const ReasonValues = ({ reason }: { reason: BlockingReasonRead }) => {
  const entries = Object.entries(reason.values ?? {});
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">No values recorded.</p>;
  return (
    <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{key}</dt>
          <dd className="truncate text-sm text-foreground [font-variant-numeric:tabular-nums]">
            {formatMaintenanceValue(key, value)}
          </dd>
        </div>
      ))}
    </dl>
  );
};

const EligibilityChecklist = ({ reasons }: { reasons: BlockingReasonRead[] }) => {
  if (reasons.length === 0) {
    return <p className="text-sm text-muted-foreground">No blocking reasons.</p>;
  }
  return (
    <ul aria-label="Eligibility checklist" className="space-y-3">
      {reasons.map((reason, index) => (
        <li key={`${reason.code}-${index}`} className="rounded-[var(--radius-default)] border border-border p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="text-sm font-medium text-foreground">{reason.summary}</p>
            <Badge variant={reason.blocks_nightly_selection ? "warning" : "secondary"}>
              {reason.blocks_nightly_selection ? "Blocks nightly selection" : "Does not block nightly selection"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {reason.code} · {reason.predicate}
            {reason.until ? ` · until ${formatTime(reason.until)}` : ""}
          </p>
          <ReasonValues reason={reason} />
        </li>
      ))}
    </ul>
  );
};

const LastActions = ({ status }: { status: MaintenanceStatusRead }) => {
  const actions = status.last_actions ?? [];
  const columns = useMemo<ColumnDef<MaintenanceActionReceipt & { rowId: string }, unknown>[]>(
    () => [
      {
        id: "ts",
        header: "Time",
        cell: ({ row }) => (row.original.ts ? formatTime(row.original.ts) : "Unknown")
      },
      {
        id: "operation",
        header: "Operation",
        cell: ({ row }) => operationLabel(row.original)
      },
      {
        id: "engine",
        header: "Engine",
        cell: ({ row }) => row.original.engine || "Unknown"
      },
      {
        id: "duration",
        header: "Duration",
        cell: ({ row }) =>
          row.original.duration_ms == null ? "Unknown" : formatDuration(row.original.duration_ms)
      },
      {
        id: "files",
        header: "Files",
        cell: ({ row }) => auditFilesLabel(row.original.input_files_count, row.original.output_files_count)
      },
      {
        id: "bytes",
        header: "Bytes",
        cell: ({ row }) => auditBytesLabel(receiptBytes(row.original))
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) =>
          row.original.status ? <StatusTag status={row.original.status} /> : <span>Unknown</span>
      }
    ],
    []
  );

  if (actions.length === 0) {
    return (
      <EmptyState
        title="Last actions are not recorded"
        description="No receipt was returned for any operation. Nothing here is inferred."
      />
    );
  }

  const rows = actions.map((action, index) => ({
    ...action,
    rowId: action.id == null ? `action-${index}` : String(action.id)
  }));

  return (
    <DataTable
      columns={columns}
      data={rows}
      density="compact"
      getRowId={(row) => row.rowId}
      emptyTitle="Last actions are not recorded"
      emptyDescription="No receipt was returned for any operation."
    />
  );
};

const NowSection = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const runs = useQuery({
    queryKey: tableKeys.runs(tableId, token, RECENT_RUNS_LIMIT, 0),
    queryFn: () => getTableRuns(api, tableId, { limit: RECENT_RUNS_LIMIT, offset: 0 }),
    enabled: tableId.length > 0,
    retry: false
  });
  const pending = (runs.data ?? []).filter((run) => isPendingWork(run.status));

  return (
    <section className="mb-6" aria-labelledby="maintenance-now">
      <h3 id="maintenance-now" className="mb-2 text-sm font-semibold text-foreground">
        Now
      </h3>
      <QueryErrorAlert error={runs.error} title="Couldn't load pending work" />
      {runs.isLoading ? <p className="text-sm text-muted-foreground">Loading pending work</p> : null}
      {!runs.isLoading && pending.length === 0 ? (
        <p className="text-sm text-muted-foreground">No pending or running work.</p>
      ) : null}
      {pending.length > 0 ? (
        <ul className="space-y-2">
          {pending.map((run) => (
            <PendingWork key={run.job_id || run.run_id} run={run} />
          ))}
        </ul>
      ) : null}
    </section>
  );
};

const PendingWork = ({ run }: { run: TableRunRead }) => {
  const label = nowWorkLabel(run.status);
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <span>{label}</span>
      {run.run_id ? (
        <Link to={`/runs/${run.run_id}`} className="text-primary hover:underline">
          Open run
        </Link>
      ) : (
        <span className="text-muted-foreground">Run id not recorded</span>
      )}
    </li>
  );
};

const WhySection = ({ status }: { status: MaintenanceStatusRead }) => {
  const first = (status.blocking_reasons ?? []).find((reason) => reason.blocks_nightly_selection) ?? null;
  const selected =
    status.eligible === true ? "selected" : status.eligible === false ? "not selected" : "unknown";
  return (
    <section className="mb-6" aria-labelledby="maintenance-why">
      <h3 id="maintenance-why" className="mb-2 text-sm font-semibold text-foreground">
        Why / why not
      </h3>
      <p className="text-sm text-foreground">
        Current evaluation: {selected}
        {first ? `. First gate: ${first.code}.` : ". No failing gate on this evaluation."}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">History not recorded yet</p>
    </section>
  );
};

export const MaintenanceTab = () => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const status = useQuery({
    queryKey: tableKeys.maintenanceStatus(tableId, token),
    queryFn: () => getTableMaintenanceStatus(api, tableId),
    enabled: tableId.length > 0
  });
  const data = status.data;
  const scanBackoff = (data?.blocking_reasons ?? []).find((reason) => reason.code === "scan_retry_backoff");

  return (
    <div>
      <QueryErrorAlert error={status.error} title="Couldn't load maintenance status" />
      {!data && status.isLoading ? <p className="text-sm text-muted-foreground">Loading maintenance status</p> : null}
      <NowSection tableId={tableId} />
      {data ? (
        <section className="mb-6" aria-labelledby="maintenance-tonight">
          <h3 id="maintenance-tonight" className="mb-2 text-sm font-semibold text-foreground">
            Tonight
          </h3>
          <Card className="mb-4">
            <div className="p-4">
              <p className="text-xs text-muted-foreground">Nightly selection</p>
              <h2 className="text-2xl font-semibold text-foreground">{eligibilityLabel(data.eligible)}</h2>
              <p className="mt-2 max-w-3xl text-sm text-muted-foreground">{data.eligibility_basis}</p>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border bg-[var(--color-surface-sunken)] px-4 py-2 text-xs text-muted-foreground">
              <span>Evaluated {data.evaluated_at ? formatTime(data.evaluated_at) : "Unknown"}</span>
              <span>
                Next nightly{" "}
                {data.next_nightly_at ? formatTime(data.next_nightly_at) : "not recorded"}
              </span>
              <span>
                Cooldown {formatMaintenanceValue("cooldown_hours", data.thresholds?.cooldown_hours)} h
              </span>
              <span>
                Stats window {formatMaintenanceValue("max_stats_age_days", data.thresholds?.max_stats_age_days)} days
              </span>
            </div>
          </Card>
          {scanBackoff ? (
            <p className="mb-4 text-sm text-muted-foreground">
              Scan backoff until {scanBackoff.until ? formatTime(scanBackoff.until) : "not recorded"}.
            </p>
          ) : null}

          <section className="mb-6" aria-labelledby="maintenance-checklist">
            <h3 id="maintenance-checklist" className="mb-2 text-sm font-semibold text-foreground">
              Eligibility checklist
            </h3>
            <EligibilityChecklist reasons={data.blocking_reasons ?? []} />
          </section>

          <section className="mb-6" aria-labelledby="maintenance-actions">
            <h3 id="maintenance-actions" className="mb-2 text-sm font-semibold text-foreground">
              Last actions
            </h3>
            <LastActions status={data} />
          </section>

          {(data.omitted ?? []).length > 0 ? (
            <section aria-labelledby="maintenance-omitted">
              <h3 id="maintenance-omitted" className="mb-2 text-sm font-semibold text-foreground">
                Not evaluated
              </h3>
              <ul className="space-y-2">
                {data.omitted.map((gate) => (
                  <li key={gate.code} className="text-sm text-muted-foreground">
                    <span className="font-medium text-foreground">{gate.code}</span>
                    {": "}
                    {gate.why}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </section>
      ) : null}
      {data ? <WhySection status={data} /> : null}
      <section className="mb-6" aria-labelledby="maintenance-lanes">
        <h3 id="maintenance-lanes" className="mb-2 text-sm font-semibold text-foreground">
          Last 30 days
        </h3>
        <p className="text-sm text-muted-foreground">History not recorded yet</p>
      </section>
      <MaintenanceHistory tableId={tableId} />
      <EffectiveSettings tableId={tableId} />
      <CompactionAnalysis tableId={tableId} />
    </div>
  );
};
