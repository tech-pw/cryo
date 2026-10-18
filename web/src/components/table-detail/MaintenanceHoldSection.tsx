import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { getTableHold, releaseTableHold, setTableHold } from "../../api/client";
import type { TableHoldRead } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { useWriteControl } from "../../lib/useWriteControl";
import { tableKeys } from "../../pages/table-detail/tableKeys";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Segmented } from "../ui/segmented";

const DURATIONS = [
  { label: "1 day", value: "1" },
  { label: "3 days", value: "3" },
  { label: "1 week", value: "7" },
  { label: "2 weeks", value: "14" },
  { label: "30 days", value: "30" },
  { label: "90 days", value: "90" }
];

const HoldSummary = ({ hold, schemaName }: { hold: TableHoldRead; schemaName: string }) => (
  <div className="flex gap-3 rounded-[var(--radius-default)] border border-[var(--color-status-warning)] p-3">
    <PauseCircle className="mt-0.5 size-4 shrink-0 text-[var(--color-status-warning)]" aria-hidden="true" />
    <div className="min-w-0 text-xs text-muted-foreground">
      <p className="text-sm font-medium text-foreground">
        {hold.scope === "schema" ? `Held with the whole ${schemaName} schema` : "Maintenance on hold"}
      </p>
      {hold.reason ? <p className="mt-0.5">“{hold.reason}”</p> : null}
      <p className="mt-0.5">
        {hold.created_by ? <>Set by {hold.created_by}</> : "Set"}
        {hold.created_at ? <> on {formatTime(hold.created_at)}</> : null}.{" "}
        {hold.expires_at ? (
          <>Scheduled runs pick the table up again after {formatTime(hold.expires_at)}.</>
        ) : (
          <>No end date. It stays until someone releases it.</>
        )}
      </p>
      {hold.scope === "schema" ? (
        <p className="mt-0.5">
          This rule covers every table in the schema, so it can't be released from one table.
        </p>
      ) : null}
    </div>
  </div>
);

/**
 * A hold stops scheduled and manual maintenance for this table until it expires or is released.
 * Scans keep running, so the health grade still tracks what the hold is costing.
 */
export const MaintenanceHoldSection = ({ tableId, schemaName }: { tableId: string; schemaName: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const access = useWriteControl("holdMaintenance");
  const [reason, setReason] = useState("");
  const [days, setDays] = useState("7");

  const hold = useQuery({
    queryKey: tableKeys.hold(tableId, token),
    queryFn: () => getTableHold(api, tableId)
  });

  const onSaved = (next: TableHoldRead, message: string) => {
    queryClient.setQueryData(tableKeys.hold(tableId, token), next);
    void queryClient.invalidateQueries({ queryKey: ["table-maintenance-status", tableId] });
    toast.success(message);
  };

  const place = useMutation({
    mutationFn: () =>
      setTableHold(api, tableId, {
        reason: reason.trim(),
        expires_at: new Date(Date.now() + Number(days) * 86_400_000).toISOString()
      }),
    onSuccess: (next) => {
      setReason("");
      onSaved(next, "Maintenance on hold");
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const release = useMutation({
    mutationFn: () => releaseTableHold(api, tableId),
    onSuccess: (next) => onSaved(next, "Hold released"),
    onError: (error: Error) => toast.error(error.message)
  });

  const current = hold.data;
  const ownHold = current?.held && current.scope === "table";
  const reasonValid = reason.trim().length >= 3;

  return (
    <section className="space-y-3 border-t border-border pt-6" aria-labelledby="settings-hold">
      <h4 id="settings-hold" className="text-sm font-semibold">Maintenance hold</h4>
      <p className="text-xs text-muted-foreground">
        Pause compaction and cleanup during a backfill, migration or incident. Scans keep running. Every hold
        is audited and ends on its own after at most {current?.max_hold_days ?? 90} days.
      </p>
      {hold.isPending ? <p className="text-xs text-muted-foreground">Loading hold…</p> : null}
      {hold.isError ? <p className="text-xs text-destructive">Couldn't load the hold.</p> : null}
      {current?.held ? <HoldSummary hold={current} schemaName={schemaName} /> : null}

      {ownHold ? (
        <Button
          size="sm"
          variant="secondary"
          loading={release.isPending}
          disabled={!access.allowed}
          title={access.reason ?? undefined}
          onClick={() => release.mutate()}
        >
          Release hold
        </Button>
      ) : current && !current.held ? (
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground" htmlFor="hold-reason">
            Reason
          </label>
          <Input
            id="hold-reason"
            value={reason}
            maxLength={500}
            disabled={!access.allowed || place.isPending}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Backfill of 2026-09 in progress"
          />
          <p className="text-xs text-muted-foreground">
            Hold for
          </p>
          <Segmented
            size="small"
            aria-label="Hold duration"
            value={days}
            onChange={setDays}
            options={DURATIONS}
          />
          <div>
            <Button
              size="sm"
              loading={place.isPending}
              disabled={!access.allowed || !reasonValid}
              title={access.reason ?? (reasonValid ? undefined : "Say why, so the next person knows when to release it.")}
              onClick={() => place.mutate()}
            >
              Put on hold
            </Button>
          </div>
        </div>
      ) : null}
      {!access.allowed && access.reason ? <p className="text-xs text-muted-foreground">{access.reason}</p> : null}
    </section>
  );
};
