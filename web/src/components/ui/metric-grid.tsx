import { type ReactNode } from "react";
import { cn } from "../../lib/cn";

export type MetricGridColumns = 2 | 3 | 4 | 5;

const columnClass: Record<MetricGridColumns, string> = {
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
  5: "grid-cols-2 sm:grid-cols-4 lg:grid-cols-5"
};

export interface MetricGridProps {
  children: ReactNode;
  columns?: MetricGridColumns;
  className?: string;
}

/** Zone B supporting-metrics row — one tile component per cell, no hover lift. */
export function MetricGrid({ children, columns = 4, className }: MetricGridProps) {
  return (
    <div className={cn("grid gap-3", columnClass[columns], className)} data-metric-grid={columns}>
      {children}
    </div>
  );
}

export { Stat as MetricTile, type StatProps as MetricTileProps } from "./stat";
