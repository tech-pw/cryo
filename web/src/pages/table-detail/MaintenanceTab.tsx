import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams } from "react-router-dom";
import {
  getTableMaintenanceStatus,
  type BlockingReasonRead,
  type MaintenanceActionReceipt,
  type MaintenanceStatusRead
} from "../../api/maintenanceStatus";
import { QueryErrorAlert } from "../../components/common";
import { StatusTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Card } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { EmptyState } from "../../components/ui/empty-state";
import { useAuth } from "../../lib/auth";
import { auditBytesLabel, auditFilesLabel } from "../../lib/auditFeed";
import { formatDuration, formatTime } from "../../lib/format";

const BYTE_KEYS = new Set([
  "actual_bytes_freed",
  "bytes",
  "bytes_freed",
  "bytes_rewritten",
  "size_bytes"
]);

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

export const MaintenanceTab = () => {
  const { tableId = "" } = useParams();
  const { api } = useAuth();
  const status = useQuery({
    queryKey: ["table-maintenance-status", tableId, api.token],
    queryFn: () => getTableMaintenanceStatus(api, tableId),
    enabled: tableId.length > 0
  });
  const data = status.data;

  return (
    <div>
      <QueryErrorAlert error={status.error} title="Couldn't load maintenance status" />
      {!data && status.isLoading ? <p className="text-sm text-muted-foreground">Loading maintenance status</p> : null}
      {data ? (
        <>
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
        </>
      ) : null}
    </div>
  );
};
