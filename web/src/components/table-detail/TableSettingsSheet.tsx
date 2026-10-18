import { CheckCircle2, Clock3, Lock, ShieldOff, X } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { useWriteControl } from "../../lib/useWriteControl";
import { AsyncBoundary } from "../AsyncBoundary";
import { formatNumber, formatTime } from "../../lib/format";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { MaintenanceHoldSection } from "./MaintenanceHoldSection";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle
} from "../ui/sheet";

const TONE_CLASS = {
  ok: "text-[var(--color-status-success)]",
  warn: "text-[var(--color-status-warning)]",
  muted: "text-muted-foreground"
} as const;

const ComplianceRow = ({
  icon,
  tone,
  title,
  children
}: {
  icon: ReactNode;
  tone: "ok" | "warn" | "muted";
  title: ReactNode;
  children?: ReactNode;
}) => (
  <li className="flex gap-3 rounded-[var(--radius-default)] border border-border p-3">
    <span className={`mt-0.5 shrink-0 ${TONE_CLASS[tone]}`} aria-hidden="true">
      {icon}
    </span>
    <div className="min-w-0">
      <p className="text-sm font-medium">{title}</p>
      {children ? <div className="mt-0.5 text-xs text-muted-foreground">{children}</div> : null}
    </div>
  </li>
);

interface ComplianceBlockProps {
  tableId: string;
  pendingRows: number;
  blockedUntil: string | null;
  protectedCount: number;
  abacCount: number;
  abacNote: string | null;
  onNavigate: () => void;
}

const ComplianceBlock = ({
  tableId,
  pendingRows,
  blockedUntil,
  protectedCount,
  abacCount,
  abacNote,
  onNavigate
}: ComplianceBlockProps) => {
  const snapshotsLink = (label: string) => (
    <Link className="underline" to={`/tables/${tableId}?tab=snapshots#excluded-from-expiry`} onClick={onNavigate}>
      {label}
    </Link>
  );
  return (
    <ul className="space-y-2">
      {pendingRows > 0 ? (
        <ComplianceRow
          icon={<Clock3 className="size-4" />}
          tone="warn"
          title={`${formatNumber(pendingRows)} deleted row${pendingRows === 1 ? "" : "s"} still readable`}
        >
          Older snapshots still hold them. They are gone for good when snapshot expiry removes those snapshots
          {blockedUntil ? <>, by {formatTime(blockedUntil)}</> : null}.
          {protectedCount > 0 ? (
            <> {formatNumber(protectedCount)} protected snapshot{protectedCount === 1 ? "" : "s"} may keep them longer. </>
          ) : null}
        </ComplianceRow>
      ) : (
        <ComplianceRow icon={<CheckCircle2 className="size-4" />} tone="ok" title="No deleted rows waiting">
          Rows deleted from this table are not held by any snapshot Cryo knows about.
        </ComplianceRow>
      )}
      <ComplianceRow
        icon={<Lock className="size-4" />}
        tone={protectedCount > 0 ? "warn" : "muted"}
        title={
          protectedCount > 0
            ? `${formatNumber(protectedCount)} snapshot${protectedCount === 1 ? "" : "s"} protected from expiry`
            : "No protected snapshots"
        }
      >
        {protectedCount > 0
          ? <>Expiry skips them, along with every file and deleted row they reference. {snapshotsLink("Review on Snapshots")}</>
          : <>Protect a snapshot on the {snapshotsLink("Snapshots tab")} to keep it past expiry.</>}
      </ComplianceRow>
      <ComplianceRow
        icon={<ShieldOff className="size-4" />}
        tone={abacCount > 0 ? "warn" : "muted"}
        title={abacCount > 0 ? `${formatNumber(abacCount)} access polic${abacCount === 1 ? "y" : "ies"} recorded, not enforced` : "No access policies"}
      >
        {abacCount > 0
          ? abacNote
          : "Row filters and column masks (ABAC) are enforced by your catalog or engine, not Cryo."}
      </ComplianceRow>
    </ul>
  );
};

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
  const pendingRows = compliance.data?.pending_deletion?.pending_rows ?? 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Table settings</SheetTitle>
          <SheetDescription>
            Who owns this table, who hears about it, what is holding deleted data, and whether maintenance is paused.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          <section className="space-y-3" aria-labelledby="settings-compliance">
            <h4 id="settings-compliance" className="text-sm font-semibold">Compliance &amp; protection</h4>
            <AsyncBoundary
              isLoading={compliance.isPending}
              isError={Boolean(compliance.error) && !complianceNotFound}
              error={compliance.error}
              isEmpty={complianceNotFound}
              emptyTitle="No compliance data yet"
              emptyDescription="This appears after the first scan."
            >
              <ComplianceBlock
                tableId={tableId}
                pendingRows={pendingRows}
                blockedUntil={compliance.data?.pending_deletion?.blocked_until ?? null}
                protectedCount={compliance.data?.protected_snapshot_count ?? 0}
                abacCount={compliance.data?.abac_policy_count ?? 0}
                abacNote={compliance.data?.abac_enforcement_note ?? null}
                onNavigate={() => onOpenChange(false)}
              />
            </AsyncBoundary>
          </section>

          <MaintenanceHoldSection tableId={tableId} schemaName={row?.schema_name ?? "this"} />

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
