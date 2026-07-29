import { type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Skeleton } from "./skeleton";

export interface StatProps {
  label: string;
  value: ReactNode;
  suffix?: string;
  delta?: {
    value: string;
    tone?: "good" | "bad" | "neutral";
  };
  hint?: ReactNode;
  sparkline?: ReactNode;
  emphasis?: boolean;
  loading?: boolean;
  className?: string;
}

const deltaToneClass = {
  good: "text-[var(--color-status-success)]",
  bad: "text-[var(--color-status-danger)]",
  neutral: "text-muted-foreground"
} as const;

export function Stat({
  label,
  value,
  suffix,
  delta,
  hint,
  sparkline,
  emphasis = false,
  loading = false,
  className
}: StatProps) {
  if (loading) {
    return (
      <div className={cn("rounded-[var(--radius-default)] border border-border bg-card p-4", className)}>
        <Skeleton className="h-3 w-20" />
        <Skeleton className="mt-2 h-6 w-24" />
        <Skeleton className="mt-2 h-3 w-16" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-[var(--radius-default)] border border-border bg-card p-4",
        emphasis && "border-l-[3px] border-l-[var(--color-primary)]",
        className
      )}
    >
      <div className="flex items-center gap-1.5">
        <p className="text-[11px] font-medium leading-4 text-muted-foreground">{label}</p>
        {hint}
      </div>
      <div className="mt-1 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xl font-semibold leading-7 text-foreground [font-variant-numeric:tabular-nums]">
            {value}
            {suffix ? <span className="ml-1 text-sm font-normal text-muted-foreground">{suffix}</span> : null}
          </p>
          {delta ? (
            <p className={cn("mt-1 text-xs [font-variant-numeric:tabular-nums]", deltaToneClass[delta.tone ?? "neutral"])}>
              {delta.value}
            </p>
          ) : null}
        </div>
        {sparkline ? <div className="shrink-0">{sparkline}</div> : null}
      </div>
    </div>
  );
}

export { Stat as MetricTile };
