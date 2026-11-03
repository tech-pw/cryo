import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getTableSpecMigration, requestSpecMigrationApproval, runTableOperations } from "../../../api/client";
import type { SpecMigrationRead } from "../../../api/types";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";

const plural = (count: number, noun: string) => `${count.toLocaleString()} ${noun}${count === 1 ? "" : "s"}`;

/** Share of the baseline already migrated, 0–100, or null with no baseline to compare to. */
export const migrationProgress = (data: Pick<SpecMigrationRead, "baseline_old_spec_partitions" | "old_spec_partitions">) => {
  const baseline = data.baseline_old_spec_partitions;
  if (baseline == null || baseline <= 0) return null;
  const done = Math.max(0, baseline - data.old_spec_partitions);
  return Math.min(100, Math.round((done / baseline) * 100));
};

const lastRunTone = (status: string) => {
  const key = status.toUpperCase();
  if (key === "OK") return "success" as const;
  if (key === "FAILED" || key === "ERROR") return "danger" as const;
  return "secondary" as const;
};

export const SpecMigrationCard = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const access = useWriteControl("runMaintenance");
  const migration = useQuery({
    queryKey: tableKeys.specMigration(tableId, token),
    queryFn: () => getTableSpecMigration(api, tableId),
    enabled: tableId.length > 0
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["table-spec-migration", tableId] });
  const requestApproval = useMutation({
    mutationFn: () => requestSpecMigrationApproval(api, tableId),
    onSuccess: () => {
      toast.success("Approval requested. An editor approves it under Decisions.");
      void queryClient.invalidateQueries({ queryKey: ["pending-approvals"] });
      refresh();
    },
    onError: (error: Error) => toast.error(error.message)
  });
  const run = useMutation({
    mutationFn: () => runTableOperations(api, tableId, { operations: ["migrate_partition_spec"], trigger: "web-spec-migration" }),
    onSuccess: () => {
      toast.success("Migration run started.");
      void queryClient.invalidateQueries({ queryKey: ["table-runs", tableId] });
      refresh();
    },
    onError: (error: Error) => toast.error(error.message)
  });

  if (migration.isError) return <QueryErrorAlert error={migration.error} title="Couldn't load partition spec migration" />;
  const data = migration.data;
  if (!data || !data.recorded) return null;
  if (data.old_spec_partitions === 0 && !data.last_run) return null;

  const progress = migrationProgress(data);
  const approval = data.approval;
  const approved = approval?.status === "approved";
  const blockedReason = !access.allowed ? access.reason : !data.engine_supported ? data.engine_note : null;
  const done = data.old_spec_partitions === 0;

  return (
    <section className="td-mt-card mb-6" aria-labelledby="spec-migration-title">
      <div className="td-mt-card-head">
        <p className="td-mt-kicker">Partition spec migration</p>
        {approval && !approved ? (
          <Link className="td-mt-config-link" to="/decisions?tab=approvals#operation-approvals">
            Review in Decisions <ArrowUpRight size={13} aria-hidden="true" />
          </Link>
        ) : null}
      </div>
      <h3 id="spec-migration-title" className="td-mt-status" data-eligible={done || undefined}>
        {done ? "Every partition is on the current spec" : "Migrate to current partition spec"}
      </h3>
      <p className="td-mt-lede">
        {done
          ? `All data files are written under partition spec ${data.default_spec_id}.`
          : `${plural(data.old_spec_partitions, "partition")} (${plural(data.old_spec_files, "data file")}, ${formatBytesGb(data.old_spec_bytes)}) are still written under an older partition spec. Routine compaction picks partitions by small files and leaves these in the old layout.`}
      </p>
      {progress != null ? (
        <div className="mt-3" aria-label="Migration progress">
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-sunken)]"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
            aria-label={`${progress}% of old-spec partitions migrated`}
          >
            <div className="h-full rounded-full bg-[var(--color-status-success)]" style={{ width: `${progress}%` }} />
          </div>
          <p className="mt-1 text-xs text-muted-foreground [font-variant-numeric:tabular-nums]">
            {progress}% migrated · {plural(data.old_spec_partitions, "partition")} left of {data.baseline_old_spec_partitions?.toLocaleString()} when
            approval was requested. Counts update after the next scan.
          </p>
        </div>
      ) : null}
      <dl className="td-mt-facts">
        <div>
          <dt>Default spec</dt>
          <dd>{data.default_spec_id ?? "Unknown"}</dd>
        </div>
        <div>
          <dt>Scanned</dt>
          <dd>{data.scanned_at ? formatTime(data.scanned_at) : "Unknown"}</dd>
        </div>
        <div>
          <dt>Engine</dt>
          <dd>{data.destination_engine ?? "No workflow"}</dd>
        </div>
        <div>
          <dt>Approval</dt>
          <dd>{approval ? (approved ? `Approved by ${approval.approved_by ?? "an editor"}` : "Pending") : "Not requested"}</dd>
        </div>
      </dl>
      {data.last_run ? (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          Last migration run
          <Badge variant={lastRunTone(data.last_run.status)}>{data.last_run.status}</Badge>
          {formatTime(data.last_run.at)}
        </p>
      ) : null}
      {data.engine_note ? <p className="td-mt-lede">{data.engine_note}</p> : null}
      {!done ? (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {approved ? (
            <Button variant="primary" size="sm" disabled={Boolean(blockedReason) || run.isPending} title={blockedReason ?? undefined} onClick={() => run.mutate()}>
              {run.isPending ? "Starting…" : "Run migration"}
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={Boolean(blockedReason) || Boolean(approval) || requestApproval.isPending}
              title={blockedReason ?? undefined}
              onClick={() => requestApproval.mutate()}
            >
              {approval ? "Waiting for approval" : requestApproval.isPending ? "Requesting…" : "Request approval"}
            </Button>
          )}
          <p className="text-xs text-muted-foreground">
            Rewrites the old-spec partitions in budgeted chunks on a Spark destination. One approval covers one run.
          </p>
        </div>
      ) : null}
    </section>
  );
};
