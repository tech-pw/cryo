import { FixedSizeList } from "react-window";
import { useMemo } from "react";
import type { ReactNode } from "react";

interface VirtualizedTableProps<T> {
  rows: T[];
  rowKey: (row: T, index: number) => string;
  rowHeight: number;
  height: number;
  renderRow: (row: T, index: number, style: React.CSSProperties) => ReactNode;
  emptyState?: ReactNode;
  /** Optional header rendered above the virtual list. */
  header?: ReactNode;
}

/**
 * Lightweight virtualized table for inventory lists that can exceed 1k rows.
 *
 * Renders only the rows visible in the viewport (plus a small overscan) so
 * scrolling stays smooth even with 10k+ rows. For rows < 100 the standard Ant
 * ``Table`` is preferable — use this only when virtual scrolling is needed.
 */
export function VirtualizedTable<T>({
  rows,
  rowKey,
  rowHeight,
  height,
  renderRow,
  emptyState,
  header
}: VirtualizedTableProps<T>) {
  const itemCount = rows.length;
  const itemKey = useMemo(() => (index: number) => rowKey(rows[index], index), [rows, rowKey]);

  if (itemCount === 0 && emptyState) {
    return <>{emptyState}</>;
  }

  return (
    <div className="virtualized-table">
      {header ? <div className="virtualized-table-header">{header}</div> : null}
      <FixedSizeList
        height={height}
        itemCount={itemCount}
        itemSize={rowHeight}
        width="100%"
        itemKey={itemKey}
        overscanCount={6}
      >
        {({ index, style }) => renderRow(rows[index], index, style)}
      </FixedSizeList>
    </div>
  );
}