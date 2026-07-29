import { X } from "lucide-react";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { formatTime } from "../../lib/format";
import { AsyncBoundary } from "../AsyncBoundary";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { PendingDeletionTag } from "../status";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle
} from "../ui/sheet";

interface TableSettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Edit actions for the low-frequency, setup-once settings that used to occupy a permanent
 * sidebar on every tab ("Compliance & Protection", "Ownership & Alerts"). Read-only summaries
 * of the same data live as compact cards on the Overview tab; this Sheet is where an operator
 * actually changes something -- reusing the "click -> Sheet with detail" pattern already
 * established for partition-row and job-log detail elsewhere on this page.
 */
export const TableSettingsSheet = ({ open, onOpenChange }: TableSettingsSheetProps) => {
  const {
    row,
    role,
    compliance,
    complianceNotFound,
    protect,
    protectedLocal,
    protectedFromAudit,
    ownerEmailInput,
    setOwnerEmailInput,
    setOwner,
    alertSubscriptions,
    newSubscriberEmail,
    setNewSubscriberEmail,
    addSubscriber,
    removeSubscriber
  } = useTableDetailContext();

  const canEdit = role === "admin" || role === "operator";
  const protectedItems: Array<{ snapshot_id: number; label?: string | null; ts?: string | null }> = [
    ...protectedLocal,
    ...protectedFromAudit
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Table settings</SheetTitle>
          <SheetDescription>Compliance, protection, ownership, and alert subscribers.</SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          <section className="space-y-3">
            <h4 className="text-sm font-semibold">Compliance &amp; protection</h4>
            <AsyncBoundary
              isLoading={compliance.isLoading}
              isError={Boolean(compliance.error) && !complianceNotFound}
              error={compliance.error}
              isEmpty={complianceNotFound || !compliance.data}
              emptyTitle="No deletion-tracking data yet"
              emptyDescription="This table has no compliance events yet."
            >
              <dl className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <dt className="text-muted-foreground">ABAC count</dt>
                  <dd>
                    <Badge variant="default">{compliance.data?.abac_policy_count ?? 0}</Badge>
                  </dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-muted-foreground">Deletion delay</dt>
                  <dd>
                    <PendingDeletionTag pending={Boolean(compliance.data?.pending_deletion)} />
                  </dd>
                </div>
              </dl>
            </AsyncBoundary>

            {canEdit ? (
              <form
                className="space-y-1.5 border-t border-border pt-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  const formData = new FormData(event.currentTarget);
                  const snapshotId = Number(formData.get("snapshot_id"));
                  const label = String(formData.get("label") || "");
                  if (!snapshotId) return;
                  protect.mutate({ snapshot_id: snapshotId, label: label || undefined });
                  event.currentTarget.reset();
                }}
              >
                <p className="text-xs font-medium text-muted-foreground">Protect snapshot</p>
                <div className="flex gap-1.5">
                  <Input name="snapshot_id" type="number" min={1} placeholder="Snapshot ID" className="w-28" required />
                  <Input name="label" placeholder="Label (optional)" className="flex-1" />
                  <Button type="submit" size="sm" loading={protect.isPending}>
                    Protect
                  </Button>
                </div>
              </form>
            ) : null}

            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Protected snapshots</p>
              {protectedItems.filter((item) => item.snapshot_id > 0).length === 0 ? (
                <p className="text-xs text-muted-foreground">No protected snapshots recorded yet.</p>
              ) : (
                protectedItems
                  .filter((item) => item.snapshot_id > 0)
                  .slice(0, 8)
                  .map((item, index) => (
                    <div key={`${item.snapshot_id}-${index}`} className="flex items-center justify-between text-xs">
                      <span>
                        #{item.snapshot_id}
                        {item.label ? <span className="ml-1 text-muted-foreground">({item.label})</span> : null}
                      </span>
                      {item.ts ? <span className="text-muted-foreground">{formatTime(item.ts)}</span> : null}
                    </div>
                  ))
              )}
            </div>
          </section>

          <section className="space-y-3 border-t border-border pt-6">
            <h4 className="text-sm font-semibold">Ownership</h4>
            {canEdit ? (
              <div className="flex gap-1.5">
                <Input
                  value={ownerEmailInput}
                  onChange={(event) => setOwnerEmailInput(event.target.value)}
                  placeholder="owner@example.com"
                />
                <Button size="sm" loading={setOwner.isPending} onClick={() => setOwner.mutate(ownerEmailInput.trim())}>
                  Save
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{row?.owner_email || "No owner assigned"}</p>
            )}
          </section>

          <section className="space-y-3 border-t border-border pt-6">
            <h4 className="text-sm font-semibold">Alert subscribers</h4>
            <p className="text-xs text-muted-foreground">
              Owner and subscribers get an email (and Slack, if configured) when this table breaches its SLA or
              data-quality thresholds.
            </p>
            <div className="space-y-1">
              {(alertSubscriptions.data ?? []).map((sub) => (
                <div key={sub.id} className="flex items-center justify-between text-sm">
                  <span>{sub.email}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    aria-label={`Remove subscriber ${sub.email}`}
                    loading={removeSubscriber.isPending}
                    onClick={() => removeSubscriber.mutate(sub.id)}
                  >
                    <X className="size-3" />
                  </Button>
                </div>
              ))}
              {alertSubscriptions.data && alertSubscriptions.data.length === 0 ? (
                <p className="text-xs text-muted-foreground">No subscribers yet.</p>
              ) : null}
            </div>
            <div className="flex gap-1.5">
              <Input
                value={newSubscriberEmail}
                onChange={(event) => setNewSubscriberEmail(event.target.value)}
                placeholder="consumer@example.com"
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || !newSubscriberEmail.trim()) return;
                  addSubscriber.mutate(newSubscriberEmail.trim());
                  setNewSubscriberEmail("");
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                loading={addSubscriber.isPending}
                onClick={() => {
                  if (!newSubscriberEmail.trim()) return;
                  addSubscriber.mutate(newSubscriberEmail.trim());
                  setNewSubscriberEmail("");
                }}
              >
                Subscribe
              </Button>
            </div>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
};
