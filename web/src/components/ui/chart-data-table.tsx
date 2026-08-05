import { type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface ChartDataTableColumn<T> {
  key: string;
  header: string;
  align?: "left" | "right";
  render: (row: T, index: number) => ReactNode;
}

export interface ChartDataTableProps<T> {
  /** Screen-reader/table caption -- describe what the chart above shows in plain language. */
  caption: string;
  columns: ChartDataTableColumn<T>[];
  rows: T[];
  getRowKey: (row: T, index: number) => string;
  /** Visible label for the disclosure toggle. Defaults to "View data as table". */
  toggleLabel?: string;
  className?: string;
}

/**
 * Accessible, always-in-the-DOM table equivalent for a chart. Every trend/comparison chart
 * should pair with one of these so the same values are available without color, hover, or
 * pointer input -- native `<details>` keeps the disclosure keyboard-operable and screen-reader
 * friendly without hand-rolling ARIA state.
 */
export function ChartDataTable<T>({
  caption,
  columns,
  rows,
  getRowKey,
  toggleLabel = "View data as table",
  className
}: ChartDataTableProps<T>) {
  if (!rows.length) return null;

  return (
    <details className={cn("chart-data-table mt-2 text-xs", className)}>
      <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">
        {toggleLabel}
      </summary>
      <div className="mt-2 overflow-x-auto rounded-[var(--radius-sm)] border border-border">
        <table className="w-full min-w-full border-collapse text-[12px]">
          <caption className="sr-only">{caption}</caption>
          <thead className="bg-[var(--color-table-header)]">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    "px-2.5 py-1.5 font-medium text-muted-foreground",
                    column.align === "right" ? "text-right" : "text-left"
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={getRowKey(row, index)} className="border-t border-border/70">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      "px-2.5 py-1.5 text-foreground [font-variant-numeric:tabular-nums]",
                      column.align === "right" ? "text-right" : "text-left"
                    )}
                  >
                    {column.render(row, index)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
