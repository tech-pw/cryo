import { Check } from "lucide-react";
import { cn } from "../../lib/cn";

export type SourceDrawerStep = "connect" | "storage" | "review";

const STEPS: { id: SourceDrawerStep; label: string }[] = [
  { id: "connect", label: "Connect" },
  { id: "storage", label: "Storage" },
  { id: "review", label: "Review" }
];

/**
 * Terse numbered rail, not a progress bar — this is a connect flow inside an
 * operator tool, not a SaaS signup wizard. Completed steps stay clickable so an
 * operator can jump back to fix a field without losing later input.
 */
export const SourceDrawerSteps = ({
  current,
  furthestReached,
  onJump
}: {
  current: SourceDrawerStep;
  furthestReached: SourceDrawerStep;
  onJump: (step: SourceDrawerStep) => void;
}) => {
  const order = STEPS.map((s) => s.id);
  const furthestIndex = order.indexOf(furthestReached);

  return (
    <ol className="flex items-center gap-1" aria-label="Lakehouse setup progress">
      {STEPS.map((step, index) => {
        const isCurrent = step.id === current;
        const isDone = index < order.indexOf(current) || (isCurrent && index < furthestIndex);
        const isReachable = index <= furthestIndex;

        return (
          <li key={step.id} className="flex items-center gap-1">
            {index > 0 ? (
              <span
                aria-hidden="true"
                className={cn(
                  "h-px w-4 shrink-0 transition-colors duration-[var(--motion-base)]",
                  index <= furthestIndex ? "bg-[color:var(--color-status-success)]" : "bg-border"
                )}
              />
            ) : null}
            <button
              type="button"
              disabled={!isReachable}
              onClick={() => isReachable && onJump(step.id)}
              className={cn(
                "flex items-center gap-1.5 rounded-full py-1 pl-1 pr-2.5 text-xs font-medium transition-colors duration-[var(--motion-fast)]",
                isCurrent && "bg-[var(--color-surface-sunken)] text-foreground",
                !isCurrent && isReachable && "text-foreground/80 hover:text-foreground",
                !isReachable && "cursor-not-allowed text-muted-foreground/50"
              )}
              aria-current={isCurrent ? "step" : undefined}
            >
              <span
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums",
                  isDone && "bg-[color:var(--color-status-success)] text-white",
                  !isDone && isCurrent && "border border-foreground text-foreground",
                  !isDone && !isCurrent && "border border-border text-muted-foreground"
                )}
              >
                {isDone ? <Check className="size-2.5" strokeWidth={3} /> : index + 1}
              </span>
              {step.label}
            </button>
          </li>
        );
      })}
    </ol>
  );
};
