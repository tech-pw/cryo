import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { listProtectedSnapshots, unprotectTableSnapshot } from "../../api/snapshots";
import { ConfirmAction } from "../confirm";
import { Button } from "../ui/button";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { useWriteControl } from "../../lib/useWriteControl";
import { tableKeys } from "../../pages/table-detail/tableKeys";

const copyId = async (snapshotId: string) => {
  try {
    await navigator.clipboard.writeText(snapshotId);
    toast.success("Copied snapshot ID");
  } catch {
    toast.error("Couldn't copy the snapshot ID");
  }
};

export const ExcludedFromExpiry = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const unprotectAccess = useWriteControl("unprotectSnapshot");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const excluded = useQuery({
    queryKey: tableKeys.protectedSnapshots(tableId, token),
    queryFn: () => listProtectedSnapshots(api, tableId),
    enabled: tableId.length > 0
  });
  const unprotect = useMutation({
    mutationFn: (snapshotId: string) => unprotectTableSnapshot(api, tableId, snapshotId),
    onSuccess: (_result, snapshotId) => {
      toast.success(`Removed snapshot ${snapshotId} from Cryo expiry exclusion`);
      void queryClient.invalidateQueries({ queryKey: tableKeys.protectedSnapshots(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: ["table-snapshots", tableId] });
    },
    onError: (error) => toast.error((error as Error).message),
    onSettled: () => setPendingId(null)
  });
  const rows = excluded.data?.snapshots ?? [];

  return (
    <section className="mb-3" aria-labelledby="excluded-from-expiry">
      <h3 id="excluded-from-expiry" className="text-sm font-semibold">
        Excluded from Cryo expiry
      </h3>
      <p className="mb-2 text-xs text-muted-foreground">
        On Spark, Cryo keeps each one by tagging it <code>cryo-protect-{"{id}"}</code>. Trino's expiry can't skip a single
        snapshot, so on Trino destinations Cryo skips expire_snapshots for this table while any exclusion exists, and the
        plan says why. Removing a row doesn't drop a tag already created.
      </p>
      {excluded.isError ? (
        <p className="text-xs text-destructive">
          Couldn't load the exclusion list.{" "}
          <button type="button" className="underline" onClick={() => void excluded.refetch()}>
            Retry
          </button>
        </p>
      ) : excluded.isLoading ? (
        <p className="text-xs text-muted-foreground">Loading expiry exclusions…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">No snapshots are excluded from Cryo expiry.</p>
      ) : (
        <ul className="divide-y divide-border rounded-[var(--radius-default)] border border-border">
          {rows.map((row) => (
            <li key={row.snapshot_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs">
              <div className="min-w-0">
                <p className="font-mono break-all">{row.snapshot_id}</p>
                <p className="text-muted-foreground">
                  {row.label || "No label"} · {row.protected_by || "Protector not recorded"} ·{" "}
                  {row.protected_at ? formatTime(row.protected_at) : "time not recorded"}
                </p>
              </div>
              <div className="flex gap-1">
                <Button type="button" size="sm" variant="ghost" onClick={() => void copyId(row.snapshot_id)}>
                  Copy ID
                </Button>
                <ConfirmAction
                  title={`Remove snapshot ${row.snapshot_id} from Cryo expiry exclusion?`}
                  description="Cryo may expire it on the next Spark run if no tag or retention rule keeps it. A tag already created is not dropped here. This does not restore or delete data."
                  okText="Remove exclusion"
                  onConfirm={() => {
                    setPendingId(row.snapshot_id);
                    unprotect.mutate(row.snapshot_id);
                  }}
                >
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={!unprotectAccess.allowed || pendingId === row.snapshot_id}
                  >
                    Remove exclusion
                  </Button>
                </ConfirmAction>
                {unprotectAccess.reason ? (
                  <p className="text-xs text-muted-foreground">{unprotectAccess.reason}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
