import { X } from "lucide-react";
import { Link } from "react-router-dom";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { useWriteControl } from "../../lib/useWriteControl";
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
    tableId,
    compliance,
    complianceNotFound,
    ownerEmailInput,
    setOwnerEmailInput,
    setOwner,
    alertSubscriptions,
    newSubscriberEmail,
    setNewSubscriberEmail,
    addSubscriber,
    removeSubscriber
  } = useTableDetailContext();

  const ownerAccess = useWriteControl("patchTable");

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
              isLoading={compliance.isPending}
              isError={Boolean(compliance.error)}
              error={compliance.error}
              isEmpty={compliance.isFetched && !compliance.data && !compliance.error}
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

            <p className="text-xs text-muted-foreground">
              Snapshot exclusion lives on the{" "}
              <Link className="underline" to={`/tables/${tableId}?tab=snapshots#excluded-from-expiry`} onClick={() => onOpenChange(false)}>
                Snapshots
              </Link>{" "}
              tab. Spark expire keeps each recorded id. Trino expire cannot skip one id.
            </p>
          </section>

          <section className="space-y-3 border-t border-border pt-6">
            <h4 className="text-sm font-semibold">Ownership</h4>
            <label className="text-xs text-muted-foreground" htmlFor="owner-email">
              Owner email
            </label>
            <div className="flex gap-1.5">
              <Input
                id="owner-email"
                type="email"
                autoComplete="email"
                aria-label="Owner email"
                value={ownerEmailInput}
                onChange={(event) => setOwnerEmailInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && ownerAccess.allowed && !setOwner.isPending) {
                    setOwner.mutate(ownerEmailInput.trim());
                  }
                }}
                placeholder={row?.owner_email || "owner@example.com"}
                disabled={!ownerAccess.allowed || setOwner.isPending}
              />
              <Button
                size="sm"
                loading={setOwner.isPending}
                disabled={!ownerAccess.allowed}
                title={ownerAccess.reason ?? undefined}
                onClick={() => setOwner.mutate(ownerEmailInput.trim())}
              >
                Save
              </Button>
            </div>
            {!ownerAccess.allowed && ownerAccess.reason ? (
              <p className="text-xs text-muted-foreground">{ownerAccess.reason}</p>
            ) : null}
          </section>

          <section className="space-y-3 border-t border-border pt-6">
            <h4 className="text-sm font-semibold">Alert subscribers</h4>
            <p className="text-xs text-muted-foreground">
              The owner address is the email recipient for SLA and quality breaches. Slack, if configured, is a shared
              channel, not a message to this address.
            </p>
            <div className="space-y-1">
              {alertSubscriptions.isPending ? (
                <p className="text-xs text-muted-foreground">Loading subscribers…</p>
              ) : null}
              {alertSubscriptions.isError ? (
                <p className="text-xs text-destructive">Couldn't load subscribers.</p>
              ) : null}
              {(alertSubscriptions.data ?? []).map((sub) => (
                <div key={sub.id} className="flex items-center justify-between text-sm">
                  <span>{sub.email}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    aria-label={`Remove subscriber ${sub.email}`}
                    loading={removeSubscriber.isPending}
                    disabled={!ownerAccess.allowed}
                    title={ownerAccess.reason ?? undefined}
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
                disabled={!ownerAccess.allowed}
                onChange={(event) => setNewSubscriberEmail(event.target.value)}
                placeholder="consumer@example.com"
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || !newSubscriberEmail.trim()) return;
                  const email = newSubscriberEmail.trim();
                  addSubscriber.mutate(email, { onSuccess: () => setNewSubscriberEmail("") });
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                loading={addSubscriber.isPending}
                disabled={!ownerAccess.allowed}
                title={ownerAccess.reason ?? undefined}
                onClick={() => {
                  if (!newSubscriberEmail.trim()) return;
                  const email = newSubscriberEmail.trim();
                  addSubscriber.mutate(email, { onSuccess: () => setNewSubscriberEmail("") });
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
