import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { EmptyState } from "./empty-state";
import { Skeleton } from "./skeleton";

export interface SimpleTableColumn<T> {
  title: ReactNode;
  key?: string;
  dataIndex?: keyof T & string;
  width?: number | string;
  align?: "left" | "right" | "center";
  ellipsis?: boolean;
  render?: (value: unknown, row: T, index: number) => ReactNode;
}

export interface SimpleTableProps<T> {
  columns: SimpleTableColumn<T>[];
  dataSource: T[];
  rowKey: (row: T, index: number) => string;
  loading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
  size?: "small" | "default";
}

export function SimpleTable<T>({
  columns,
  dataSource,
  rowKey,
  loading,
  emptyTitle = "No data",
  emptyDescription,
  className,
  size = "default"
}: SimpleTableProps<T>) {
  if (loading) {
    return <Skeleton className="h-32 w-full" />;
  }

  if (!dataSource.length) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className={cn("td-schema-table w-full", size === "small" && "text-xs")}>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th
                key={column.key ?? column.dataIndex ?? `col-${index}`}
                style={{ width: column.width, textAlign: column.align }}
              >
                {column.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dataSource.map((row, rowIndex) => (
            <tr key={rowKey(row, rowIndex)}>
              {columns.map((column, colIndex) => {
                const value = column.dataIndex ? row[column.dataIndex] : undefined;
                return (
                  <td
                    key={column.key ?? column.dataIndex ?? `col-${colIndex}`}
                    style={{ textAlign: column.align }}
                    className={column.ellipsis ? "max-w-[16rem] truncate" : undefined}
                  >
                    {column.render ? column.render(value, row, rowIndex) : String(value ?? "—")}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
