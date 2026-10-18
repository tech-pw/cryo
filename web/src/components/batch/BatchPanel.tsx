import { useEffect, useId, useState } from "react";
import { ArrowRight, Layers, X } from "lucide-react";
import { Button } from "../ui/button";
import { Segmented } from "../ui/segmented";
import { OPERATION_CATALOG, type RunnableOperation } from "../table-detail/operations/operationCatalog";
import { cn } from "../../lib/cn";
import { useReducedMotion } from "../../lib/motion";
import { MAINTENANCE_OPERATION_ORDER as CORRECT_ORDER } from "../../lib/operationOrder";
import {
  BATCH_RECIPES,
  BATCH_RECIPE_IDS,
  MAINTENANCE_BATCH_CHANGE_EVENT,
  batchHighlightClass,
  clearMembership,
  createEmptyDraft,
  estimateText,
  excludeFromDraft,
  loadDraft,
  membershipSummary,
  runButtonLabel,
  saveDraft,
  sparkJobsLabel,
  withRecipe,
  type BatchIdentity,
  type BatchRecipeId,
  type MaintenanceBatchDraft
} from "../../lib/maintenanceBatch";

export interface BatchBlocker {
  id: string;
  label: string;
  reason: string;
}

export interface BatchPanelProps {
  identity: BatchIdentity;
  draft?: MaintenanceBatchDraft;
  readyCount?: number;
  blockedCount?: number;
  nothingToDoCount?: number;
  rewriteEstimate?: string | number | null;
  reclaimEstimate?: string | number | null;
  destinationLabel?: string;
  presetLabel?: string;
  blockers?: readonly BatchBlocker[];
  pulse?: boolean;
  className?: string;
  onDraftChange?: (draft: MaintenanceBatchDraft) => void;
  onRun?: (draft: MaintenanceBatchDraft) => void;
}

