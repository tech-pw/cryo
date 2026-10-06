import { useState } from "react";
import { Button } from "../ui/button";
import { cn } from "../../lib/cn";
import {
  addMatchingFilter,
  addTables,
  type AddMatchingInput,
  type BatchAddResult,
  type BatchIdentity,
  type BatchTableRef
} from "../../lib/maintenanceBatch";

export interface AddToBatchButtonProps {
  identity: BatchIdentity;
  tables?: readonly BatchTableRef[];
  matching?: AddMatchingInput;
  disabled?: boolean;
  className?: string;
  onResult?: (result: BatchAddResult) => void;
  onReview?: () => void;
}

export function AddToBatchButton({
  identity,
  tables,
  matching,
  disabled,
  className,
  onResult,
  onReview
}: AddToBatchButtonProps) {
  const [result, setResult] = useState<BatchAddResult | null>(null);
  const label = buttonLabel(tables, matching);
  const inactive = matching ? matching.declaredCount === 0 : !tables?.length;

  return (
    <div className={cn("inline-flex flex-col items-start gap-1", className)}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={disabled || inactive}
        onClick={() => {
          const next = matching ? addMatchingFilter(identity, matching) : addTables(identity, tables ?? []);
          setResult(next);
          onResult?.(next);
        }}
      >
        {label}
      </Button>
      {result && !result.ok ? (
        <p role="alert" className="text-xs text-[var(--color-status-danger)]">
          {result.message}
        </p>
      ) : null}
      {result?.ok && (result.added > 0 || result.alreadyIn > 0) ? (
        <p role="status" className="text-xs text-muted-foreground">
          {result.added} added · {result.alreadyIn} already in batch
          {onReview ? (
            <Button type="button" variant="ghost" size="sm" className="ml-1 h-auto px-1" onClick={onReview}>
              Review batch
            </Button>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

function buttonLabel(tables: readonly BatchTableRef[] | undefined, matching: AddMatchingInput | undefined): string {
  if (matching) {
    return typeof matching.declaredCount === "number"
      ? `Add all ${matching.declaredCount} matching to batch`
      : "Add all matching to batch";
  }
  if ((tables?.length ?? 0) > 1) return "Add selected to batch";
  return "Add to batch";
}
