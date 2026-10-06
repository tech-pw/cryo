import { useEffect, useId, useState } from "react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Segmented } from "../ui/segmented";
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

  return (
    <section
      aria-label="Maintenance batch"
      className={cn("flex h-full min-h-0 flex-col gap-4 bg-background p-4 text-foreground", highlightClass, className)}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id={titleId} className="text-base font-semibold tracking-tight">
            Maintenance batch
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{membershipSummary(draft)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => persist(clearMembership(draft))}>
            Clear
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href="/runs/batch">Open full review</a>
          </Button>
        </div>
      </header>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-xs font-medium text-muted-foreground">Recipe</span>
          {destinationLabel ? <span>on {destinationLabel}</span> : null}
          {presetLabel ? <span className="text-muted-foreground">(preset: {presetLabel})</span> : null}
        </div>
        <Segmented
          size="small"
          value={draft.recipe}
          onChange={(value) => persist(withRecipe(draft, value as BatchRecipeId))}
          options={BATCH_RECIPE_IDS.map((id) => ({ value: id, label: BATCH_RECIPES[id].label }))}
        />
        <div role="list" aria-label="Operations" className="flex flex-wrap items-center gap-1">
          {operations.map((operation, index) => (
            <span key={operation} className="inline-flex items-center gap-1">
              {index > 0 ? (
                <span aria-hidden="true" className="text-xs text-muted-foreground">
                  →
                </span>
              ) : null}
              <Badge role="listitem" variant="secondary" data-testid="batch-operation">
                {operation}
              </Badge>
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p aria-live="polite" className="text-sm">
          Ready {ready} · Blocked {blocked} · Nothing to do {nothingToDo}
        </p>
        {blocked > 0 ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => setShowBlocked((open) => !open)}>
            {showBlocked ? "Hide blocked" : "Show blocked"}
          </Button>
        ) : null}
      </div>
      {showBlocked && blocked > 0 ? (
        <ul className="space-y-1 text-sm text-muted-foreground">
          {blockers.length === 0 ? <li>No blocker details yet.</li> : null}
          {blockers.map((blocker) => (
            <li key={blocker.id}>
              {blocker.label}: {blocker.reason}
            </li>
          ))}
        </ul>
      ) : null}

      <p aria-label="Resources" className="text-sm">
        {sparkJobsLabel(ready)}
      </p>
      <p aria-label="Expected" className="text-sm text-muted-foreground">
        Rewrite: {estimateText(rewriteEstimate)} · Reclaim: {estimateText(reclaimEstimate)}
      </p>

      <ul className="min-h-0 flex-1 space-y-1 overflow-auto text-sm">
        {draft.tables.length === 0 && draft.receipts.length === 0 ? (
          <li className="text-muted-foreground">No tables in this batch.</li>
        ) : null}
        {draft.tables.map((table) => (
          <li key={table.id} className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate">{table.label}</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Remove ${table.label}`}
              onClick={() => persist(excludeFromDraft(draft, table.id))}
            >
              Remove
            </Button>
          </li>
        ))}
        {draft.receipts.map((receipt) => (
          <li key={receipt.id} className="text-muted-foreground">
            {receipt.label}
            {receipt.declaredCount != null ? ` · ${receipt.declaredCount}` : " · Resolving…"}
          </li>
        ))}
      </ul>
      {draft.exclusions.length > 0 ? (
        <p className="text-xs text-muted-foreground">{draft.exclusions.length} excluded</p>
      ) : null}
      {ready === 0 && nothingToDo > 0 ? <p className="text-sm">Nothing needs work.</p> : null}

      <div className="flex flex-col gap-2">
        <Button
          type="button"
          variant="primary"
          disabled={ready === 0}
          aria-describedby={admissionId}
          onClick={() => {
            if (ready === 0) return;
            onRun?.(draft);
          }}
        >
          {runButtonLabel(ready)}
        </Button>
        <p id={admissionId} className="text-xs text-muted-foreground">
          Enters normal admission; may wait for capacity.
        </p>
      </div>
    </section>
  );
}

function finiteReady(count: number): number {
  if (!Number.isFinite(count) || count <= 0) return 0;
  return Math.trunc(count);
}

export const emptyBatchDraft = createEmptyDraft;
