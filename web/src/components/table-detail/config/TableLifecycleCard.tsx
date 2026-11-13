import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { getTable, putTableLifecycle } from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { formatShortDate } from "../../../lib/format";
import type { WriteAccess } from "../../../lib/permissions";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { ARCHIVED_TOOLTIP, LifecycleBadge } from "../../LifecycleBadge";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../ui/dialog";
import { Input } from "../../ui/input";
import { Textarea } from "../../ui/textarea";
import { MIN_REASON_LENGTH } from "./ConfigReviewDialog";

export const LIFECYCLE_SECTION_ID = "tc-lifecycle";

export interface TableLifecycleCardProps {
  tableId: string;
  access: WriteAccess;
}

/** Table lifecycle (issue 519): mark a backup or retired table archived, or return it to active. */
export const TableLifecycleCard = ({ tableId, access }: TableLifecycleCardProps) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const table = useQuery({ queryKey: tableKeys.detail(tableId, token), queryFn: () => getTable(api, tableId) });
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const row = table.data;
  const archived = row?.lifecycle === "archived";
  const target = archived ? "active" : "archived";
  const tableName = row?.table_name ?? "";

  const save = useMutation({
    mutationFn: () => putTableLifecycle(api, tableId, { lifecycle: target, reason: reason.trim(), confirm: typed }),
    onSuccess: (result) => {
      toast.success(result.message);
      setOpen(false);
      setReason("");
      setTyped("");
      void queryClient.invalidateQueries({ queryKey: tableKeys.detail(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: tableKeys.pathToA(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: ["table-audit", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : "Couldn't change the lifecycle")
  });

  if (!row) return null;
  const valid = reason.trim().length >= MIN_REASON_LENGTH && typed === tableName;

  return (
    <section id={LIFECYCLE_SECTION_ID} className="tc-group" aria-labelledby="tc-lifecycle-title">
      <header className="tc-group-head">
        <div>
          <h3 id="tc-lifecycle-title" className="flex items-center gap-2">
            Lifecycle <LifecycleBadge lifecycle={row.lifecycle} />
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {archived
              ? ARCHIVED_TOOLTIP
              : "Active. Mark a backup, snapshot copy or retired table as archived so it isn't graded for having no recent commits."}
          </p>
          {archived && row.lifecycle_changed_at ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Archived {formatShortDate(row.lifecycle_changed_at)}
              {row.lifecycle_changed_by ? ` by ${row.lifecycle_changed_by}` : ""}
              {row.lifecycle_reason ? ` · ${row.lifecycle_reason}` : ""}
            </p>
          ) : null}
          {!archived && row.archive_candidate ? (
            <Badge variant="warning" className="mt-2" data-testid="archive-candidate">
              Suggested: no commits in 90+ days and no reads in 30
            </Badge>
          ) : null}
        </div>
        <span className="tc-group-meta">
          <Button
            size="sm"
            variant="secondary"
            disabled={!access.allowed}
            title={access.allowed ? undefined : access.reason ?? undefined}
            onClick={() => setOpen(true)}
          >
            {archived ? (
              <ArchiveRestore size={14} aria-hidden="true" className="mr-1.5" />
            ) : (
              <Archive size={14} aria-hidden="true" className="mr-1.5" />
            )}
            {archived ? "Return to active" : "Mark as archived"}
          </Button>
        </span>
      </header>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{archived ? "Return to active?" : "Mark as archived?"}</DialogTitle>
            <DialogDescription>
              {archived
                ? "Compaction, layout changes and the stale-commit check apply again from the next plan."
                : "The stale-commit check stops applying. Compaction, delete rewrites, data expiry and layout changes stop. Snapshot expiry, orphan cleanup and manifest rewrites keep running. Nothing in the table changes."}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span>Reason (audited)</span>
              <Textarea
                aria-label="Reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={archived ? "Writes resumed" : "Nightly backup copy, read-only"}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span>
                Type <strong>{tableName}</strong> to confirm
              </span>
              <Input
                aria-label={`Type ${tableName} to confirm`}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
              />
            </label>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
              {archived ? "Return to active" : "Mark as archived"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
};
