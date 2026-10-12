import { type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { metric, metricTooltip, provenanceLabel, type MetricId } from "../../lib/metrics";
import { Skeleton } from "./skeleton";

type StatBaseProps = {
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
  onClick?: () => void;
  pressed?: boolean;
};

export type StatProps = StatBaseProps &
  ({ label: string; metricId?: MetricId } | { label?: string; metricId: MetricId });

const deltaToneClass = {
  good: "text-[var(--color-status-success)]",
  bad: "text-[var(--color-status-danger)]",
  neutral: "text-muted-foreground"
} as const;

export function Stat({
  label,
  metricId,
  value,
  suffix,
  delta,
  hint,
  sparkline,
  emphasis = false,
  loading = false,
  className,
  onClick,
  pressed
}: StatProps) {
  const entry = metricId ? metric(metricId) : undefined;
  const shownLabel = entry?.label ?? label ?? "";
  const tooltip = entry ? metricTooltip(entry.id) : undefined;

  if (loading) {
    return (
      <div className={cn("cryo-metric-tile", className)} data-metric-id={metricId}>
        <Skeleton className="h-3 w-20" />
        <Skeleton className="mt-2 h-6 w-24" />
        <Skeleton className="mt-2 h-3 w-16" />
      </div>
    );
  }

  const tileClass = cn(
    "cryo-metric-tile",
    emphasis && "cryo-metric-tile--emphasis",
    onClick && "cryo-metric-tile--action",
    className
  );
  const Comp = onClick ? "button" : "div";

  return (
    <Comp
      type={onClick ? "button" : undefined}
      className={tileClass}
      data-metric-id={metricId}
      onClick={onClick}
      aria-pressed={onClick ? Boolean(pressed) : undefined}
    >
      <div className="flex items-center gap-1.5">
        <p
          className={cn(
            "text-[11px] font-semibold leading-4 tracking-[0.015em] text-muted-foreground",
            entry && "cursor-help"
          )}
          title={tooltip}
        >
          {shownLabel}
        </p>
        {entry ? (
          <span
            data-metric-provenance={entry.provenance}
            className="rounded-full border border-border px-1.5 text-[10px] font-medium leading-4 text-muted-foreground"
          >
            {provenanceLabel(entry.provenance)}
          </span>
        ) : null}
        {hint}
      </div>
      <div className="mt-1 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-2xl font-semibold leading-8 tracking-[-0.025em] text-foreground [font-variant-numeric:tabular-nums]">
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
    </Comp>
  );
}

export { Stat as MetricTile };
