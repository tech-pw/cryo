import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type OnChangeFn,
  type RowSelectionState,
  type TableOptions,
  type VisibilityState
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight, Columns3, Rows2, Rows3 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { Button } from "./button";
import { Checkbox } from "./checkbox";
import { EmptyState } from "./empty-state";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { Skeleton } from "./skeleton";

export type TableDensity = "default" | "compact";

export interface DataTablePagination {
  pageIndex: number;
  pageSize: number;
  totalRows: number;
  onPageChange: (pageIndex: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: number[];
}

export interface DataTableProps<TData> {
  columns: ColumnDef<TData, unknown>[];
  data: TData[];
  density?: TableDensity;
  onDensityChange?: (density: TableDensity) => void;
  showDensityToggle?: boolean;
  pagination?: DataTablePagination;
  loading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: ReactNode;
  getRowId?: TableOptions<TData>["getRowId"];
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: OnChangeFn<RowSelectionState>;
  enableRowSelection?: boolean | ((row: { original: TData }) => boolean);
  stickyFirstColumn?: boolean;
  toolbar?: ReactNode;
  className?: string;
  tableClassName?: string;
  getRowClassName?: (row: TData) => string | undefined;
  columnVisibility?: VisibilityState;
  onColumnVisibilityChange?: OnChangeFn<VisibilityState>;
  fillAvailableWidth?: boolean;
}

const rowHeightClass: Record<TableDensity, string> = {
  default: "h-[var(--table-row-height-default)]",
  compact: "h-[var(--table-row-height-compact)]"
};

/** Pin the leading identity column. With row selection, that is the checkbox plus the next column. */
function stickyColumnClass(columnIndex: number, selectionEnabled: boolean, layer: "header" | "body" | "footer"): string | undefined {
  const pinned = selectionEnabled ? columnIndex <= 1 : columnIndex === 0;
  if (!pinned) return undefined;
  const left = selectionEnabled && columnIndex === 1 ? "left-10" : "left-0";
  const edge = columnIndex === (selectionEnabled ? 1 : 0) ? "shadow-[1px_0_0_0_var(--color-border)]" : undefined;
  const surface =
    layer === "header" || layer === "footer"
      ? "z-20 bg-[var(--color-table-header)]"
      : "z-10 bg-card group-hover:bg-[var(--color-surface-sunken)] group-data-[state=selected]:bg-[var(--color-primary-soft)]";
  return cn("sticky", left, surface, edge);
}

function DensityToggle({
  density,
  onDensityChange
}: {
  density: TableDensity;
  onDensityChange: (density: TableDensity) => void;
}) {
  return (
    <div className="inline-flex rounded-[var(--radius-default)] border border-border p-0.5" role="group" aria-label="Table density">
      <Button
        type="button"
        size="sm"
        variant={density === "default" ? "secondary" : "ghost"}
        className="h-7 px-2"
        aria-pressed={density === "default"}
        onClick={() => onDensityChange("default")}
      >
        <Rows3 className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="sr-only">Default density</span>
      </Button>
      <Button
        type="button"
        size="sm"
        variant={density === "compact" ? "secondary" : "ghost"}
        className="h-7 px-2"
        aria-pressed={density === "compact"}
        onClick={() => onDensityChange("compact")}
      >
        <Rows2 className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="sr-only">Compact density</span>
      </Button>
    </div>
  );
}

export interface ColumnVisibilityOption {
  id: string;
  label: string;
}

/**
 * Standalone column-visibility control. Decoupled from the `useReactTable` instance so callers
 * that already own a `ColumnDef[]` array can drive `DataTable`'s `columnVisibility` prop without
 * reaching into TanStack internals. Uses a native, keyboard-operable disclosure (Popover +
 * checkboxes) rather than a custom ARIA grid.
 */
export function ColumnVisibilityMenu({
  options,
  visibility,
  onChange,
  onReset,
  label = "Columns"
}: {
  options: ColumnVisibilityOption[];
  visibility: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
  onReset?: () => void;
  label?: string;
}) {
  const hiddenCount = options.filter((option) => visibility[option.id] === false).length;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="secondary" className="h-7 gap-1.5 px-2">
          <Columns3 className="h-3.5 w-3.5" aria-hidden="true" />
          {label}
          {hiddenCount > 0 ? <span className="[font-variant-numeric:tabular-nums]">({hiddenCount} hidden)</span> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-2">
        <p className="mb-1.5 px-1 text-xs font-medium text-muted-foreground">Show columns</p>
        <ul className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
          {options.map((option) => {
            const checked = visibility[option.id] !== false;
            return (
              <li key={option.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-1 text-sm hover:bg-[var(--color-surface-sunken)]">
                  <Checkbox
                    checked={checked}
                    onCheckedChange={(value) => onChange({ ...visibility, [option.id]: value === true })}
                  />
                  {option.label}
                </label>
              </li>
            );
          })}
        </ul>
        {onReset ? (
          <div className="mt-1.5 border-t border-border pt-1.5">
            <button
              type="button"
              className="w-full rounded-[var(--radius-sm)] px-1.5 py-1 text-left text-sm text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
              onClick={onReset}
            >
              All columns
            </button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

/** Debounces a value so a live region can announce settled results without spamming every keystroke. */
export function useDebouncedValue<T>(value: T, delayMs = 500): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function TablePaginationBar({ pagination }: { pagination: DataTablePagination }) {
  const { pageIndex, pageSize, totalRows, onPageChange, onPageSizeChange, pageSizeOptions = [25, 50, 100] } =
    pagination;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));
  const rangeStart = totalRows === 0 ? 0 : pageIndex * pageSize + 1;
  const rangeEnd = Math.min(totalRows, (pageIndex + 1) * pageSize);
  const rangeText =
    totalRows === 0 ? "No rows" : `Showing ${rangeStart.toLocaleString()}–${rangeEnd.toLocaleString()} of ${totalRows.toLocaleString()}`;
  // Announce the settled result count, not every intermediate value while filters/search change.
  const announcedRangeText = useDebouncedValue(rangeText);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2 text-xs text-muted-foreground">
      <p className="[font-variant-numeric:tabular-nums]">{rangeText}</p>
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcedRangeText}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {onPageSizeChange ? (
          <label className="inline-flex items-center gap-1.5">
            <span>Rows</span>
            <select
              className="h-7 rounded-[var(--radius-sm)] border border-border bg-background px-2 text-xs text-foreground"
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
            >
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="inline-flex items-center gap-1">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-7 px-2"
            disabled={pageIndex <= 0}
            onClick={() => onPageChange(pageIndex - 1)}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="sr-only">Previous page</span>
          </Button>
          <span className="min-w-16 text-center [font-variant-numeric:tabular-nums]">
            {pageIndex + 1} / {pageCount}
          </span>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-7 px-2"
            disabled={pageIndex + 1 >= pageCount}
            onClick={() => onPageChange(pageIndex + 1)}
          >
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="sr-only">Next page</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

export function DataTable<TData>({
  columns,
  data,
  density = "default",
  onDensityChange,
  showDensityToggle = false,
  pagination,
  loading = false,
  emptyTitle = "No rows to show",
  emptyDescription,
  emptyAction,
  getRowId,
  rowSelection,
  onRowSelectionChange,
  enableRowSelection = false,
  stickyFirstColumn = false,
  toolbar,
  className,
  tableClassName,
  getRowClassName,
  columnVisibility,
  onColumnVisibilityChange,
  fillAvailableWidth = false
}: DataTableProps<TData>) {
  const selectionEnabled = enableRowSelection !== false && Boolean(enableRowSelection || rowSelection || onRowSelectionChange);

  const tableColumns = useMemo(() => {
    if (!selectionEnabled) return columns;

    const selectColumn: ColumnDef<TData, unknown> = {
      id: "__select",
      header: ({ table }) => (
        <Checkbox
          checked={table.getIsAllPageRowsSelected() ? true : table.getIsSomePageRowsSelected() ? "indeterminate" : false}
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(value === true)}
          aria-label="Select all rows on this page"
          title="Select all rows on this page"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          disabled={!row.getCanSelect()}
          onCheckedChange={(value) => row.toggleSelected(value === true)}
          aria-label="Select row"
        />
      ),
      enableSorting: false,
      size: 40
    };

    return [selectColumn, ...columns];
  }, [columns, selectionEnabled]);

  const table = useReactTable({
    data,
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    enableRowSelection,
    onRowSelectionChange,
    onColumnVisibilityChange,
    state: {
      rowSelection: rowSelection ?? {},
      columnVisibility: columnVisibility ?? {}
    },
    manualPagination: Boolean(pagination),
    pageCount: pagination ? Math.max(1, Math.ceil(pagination.totalRows / pagination.pageSize)) : undefined
  });

  const showToolbar = Boolean(toolbar || (showDensityToggle && onDensityChange));
  const isEmpty = !loading && data.length === 0;
  const hasFooter = tableColumns.some((column) => column.footer !== undefined);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const [track, setTrack] = useState({ width: 0, overflow: false });

  useEffect(() => {
    const scroller = tableScrollRef.current;
    if (!scroller || isEmpty) return;
    const measure = () => {
      setTrack({
        width: scroller.scrollWidth,
        overflow: scroller.scrollWidth - scroller.clientWidth > 1
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    const tableEl = scroller.querySelector("table");
    if (tableEl) observer.observe(tableEl);
    return () => observer.disconnect();
  }, [isEmpty, data, tableColumns, density]);

  const syncingScroll = useRef(false);

  const syncScroll = (source: HTMLDivElement, target: HTMLDivElement | null) => {
    if (!target || syncingScroll.current || source.scrollLeft === target.scrollLeft) return;
    syncingScroll.current = true;
    target.scrollLeft = source.scrollLeft;
    syncingScroll.current = false;
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-[var(--radius-default)] border border-border bg-card",
        className
      )}
    >
      {showToolbar ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-3 py-2">
          <div className="min-w-0 flex-1">{toolbar}</div>
          {showDensityToggle && onDensityChange ? (
            <DensityToggle density={density} onDensityChange={onDensityChange} />
          ) : null}
        </div>
      ) : null}

      {isEmpty ? (
        <div className="p-4">
          <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} />
        </div>
      ) : (
        <div
          ref={tableScrollRef}
          className="cryo-hscroll overflow-x-auto"
          onScroll={(event) => syncScroll(event.currentTarget, trackRef.current)}
        >
          <table
            className={cn(
              fillAvailableWidth ? "w-full min-w-full table-auto" : "w-max min-w-full",
              "border-separate border-spacing-0 text-[13px]",
              tableClassName
            )}
          >
            <thead className="sticky top-0 z-10 bg-[var(--color-table-header)]">
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-border">
                  {headerGroup.headers.map((header, columnIndex) => (
                    <th
                      key={header.id}
                      className={cn(
                        "border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground",
                        stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "header")
                      )}
                      style={fillAvailableWidth ? undefined : { width: header.getSize() }}
                    >
                      {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {loading && data.length === 0
                ? Array.from({ length: pagination?.pageSize ?? 5 }).map((_, rowIndex) => (
                    <tr key={`loading-${rowIndex}`} className={cn("group", rowHeightClass[density])}>
                      {tableColumns.map((column, columnIndex) => (
                        <td
                          key={`${column.id ?? columnIndex}-loading`}
                          className={cn(
                            "border-b border-border/70 px-3 py-2",
                            stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "body")
                          )}
                        >
                          <Skeleton className="h-4 w-full max-w-[12rem]" />
                        </td>
                      ))}
                    </tr>
                  ))
                : table.getRowModel().rows.map((row) => (
                    <tr
                      key={row.id}
                      className={cn(
                        "group transition-colors duration-[var(--motion-fast)] ease-[var(--ease-standard)] hover:bg-[var(--color-surface-sunken)] data-[state=selected]:bg-[var(--color-primary-soft)]",
                        rowHeightClass[density],
                        row.getIsSelected() && "bg-[var(--color-primary-soft)]",
                        getRowClassName?.(row.original)
                      )}
                      data-state={row.getIsSelected() ? "selected" : undefined}
                    >
                      {row.getVisibleCells().map((cell, columnIndex) => (
                        <td
                          key={cell.id}
                          className={cn(
                            "border-b border-border/70 px-3 py-2 align-middle text-foreground",
                            stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "body")
                          )}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
            </tbody>
            {hasFooter ? (
              <tfoot>
                {table.getFooterGroups().map((footerGroup) => (
                  <tr key={footerGroup.id} className="bg-[var(--color-table-header)]">
                    {footerGroup.headers.map((header, columnIndex) => (
                      <td
                        key={header.id}
                        className={cn(
                          "border-t-2 border-border px-3 py-2 text-left text-xs font-semibold text-foreground [font-variant-numeric:tabular-nums]",
                          stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "footer")
                        )}
                      >
                        {header.isPlaceholder ? null : flexRender(header.column.columnDef.footer, header.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tfoot>
            ) : null}
          </table>
        </div>
      )}

      {!isEmpty && track.overflow ? (
        <div
          ref={trackRef}
          className="cryo-hscroll-track overflow-x-auto overflow-y-hidden"
          onScroll={(event) => syncScroll(event.currentTarget, tableScrollRef.current)}
          aria-hidden="true"
        >
          <div style={{ width: track.width, height: 1 }} />
        </div>
      ) : null}

      {pagination ? <TablePaginationBar pagination={pagination} /> : null}
    </div>
  );
}
