import { Bell, Bot, LayoutDashboard, X, Zap } from "lucide-react";
import { AsyncBoundary } from "../AsyncBoundary";
import { ConfirmAction } from "../confirm";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Badge } from "../ui/badge";
import { PendingDeletionTag } from "../status";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { formatTime } from "../../lib/format";
import { provenanceLabel } from "./helpers";
import { SidebarCard } from "./SidebarCard";

export const TableDetailSidebar = () => {
  const ctx = useTableDetailContext();
  const {
    row,
    stats,
    role,
    effectivePreset,
    assignPresetOptions,
    assign,
    unassign,
    presets,
    compatiblePresets,
    lastSnapshotCommittedAt,
    queuedMaintenanceRunId,
    run,
    handleRunAction,
    setPreviewOpen,
    manifestRewrite,
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
  } = ctx;

  const protectedItems: Array<{ snapshot_id: number; label?: string | null; ts?: string | null }> = [
    ...protectedLocal,
    ...protectedFromAudit
  ];

  const presetLoading =
    assign.isPending || presets.isLoading || compatiblePresets.isLoading || unassign.isPending;

  return (
    <div className="td-sidebar-sticky">
      <SidebarCard title="Preset & Engine">
        <div className="td-sidebar-meta-list">
          <div
            className="td-sidebar-meta-item"
            style={{ flexDirection: "column", alignItems: "stretch", gap: 6, marginBottom: 12 }}
          >
            <span className="td-sidebar-meta-label">Assign Preset</span>
            <select
              className="flex h-8 w-full rounded-[var(--radius-default)] border border-border bg-background px-2 text-xs"
              value={effectivePreset.data?.preset_id ?? ""}
              disabled={!row || presetLoading}
              onChange={(e) => {
                const presetId = e.target.value;
                if (presetId) assign.mutate(presetId);
                else unassign.mutate();
              }}
            >
              <option value="">Assign preset</option>
              {assignPresetOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            {effectivePreset.data?.provenance ? (
              <span className="td-meta-faint" style={{ fontSize: 10 }}>
                Active: <strong>{effectivePreset.data?.preset_name}</strong> (
                {provenanceLabel(effectivePreset.data?.provenance)})
              </span>
            ) : null}
          </div>

          {stats?.scan_at ? (
            <div className="td-sidebar-meta-item">
              <span className="td-sidebar-meta-label">Last scan</span>
              <span className="td-sidebar-meta-value">{formatTime(stats.scan_at)}</span>
            </div>
          ) : null}
          {lastSnapshotCommittedAt ? (
            <div className="td-sidebar-meta-item">
              <span className="td-sidebar-meta-label">Last commit</span>
              <span className="td-sidebar-meta-value">{formatTime(lastSnapshotCommittedAt)}</span>
            </div>
          ) : null}

          <div
            style={{
              marginTop: 14,
              paddingTop: 14,
              borderTop: "1.5px solid var(--line, #e1e6ed)",
              display: "flex",
              flexDirection: "column",
              gap: 8
            }}
          >
            {queuedMaintenanceRunId || run.isPending ? (
              <Button
                variant="primary"
                size="sm"
                className="w-full"
                loading={run.isPending}
                onClick={handleRunAction}
                disabled={!row || run.isPending || !queuedMaintenanceRunId}
                aria-label={run.isPending ? "Queueing run" : "Open maintenance run"}
              >
                <Zap className="size-3.5" />
                {run.isPending ? "Queueing run..." : "Open maintenance run"}
              </Button>
            ) : (
              <ConfirmAction
                title="Run maintenance?"
                description="Queues a real maintenance job using the assigned preset and destination."
                onConfirm={() => run.mutate()}
              >
                <Button
                  variant="primary"
                  size="sm"
                  className="w-full"
                  disabled={!row || !effectivePreset.data?.preset_id}
                  aria-label="Run maintenance"
                >
                  <Zap className="size-3.5" />
                  Run maintenance
                </Button>
              </ConfirmAction>
            )}
            <div className="flex gap-1.5">
              <Button
                variant="secondary"
                size="sm"
                className="flex-1 text-[11px]"
                onClick={() => setPreviewOpen(true)}
                disabled={!row || !effectivePreset.data?.preset_id}
              >
                <LayoutDashboard className="size-3.5" />
                Preview
              </Button>
              <ConfirmAction
                title="Rewrite manifests?"
                description="Queues manifest-only maintenance without data file compaction."
                onConfirm={() => manifestRewrite.mutate()}
              >
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1 text-[11px]"
                  loading={manifestRewrite.isPending}
                  disabled={!row}
                >
                  <Bot className="size-3.5" />
                  Manifest
                </Button>
              </ConfirmAction>
            </div>
          </div>
        </div>
      </SidebarCard>

      <SidebarCard title="Compliance & Protection">
        <div className="td-sidebar-compliance-wrap">
          <AsyncBoundary
            isLoading={compliance.isLoading}
            isError={Boolean(compliance.error) && !complianceNotFound}
            error={compliance.error}
            isEmpty={complianceNotFound || !compliance.data}
            emptyTitle="No deletion-tracking data yet"
            emptyDescription="This table has no compliance events yet. ABAC and deletion-delay details will appear after governance policies or delete workflows are applied."
          >
            <dl className="td-meta-desc space-y-2 text-sm">
              <div>
                <dt className="text-muted-foreground">Table Ref</dt>
                <dd className="td-sidebar-desc-val">{compliance.data?.table_ref}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">ABAC Count</dt>
                <dd>
                  <Badge variant="default">{compliance.data?.abac_policy_count ?? 0}</Badge>
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Deletion delay</dt>
                <dd>
                  <PendingDeletionTag pending={Boolean(compliance.data?.pending_deletion)} />
                </dd>
              </div>
              {compliance.data?.abac_enforcement_note ? (
                <div>
                  <dt className="text-muted-foreground">ABAC Note</dt>
                  <dd className="td-sidebar-desc-val">{compliance.data.abac_enforcement_note}</dd>
                </div>
              ) : null}
              {compliance.data?.pending_deletion ? (
                <div>
                  <dt className="text-muted-foreground">Deletion Blocks</dt>
                  <dd className="td-sidebar-desc-val">
                    {compliance.data.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                    {compliance.data.pending_deletion.blocked_until
                      ? ` until ${formatTime(compliance.data.pending_deletion.blocked_until)}`
                      : ""}
                  </dd>
                </div>
              ) : null}
            </dl>
          </AsyncBoundary>

          {role === "admin" || role === "operator" ? (
            <div
              className="td-sidebar-protect-form"
              style={{ marginTop: 14, paddingTop: 14, borderTop: "1.5px solid var(--line, #e1e6ed)" }}
            >
              <p className="mb-1.5 text-[11px] font-semibold">Protect Snapshot</p>
              <form
                className="space-y-1.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  const fd = new FormData(e.currentTarget);
                  const snapshot_id = Number(fd.get("snapshot_id"));
                  const label = String(fd.get("label") || "");
                  if (!snapshot_id) return;
                  protect.mutate({ snapshot_id, label: label || undefined });
                  e.currentTarget.reset();
                }}
              >
                <div className="grid grid-cols-12 gap-1.5">
                  <Input
                    name="snapshot_id"
                    type="number"
                    min={1}
                    placeholder="ID"
                    className="col-span-5 h-8 text-xs"
                    required
                  />
                  <Input name="label" placeholder="Label" className="col-span-7 h-8 text-xs" />
                </div>
                <Button type="submit" variant="primary" size="sm" className="w-full" loading={protect.isPending}>
                  Protect Snapshot
                </Button>
              </form>
            </div>
          ) : null}

          <div className="td-protected-list" style={{ marginTop: 14 }}>
            <p className="mb-1 text-[11px] font-semibold">Protected Snapshots</p>
            {protectedItems
              .filter((item) => item.snapshot_id > 0)
              .slice(0, 8)
              .map((item, index) => (
                <div
                  className="td-protected-row"
                  key={`${item.snapshot_id}-${index}`}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    fontSize: 11,
                    marginBottom: 4
                  }}
                >
                  <div>
                    <Badge variant="default" className="mr-1.5">●</Badge>
                    <span style={{ fontWeight: 600 }}>#{item.snapshot_id}</span>
                    {item.label ? (
                      <span style={{ marginLeft: 4, color: "var(--muted, #5c667a)", fontSize: 10 }}>
                        ({item.label})
                      </span>
                    ) : null}
                  </div>
                  {item.ts ? (
                    <span className="text-[10px] text-muted-foreground">{formatTime(item.ts)}</span>
                  ) : null}
                </div>
              ))}
            {!protectedLocal.length && !protectedFromAudit.length ? (
              <p className="text-[11px] text-muted-foreground">No protected snapshots recorded yet.</p>
            ) : null}
          </div>
        </div>
      </SidebarCard>

      <SidebarCard
        title={
          <span>
            <Bell className="mr-1.5 inline size-3.5" />
            Ownership &amp; Alerts
          </span>
        }
      >
        <div className="td-sidebar-alerts-wrap">
          <p className="mb-1.5 text-[11px] font-semibold">Owner</p>
          {role === "admin" || role === "operator" ? (
            <div className="mb-3.5 flex gap-1">
              <Input
                value={ownerEmailInput}
                onChange={(e) => setOwnerEmailInput(e.target.value)}
                placeholder="owner@example.com"
                className="h-8 text-xs"
              />
              <Button
                size="sm"
                variant="primary"
                loading={setOwner.isPending}
                onClick={() => setOwner.mutate(ownerEmailInput.trim())}
              >
                Save
              </Button>
            </div>
          ) : (
            <p className="mb-3.5 text-xs text-muted-foreground">{row?.owner_email || "No owner assigned"}</p>
          )}

          <p className="mb-1.5 text-[11px] font-semibold">Alert subscribers</p>
          <p className="mb-2 text-[10.5px] text-muted-foreground">
            Owner and subscribers get an email (and Slack, if configured) when this table breaches its SLA or
            data-quality thresholds.
          </p>

          <div style={{ marginBottom: 8 }}>
            {(alertSubscriptions.data ?? []).map((sub) => (
              <div
                key={sub.id}
                className="mb-1 flex items-center justify-between text-[11px]"
              >
                <span>{sub.email}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0"
                  aria-label={`Remove subscriber ${sub.email}`}
                  loading={removeSubscriber.isPending}
                  onClick={() => removeSubscriber.mutate(sub.id)}
                >
                  <X className="size-2.5" />
                </Button>
              </div>
            ))}
            {alertSubscriptions.data && alertSubscriptions.data.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No subscribers yet.</p>
            ) : null}
          </div>

          <div className="flex gap-1">
            <Input
              value={newSubscriberEmail}
              onChange={(e) => setNewSubscriberEmail(e.target.value)}
              placeholder="consumer@example.com"
              className="h-8 text-xs"
              onKeyDown={(e) => {
                if (e.key !== "Enter" || !newSubscriberEmail.trim()) return;
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
        </div>
      </SidebarCard>
    </div>
  );
};
