import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getTableAudit, getTableRuns } from "../../../api/client";
import type { AuditEntry, TableRunRead } from "../../../api/types";
import { QueryErrorAlert } from "../../common";
import { EmptyState } from "../../ui/empty-state";
import { useAuth } from "../../../lib/auth";
import { auditBytesLabel } from "../../../lib/auditFeed";
import { formatDuration, formatTime } from "../../../lib/format";
import { MAINTENANCE_OPERATION_ORDER, type MaintenanceOperation } from "../../../lib/operationOrder";
import { tableKeys } from "../../../pages/table-detail/tableKeys";

/** AuditLog.job_id → jobs.id. Audit rows have no run_id column. Never join on ts. */
export const RECENT_RUNS_LIMIT = 50;
export const HISTORY_PAGE_SIZE = 25;

export interface MaintenanceAuditRow extends AuditEntry {
  job_id?: string | null;
  run_id?: string | null;
  operation?: string | null;
  committed_snapshot_id?: string | null;
}

export type AuditJoinKey = { kind: "job" | "run"; id: string };

const cleanId = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

export const auditRunJoinKey = (row: {
  job_id?: string | null;
  run_id?: string | null;
}): AuditJoinKey | null => {
  const jobId = cleanId(row.job_id);
  if (jobId) return { kind: "job", id: jobId };
  const runId = cleanId(row.run_id);
  if (runId) return { kind: "run", id: runId };
  return null;
};

export const historyPageFromSearch = (value: string | null): number => {
  if (!value || !/^[1-9]\d*$/.test(value)) return 1;
  return Number(value);
};

const operationRank = (name?: string | null): number => {
  if (!name) return MAINTENANCE_OPERATION_ORDER.length + 1;
  const index = MAINTENANCE_OPERATION_ORDER.indexOf(name as MaintenanceOperation);
  return index === -1 ? MAINTENANCE_OPERATION_ORDER.length : index;
};

export const sortReceiptsByOperation = <T extends { action?: string | null; operation?: string | null }>(
  rows: readonly T[]
): T[] =>
  rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const rank = operationRank(left.row.action || left.row.operation) - operationRank(right.row.action || right.row.operation);
      return rank === 0 ? left.index - right.index : rank;
    })
    .map((item) => item.row);

export type HistoryBlock =
  | { kind: "ungrouped"; row: MaintenanceAuditRow }
  | {
      kind: "run";
      join: AuditJoinKey;
      run: TableRunRead | null;
      receipts: MaintenanceAuditRow[];
    };

export const groupAuditReceipts = (
  rows: readonly MaintenanceAuditRow[],
  runs: readonly TableRunRead[]
): HistoryBlock[] => {
  const byJob = new Map<string, TableRunRead>();
  const byRun = new Map<string, TableRunRead>();
  for (const run of runs) {
    if (run.job_id) byJob.set(run.job_id, run);
    if (run.run_id) byRun.set(run.run_id, run);
  }
  const blocks: HistoryBlock[] = [];
  const groups = new Map<string, Extract<HistoryBlock, { kind: "run" }>>();
  for (const row of rows) {
    const join = auditRunJoinKey(row);
    if (!join) {
      blocks.push({ kind: "ungrouped", row });
      continue;
    }
    const mapKey = `${join.kind}:${join.id}`;
    let group = groups.get(mapKey);
    if (!group) {
      const run =
        join.kind === "job"
          ? (byJob.get(join.id) ?? (cleanId(row.run_id) ? byRun.get(cleanId(row.run_id) as string) ?? null : null))
          : (byRun.get(join.id) ?? null);
      group = { kind: "run", join, run, receipts: [] };
      groups.set(mapKey, group);
      blocks.push(group);
    }
    group.receipts.push(row);
  }
  for (const group of groups.values()) {
    group.receipts = sortReceiptsByOperation(group.receipts);
  }
  return blocks;
};

export const historyRunId = (block: Extract<HistoryBlock, { kind: "run" }>): string | null => {
  if (cleanId(block.run?.run_id)) return block.run?.run_id ?? null;
  if (block.join.kind === "run") return block.join.id;
  for (const row of block.receipts) {
    const runId = cleanId(row.run_id);
    if (runId) return runId;
  }
  return null;
};

const operationName = (row: MaintenanceAuditRow): string => row.action || row.operation || "not reported";

const filesText = (row: MaintenanceAuditRow): string => {
  if (row.input_files_count == null || row.output_files_count == null) return "not reported";
  return `${row.input_files_count} → ${row.output_files_count}`;
};

const bytesText = (row: MaintenanceAuditRow): string => {
  if (row.actual_bytes_freed == null || Number.isNaN(row.actual_bytes_freed)) return "not reported";
  return auditBytesLabel(row.actual_bytes_freed);
};

const isFailure = (status?: string | null): boolean => {
  const key = (status ?? "").toLowerCase();
  return key === "failed" || key === "error" || key === "failure";
};

const errorText = (row: MaintenanceAuditRow): string | null => {
  if (!isFailure(row.status)) return null;
  if (typeof row.detail === "string" && row.detail.trim()) return row.detail;
  if (row.detail && typeof row.detail === "object") return JSON.stringify(row.detail);
  return "not reported";
};

