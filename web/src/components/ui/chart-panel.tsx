import { useEffect, useRef, useState, type ReactNode } from "react";
import { ResponsiveContainer } from "recharts";
import { cn } from "../../lib/cn";
import { EmptyState } from "./empty-state";

export type ChartPanelSize = "xs" | "sm" | "md" | "lg";

const chartHeights: Record<ChartPanelSize, number> = {
  xs: 168,
  sm: 220,
  md: 260,
  lg: 320
};

export interface ChartFrameProps {
  empty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  ariaLabel?: string;
  size?: ChartPanelSize;
  className?: string;
  children?: ReactNode;
}

/** Responsive Recharts host — use inside ChartPanel or standalone for legacy chart call sites. */
export function ChartFrame({
  empty = false,
  emptyTitle = "No chart data",
  emptyDescription = "Run a scan to populate this panel.",
  ariaLabel,
  size = "md",
  className,
  children
}: ChartFrameProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const measure = () => setReady(el.clientWidth > 0 && el.clientHeight > 0);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [empty]);

  if (empty) {
    return <EmptyState title={emptyTitle} description={emptyDescription} className={className} />;
  }

  return (
    <div
      ref={hostRef}
      className={cn(
        "chart-frame overflow-hidden text-foreground",
        size === "xs" && "chart-frame xs",
        size === "sm" && "chart-frame compact",
        className
      )}
      style={{ height: chartHeights[size] }}
      role="img"
      aria-label={ariaLabel}
    >
      {ready ? <ResponsiveContainer>{children}</ResponsiveContainer> : null}
    </div>
  );
}

export interface ChartPanelProps extends Omit<ChartFrameProps, "className"> {
  title?: string;
  description?: string;
  action?: ReactNode;
  footer?: ReactNode;
  className?: string;
  bodyClassName?: string;
}

/** Zone C chart shell — shared title, padding, empty state, and Recharts frame. */
export function ChartPanel({
  title,
  description,
  action,
  footer,
  empty,
  emptyTitle,
  emptyDescription,
  ariaLabel,
  size = "md",
  className,
  bodyClassName,
  children
}: ChartPanelProps) {
  const hasHeader = Boolean(title || description || action);

  return (
    <section
      className={cn(
        "rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-1)]",
        className
      )}
    >
      {hasHeader ? (
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {title ? (
              <h3 className="text-[13px] font-semibold leading-5 text-foreground">{title}</h3>
            ) : null}
            {description ? (
              <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            ) : null}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </header>
      ) : null}

      <div className={cn("px-4 py-3", bodyClassName)}>
        <ChartFrame
          empty={empty}
          emptyTitle={emptyTitle}
          emptyDescription={emptyDescription}
          ariaLabel={ariaLabel}
          size={size}
        >
          {children}
        </ChartFrame>
      </div>

      {footer ? (
        <footer className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">{footer}</footer>
      ) : null}
    </section>
  );
}