export function BatchPanel({
  identity,
  draft: draftProp,
  readyCount,
  blockedCount = 0,
  nothingToDoCount = 0,
  rewriteEstimate,
  reclaimEstimate,
  destinationLabel,
  presetLabel,
  blockers = [],
  pulse = false,
  className,
  onDraftChange,
  onRun
}: BatchPanelProps) {
  const reducedMotion = useReducedMotion();
  const titleId = useId();
  const admissionId = useId();
  const [draft, setDraft] = useState<MaintenanceBatchDraft>(() => draftProp ?? loadDraft(identity));
  const [showBlocked, setShowBlocked] = useState(false);
  const ready = finiteReady(readyCount ?? draft.tables.length);
  const blocked = finiteReady(blockedCount);
  const nothingToDo = finiteReady(nothingToDoCount);
  const highlightClass = batchHighlightClass(reducedMotion, pulse);
  const operations = CORRECT_ORDER.filter((operation) => BATCH_RECIPES[draft.recipe].operations.includes(operation));

  useEffect(() => {
    if (draftProp) {
      setDraft(draftProp);
      return;
    }
    setDraft(loadDraft(identity));
  }, [draftProp, identity.installationId, identity.principalId, identity.tenantId]);

  useEffect(() => {
    if (draftProp) return;
    const reload = () => setDraft(loadDraft(identity));
    window.addEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
    return () => window.removeEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
  }, [draftProp, identity]);

  const persist = (next: MaintenanceBatchDraft) => {
    setDraft(next);
    if (!draftProp) saveDraft(next);
    onDraftChange?.(next);
  };

  const empty = draft.tables.length === 0 && draft.receipts.length === 0;
  const rewrite = estimateText(rewriteEstimate);
  const reclaim = estimateText(reclaimEstimate);

  return (
    <section
      aria-label="Maintenance batch"
      className={cn(
        "flex h-full min-h-0 flex-col bg-background text-foreground",
        highlightClass,
        className
      )}
    >
      <header className="flex items-end justify-between gap-3 px-4 pb-4 pt-3">
        <div className="min-w-0">
          <h2 id={titleId} className="text-[1.375rem] font-semibold leading-none tracking-[-0.03em]">
            Maintenance batch
          </h2>
          <p className="mt-1.5 text-[13px] leading-5 text-muted-foreground">{membershipSummary(draft)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button type="button" variant="ghost" size="sm" disabled={empty} onClick={() => persist(clearMembership(draft))}>
            Clear
          </Button>
          <Button asChild variant="ghost" size="sm">
            <a href="/runs/batch">
              Review
              <ArrowRight aria-hidden="true" />
            </a>
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 pb-5">
        <section aria-label="Batch status">
          <div className="flex items-end gap-5">
            <StatusFigure label="Ready" value={ready} tone={ready > 0 ? "ready" : "quiet"} live />
            <StatusFigure label="Blocked" value={blocked} tone={blocked > 0 ? "blocked" : "quiet"} />
            <StatusFigure label="Nothing to do" value={nothingToDo} tone="quiet" />
          </div>
        </section>
        {blocked > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-mt-3 self-start"
            aria-expanded={showBlocked}
            onClick={() => setShowBlocked((open) => !open)}
          >
            {showBlocked ? "Hide blocked" : "Show blocked"}
          </Button>
        ) : null}
        {showBlocked && blocked > 0 ? (
          <ul className="-mt-3 space-y-1.5 rounded-[var(--radius-lg)] bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)] px-3 py-2 text-[13px] leading-5">
            {blockers.length === 0 ? <li className="text-muted-foreground">No blocker details yet.</li> : null}
            {blockers.map((blocker) => (
              <li key={blocker.id}>
                <span className="font-medium">{blocker.label}</span>
                <span className="text-muted-foreground"> · {blocker.reason}</span>
              </li>
            ))}
          </ul>
        ) : null}

        <section>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h3 className="text-[13px] font-semibold tracking-[-0.01em]">Recipe</h3>
            {destinationLabel || presetLabel ? (
              <p className="text-[12px] leading-4 text-muted-foreground">
                {destinationLabel ? <span className="text-foreground">{destinationLabel}</span> : null}
                {destinationLabel && presetLabel ? " · " : null}
                {presetLabel}
              </p>
            ) : null}
          </div>
          <Segmented
            size="small"
            value={draft.recipe}
            onChange={(value) => persist(withRecipe(draft, value as BatchRecipeId))}
            options={BATCH_RECIPE_IDS.map((id) => ({ value: id, label: BATCH_RECIPES[id].label }))}
          />
          <ol aria-label="Operations" className="mt-3 flex flex-col">
            {operations.map((operation, index) => {
              const title = operationTitle(operation);
              return (
                <li key={operation} className="flex gap-2.5">
                  <span className="flex w-4 shrink-0 flex-col items-center" aria-hidden="true">
                    <span
                      className={cn(
                        "mt-[0.45rem] size-1.5 rounded-full",
                        index === 0 ? "bg-primary" : "bg-[var(--color-border-strong)]"
                      )}
                    />
                    {index < operations.length - 1 ? <span className="mt-1 w-px flex-1 bg-[var(--color-border)]" /> : null}
                  </span>
                  <span className="min-w-0 pb-2.5 text-[13px] leading-5 text-foreground">{title ?? operation}</span>
                  <span className="sr-only" data-testid="batch-operation">
                    {operation}
                  </span>
                </li>
              );
            })}
          </ol>
        </section>

        <p className="text-[12px] leading-5 text-muted-foreground">
          <span aria-label="Resources">{sparkJobsLabel(ready)}</span>
          <span aria-hidden="true"> · </span>
          <span aria-label="Expected">
            Rewrite: {rewrite} · Reclaim: {reclaim}
          </span>
        </p>

        <section aria-label="Tables in this batch">
          <h3 className="mb-2 text-[13px] font-semibold tracking-[-0.01em]">Tables</h3>
          {empty ? (
            <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
              <span className="flex size-9 items-center justify-center rounded-full bg-[var(--color-surface-sunken)] text-muted-foreground">
                <Layers className="size-4" aria-hidden="true" />
              </span>
              <p className="text-[13px] font-medium tracking-[-0.01em]">No tables in this batch.</p>
              <p className="max-w-[16rem] text-[12px] leading-5 text-muted-foreground">
                Add tables from the catalog, then run them together.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-[var(--color-border)] overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-border)]">
              {draft.tables.map((table) => (
                <li key={table.id} className="flex items-center gap-2 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-[13px] leading-5">{table.label}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${table.label}`}
                    className="flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:bg-[var(--color-surface-sunken)] hover:text-foreground focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring active:scale-[0.96] motion-reduce:transition-none motion-reduce:active:scale-100"
                    onClick={() => persist(excludeFromDraft(draft, table.id))}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                  </button>
                </li>
              ))}
              {draft.receipts.map((receipt) => (
                <li key={receipt.id} className="flex items-center justify-between gap-2 px-3 py-2 text-[13px] text-muted-foreground">
                  <span className="min-w-0 truncate">{receipt.label}</span>
                  <span className="shrink-0 tabular-nums">
                    {receipt.declaredCount != null ? receipt.declaredCount : "Resolving…"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {draft.exclusions.length > 0 ? (
            <p className="mt-2 text-[12px] text-muted-foreground">{draft.exclusions.length} excluded</p>
          ) : null}
          {ready === 0 && nothingToDo > 0 ? (
            <p className="mt-2 text-[13px] text-muted-foreground">Nothing needs work.</p>
          ) : null}
        </section>
      </div>

      <div className="border-t border-[color-mix(in_srgb,var(--color-border)_70%,transparent)] bg-[color-mix(in_srgb,var(--color-background)_82%,transparent)] px-4 py-3 backdrop-blur-xl supports-[backdrop-filter]:bg-[color-mix(in_srgb,var(--color-background)_72%,transparent)]">
        <Button
          type="button"
          variant="primary"
          className="h-11 w-full rounded-[var(--radius-lg)] text-[15px] tracking-[-0.01em]"
          disabled={ready === 0}
          aria-describedby={admissionId}
          onClick={() => {
            if (ready === 0) return;
            onRun?.(draft);
          }}
        >
          {runButtonLabel(ready)}
        </Button>
        <p id={admissionId} className="mt-2 text-center text-[12px] leading-4 text-muted-foreground">
          Enters normal admission; may wait for capacity.
        </p>
      </div>
    </section>
  );
}

function StatusFigure({
  label,
  value,
  tone,
  live = false
}: {
  label: string;
  value: number;
  tone: "ready" | "blocked" | "quiet";
  live?: boolean;
}) {
  return (
    <div className="min-w-0">
      <p
        className={cn(
          "text-[1.75rem] font-semibold leading-none tracking-[-0.04em] tabular-nums",
          tone === "ready" && "text-[var(--color-status-success)]",
          tone === "blocked" && "text-[var(--color-status-warning)]",
          tone === "quiet" && "text-foreground"
        )}
        aria-live={live ? "polite" : undefined}
      >
        {value}
      </p>
      <p className="mt-1 text-[12px] leading-4 text-muted-foreground">{label}</p>
    </div>
  );
}

function operationTitle(operation: string): string | null {
  if (!(operation in OPERATION_CATALOG)) return null;
  return OPERATION_CATALOG[operation as RunnableOperation].title;
}


function finiteReady(count: number): number {
  if (!Number.isFinite(count) || count <= 0) return 0;
  return Math.trunc(count);
}

export const emptyBatchDraft = createEmptyDraft;