const ReceiptFields = ({ row }: { row: MaintenanceAuditRow }) => {
  const failure = errorText(row);
  return (
    <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
      <div>
        <dt className="text-xs text-muted-foreground">Operation</dt>
        <dd>{operationName(row)}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Files</dt>
        <dd>{filesText(row)}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Bytes</dt>
        <dd>{bytesText(row)}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Engine</dt>
        <dd>{row.engine || "not reported"}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Duration</dt>
        <dd>{row.duration_ms == null ? "not reported" : formatDuration(row.duration_ms)}</dd>
      </div>
      {row.committed_snapshot_id ? (
        <div>
          <dt className="text-xs text-muted-foreground">Committed snapshot</dt>
          <dd>{row.committed_snapshot_id}</dd>
        </div>
      ) : null}
      {failure ? (
        <div className="sm:col-span-2">
          <dt className="text-xs text-muted-foreground">Error</dt>
          <dd className="text-[var(--color-status-danger)]">{failure}</dd>
        </div>
      ) : null}
    </dl>
  );
};

const gradeText = (run: TableRunRead | null): string => {
  const before = run?.before_stats?.grade;
  const after = run?.after_stats?.grade;
  if (!before && !after) return "not reported";
  return `${before ?? "not reported"} → ${after ?? "not reported"}`;
};

const resourceText = (run: TableRunRead | null): string => {
  const estimate = run?.resource_estimate;
  if (!estimate) return "not reported";
  const parts: string[] = [];
  if (estimate.engine) parts.push(estimate.engine);
  if (estimate.vcpu_hours != null) parts.push(`${estimate.vcpu_hours} vCPU-h`);
  if (estimate.memory_gib_hours != null) parts.push(`${estimate.memory_gib_hours} GiB-h`);
  return parts.length > 0 ? parts.join(", ") : "not reported";
};

const RunGroup = ({ block }: { block: Extract<HistoryBlock, { kind: "run" }> }) => {
  const [open, setOpen] = useState(false);
  const runId = historyRunId(block);
  const panelId = `maintenance-history-${block.join.kind}-${block.join.id}`;
  const when = block.receipts[0]?.ts || block.receipts[0]?.created_at;
  return (
    <li className="rounded-[var(--radius-default)] border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="text-sm font-medium text-foreground underline-offset-2 hover:underline"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide receipts" : "Show receipts"}
        </button>
        {runId ? (
          <Link to={`/runs/${runId}`} className="text-sm text-primary hover:underline">
            Open run
          </Link>
        ) : (
          <span className="text-sm text-muted-foreground">Run not in the recent list</span>
        )}
        <span className="text-xs text-muted-foreground">{when ? formatTime(when) : "Time not reported"}</span>
        {block.run?.status ? <span className="text-xs text-muted-foreground">{block.run.status}</span> : null}
      </div>
      {open ? (
        <div id={panelId} className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">Grade {gradeText(block.run)}</p>
          <p className="text-xs text-muted-foreground">Resources {resourceText(block.run)}</p>
          <ul className="space-y-3">
            {block.receipts.map((row, index) => (
              <li key={row.id == null ? `${block.join.id}-${index}` : String(row.id)} className="border-t border-border pt-3">
                <ReceiptFields row={row} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  );
};

const pageSearch = (params: URLSearchParams, page: number): string => {
  const next = new URLSearchParams(params);
  if (page <= 1) next.delete("history_page");
  else next.set("history_page", String(page));
  const query = next.toString();
  return query ? `?${query}` : "";
};

export const MaintenanceHistory = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const [searchParams] = useSearchParams();
  const page = historyPageFromSearch(searchParams.get("history_page"));
  const offset = (page - 1) * HISTORY_PAGE_SIZE;
  const enabled = tableId.length > 0;
  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token, HISTORY_PAGE_SIZE, offset),
    queryFn: () => getTableAudit(api, tableId, { limit: HISTORY_PAGE_SIZE, offset }),
    enabled,
    retry: false
  });
  const runs = useQuery({
    queryKey: tableKeys.runs(tableId, token, RECENT_RUNS_LIMIT, 0),
    queryFn: () => getTableRuns(api, tableId, { limit: RECENT_RUNS_LIMIT, offset: 0 }),
    enabled,
    retry: false
  });
  const blocks = useMemo(
    () => groupAuditReceipts((audit.data ?? []) as MaintenanceAuditRow[], runs.data ?? []),
    [audit.data, runs.data]
  );
  const fullPage = (audit.data?.length ?? 0) === HISTORY_PAGE_SIZE;

  return (
    <section className="mb-6" aria-labelledby="maintenance-history">
      <h3 id="maintenance-history" className="mb-2 text-sm font-semibold text-foreground">
        History
      </h3>
      <QueryErrorAlert error={audit.error} title="Couldn't load maintenance history" />
      <QueryErrorAlert error={runs.error} title="Couldn't load runs for history" />
      {audit.isLoading ? <p className="text-sm text-muted-foreground">Loading history</p> : null}
      {!audit.isLoading && blocks.length === 0 ? (
        <EmptyState
          title="No receipts recorded"
          description="Nothing is inferred from missing runs."
        />
      ) : null}
      {blocks.length > 0 ? (
        <ul className="space-y-3">
          {blocks.map((block, index) =>
            block.kind === "ungrouped" ? (
              <li key={block.row.id == null ? `ungrouped-${index}` : `ungrouped-${block.row.id}`} className="rounded-[var(--radius-default)] border border-border p-3">
                <p className="mb-2 text-sm text-muted-foreground">Run link not recorded</p>
                <ReceiptFields row={block.row} />
              </li>
            ) : (
              <RunGroup key={`${block.join.kind}:${block.join.id}`} block={block} />
            )
          )}
        </ul>
      ) : null}
      {page > 1 || fullPage ? (
        <div className="mt-3 flex gap-3 text-sm">
          {page > 1 ? (
            <Link to={{ search: pageSearch(searchParams, page - 1) }} className="text-primary hover:underline">
              Previous page
            </Link>
          ) : null}
          {fullPage ? (
            <Link to={{ search: pageSearch(searchParams, page + 1) }} className="text-primary hover:underline">
              Next page
            </Link>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};
