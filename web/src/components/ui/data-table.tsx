import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnSizingState,
  type Header,
  type OnChangeFn,
  type RowSelectionState,
  type TableOptions,
  type VisibilityState
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight, Columns3, Rows2, Rows3 } from "lucide-react";
import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
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
  columnSizing?: ColumnSizingState;
  onColumnSizingChange?: OnChangeFn<ColumnSizingState>;
  columnSizingStorageKey?: string;
  enableColumnResizing?: boolean;
  /** Stretch to the container. Extra space is not dumped into column 1 when resizing is on. */
  fillAvailableWidth?: boolean;
}

const rowHeightClass: Record<TableDensity, string> = {
  default: "h-[var(--table-row-height-default)]",
  compact: "h-[var(--table-row-height-compact)]"
};

function readStoredColumnSizing(key: string | undefined): ColumnSizingState {
  if (!key) return {};
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1])
      )
    );
  } catch {
    return {};
  }
}

function writeStoredColumnSizing(key: string, value: ColumnSizingState): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // private mode / quota — degrade silently
  }
}

function columnSizeStyle(size: number): CSSProperties {
  return { width: size, minWidth: size, maxWidth: size };
}

function ColumnResizeHandle<TData>({ header }: { header: Header<TData, unknown> }) {
  if (!header.column.getCanResize()) return null;
  const resizing = header.column.getIsResizing();
  return (
    <div
      aria-hidden="true"
      data-column-resize={header.column.id}
      className={cn(
        "absolute top-0 right-0 z-30 h-full w-2 cursor-col-resize touch-none select-none",
        "after:pointer-events-none after:absolute after:top-1.5 after:right-0 after:bottom-1.5 after:w-px after:bg-border",
        "after:opacity-0 hover:after:opacity-100",
        resizing && "after:bg-primary after:opacity-100"
      )}
      onMouseDown={header.getResizeHandler()}
      onTouchStart={header.getResizeHandler()}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        header.column.resetSize();
      }}
    />
  );
}

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
  /** Options sharing a group render under one heading, in first-seen order. */
  group?: string;
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
  onShowAll,
  label = "Columns",
  disabledReason
}: {
  options: ColumnVisibilityOption[];
  visibility: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
  /** Restores the page's default column set. */
  onReset?: () => void;
  onShowAll?: () => void;
  label?: string;
  /** When set, the menu explains why columns are fixed instead of offering toggles. */
  disabledReason?: string;
}) {
  const shownCount = options.filter((option) => visibility[option.id] !== false).length;
  const groups = options.reduce<Map<string, ColumnVisibilityOption[]>>((acc, option) => {
    const key = option.group ?? "";
    acc.set(key, [...(acc.get(key) ?? []), option]);
    return acc;
  }, new Map());
  const grouped = groups.size > 1;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="secondary" className="gap-1.5 px-2.5">
          <Columns3 className="h-3.5 w-3.5" aria-hidden="true" />
          {label}
          <span className="text-muted-foreground [font-variant-numeric:tabular-nums]">
            {shownCount}/{options.length}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className={cn("p-0", grouped ? "w-[26rem]" : "w-56")}>
        <div className="flex items-baseline justify-between border-b border-border px-3 py-2">
          <p className="text-xs font-medium text-foreground">Columns</p>
          <p className="text-xs text-muted-foreground [font-variant-numeric:tabular-nums]">
            {shownCount} of {options.length} shown
          </p>
        </div>
        {disabledReason ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">{disabledReason}</p>
        ) : (
          <div className={cn("gap-x-4 gap-y-3 p-3", grouped ? "grid grid-cols-2" : "flex flex-col")}>
            {[...groups.entries()].map(([group, items]) => (
              <fieldset key={group || "columns"} className="min-w-0">
                {grouped ? (
                  <legend className="mb-1 px-1 text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                    {group}
                  </legend>
                ) : null}
                <ul className="flex flex-col gap-0.5">
                  {items.map((option) => {
                    const checked = visibility[option.id] !== false;
                    return (
                      <li key={option.id}>
                        <label className="flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-1 text-sm hover:bg-[var(--color-surface-sunken)]">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(value) => onChange({ ...visibility, [option.id]: value === true })}
                          />
                          <span className={cn("truncate", !checked && "text-muted-foreground")}>{option.label}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </fieldset>
            ))}
          </div>
        )}
        {!disabledReason && (onReset || onShowAll) ? (
          <div className="flex items-center justify-between gap-2 border-t border-border px-2 py-1.5">
            {onReset ? (
              <button
                type="button"
                className="cursor-pointer rounded-[var(--radius-sm)] px-1.5 py-1 text-xs text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
                onClick={onReset}
              >
                Reset to default
              </button>
            ) : (
              <span />
            )}
            {onShowAll ? (
              <button
                type="button"
                className="cursor-pointer rounded-[var(--radius-sm)] px-1.5 py-1 text-xs text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
                onClick={onShowAll}
              >
                Show all
              </button>
            ) : null}
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
  columnSizing: columnSizingProp,
  onColumnSizingChange,
  columnSizingStorageKey,
  enableColumnResizing = false,
  fillAvailableWidth = false
}: DataTableProps<TData>) {
  const columnResizingEnabled = enableColumnResizing || Boolean(columnSizingStorageKey);
  const selectionEnabled = enableRowSelection !== false && Boolean(enableRowSelection || rowSelection || onRowSelectionChange);
  const [uncontrolledSizing, setUncontrolledSizing] = useState<ColumnSizingState>(() =>
    readStoredColumnSizing(columnSizingStorageKey)
  );
  const columnSizing = columnSizingProp ?? uncontrolledSizing;

  useEffect(() => {
    if (columnSizingProp !== undefined || !columnSizingStorageKey) return;
    writeStoredColumnSizing(columnSizingStorageKey, uncontrolledSizing);
  }, [columnSizingProp, columnSizingStorageKey, uncontrolledSizing]);

  const handleColumnSizingChange: OnChangeFn<ColumnSizingState> = (updater) => {
    if (onColumnSizingChange) {
      onColumnSizingChange(updater);
      return;
    }
    setUncontrolledSizing((current) => (typeof updater === "function" ? updater(current) : updater));
  };

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
      enableResizing: false,
      size: 40,
      minSize: 40,
      maxSize: 40
    };

    return [selectColumn, ...columns];
  }, [columns, selectionEnabled]);

  const table = useReactTable({
    data,
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    enableRowSelection,
    enableColumnResizing: columnResizingEnabled,
    columnResizeMode: "onChange",
    onRowSelectionChange,
    onColumnVisibilityChange,
    onColumnSizingChange: handleColumnSizingChange,
    defaultColumn: {
      minSize: 64,
      maxSize: 720
    },
    state: {
      rowSelection: rowSelection ?? {},
      columnVisibility: columnVisibility ?? {},
      columnSizing
    },
    manualPagination: Boolean(pagination),
    pageCount: pagination ? Math.max(1, Math.ceil(pagination.totalRows / pagination.pageSize)) : undefined
  });
  const isResizing = Boolean(table.getState().columnSizingInfo.isResizingColumn);

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
  }, [isEmpty, data, tableColumns, density, columnSizing]);

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
          className={cn("cryo-hscroll overflow-x-auto", isResizing && "select-none")}
          onScroll={(event) => syncScroll(event.currentTarget, trackRef.current)}
        >
          <table
            className={cn(
              columnResizingEnabled
                ? "table-fixed"
                : fillAvailableWidth
                  ? "w-full min-w-full table-auto"
                  : "w-max min-w-full",
              "border-separate border-spacing-0 text-[13px]",
              tableClassName
            )}
            style={columnResizingEnabled ? { width: table.getTotalSize() } : undefined}
          >
            <thead className="sticky top-0 z-10 bg-[var(--color-table-header)]">
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-border">
                  {headerGroup.headers.map((header, columnIndex) => (
                    <th
                      key={header.id}
                      className={cn(
                        "relative border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground",
                        stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "header")
                      )}
                      style={
                        columnResizingEnabled
                          ? columnSizeStyle(header.getSize())
                          : fillAvailableWidth
                            ? undefined
                            : { width: header.getSize() }
                      }
                    >
                      {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                      {columnResizingEnabled ? <ColumnResizeHandle header={header} /> : null}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {loading && data.length === 0
                ? Array.from({ length: pagination?.pageSize ?? 5 }).map((_, rowIndex) => (
                    <tr key={`loading-${rowIndex}`} className={cn("group", rowHeightClass[density])}>
                      {table.getVisibleLeafColumns().map((column, columnIndex) => (
                        <td
                          key={`${column.id}-loading`}
                          className={cn(
                            "border-b border-border/70 px-3 py-2",
                            columnResizingEnabled && "overflow-hidden",
                            stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "body")
                          )}
                          style={columnResizingEnabled ? columnSizeStyle(column.getSize()) : undefined}
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
                            columnResizingEnabled && "overflow-hidden",
                            stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "body")
                          )}
                          style={columnResizingEnabled ? columnSizeStyle(cell.column.getSize()) : undefined}
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
                          columnResizingEnabled && "overflow-hidden",
                          stickyFirstColumn && stickyColumnClass(columnIndex, Boolean(enableRowSelection), "footer")
                        )}
                        style={columnResizingEnabled ? columnSizeStyle(header.getSize()) : undefined}
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
