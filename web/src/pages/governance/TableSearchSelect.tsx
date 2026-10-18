import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { getTable, listTables } from "../../api/client";
import type { TableRead } from "../../api/types";
import { any, leaf } from "../../lib/predicate";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { qualifiedTableName } from "../../lib/tableLabels";

const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_RESULT_LIMIT = 25;

const escapeGlob = (value: string): string =>
  value.replace(/\\/g, "\\\\").replace(/[?*[\]]/g, "\\$&");

export interface TableSearchSelectProps {
  id?: string;
  value: string;
  onChange: (tableId: string) => void;
  /** Label for the empty/no-selection option. */
  placeholder?: string;
  searchPlaceholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-invalid"?: boolean | "true" | "false";
  "aria-describedby"?: string;
}

/**
 * Server-search-backed table selector: replaces a static first-200-tables `<select>` with a
 * debounced name/schema search against the existing `/tables` predicate filter, an explicit
 * match count, and persistence of the currently selected table's label even when it falls
 * outside the active search results (so a selection never silently goes blank).
 */
export const TableSearchSelect = ({
  id,
  value,
  onChange,
  placeholder = "Select table",
  searchPlaceholder = "Search tables by name…",
  disabled,
  className,
  ...ariaProps
}: TableSearchSelectProps) => {
  const { api } = useAuth();
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  const predicate = useMemo(() => {
    if (!debouncedQuery) return null;
    const escaped = escapeGlob(debouncedQuery);
    return any([
      leaf("table_name", "glob", `*${escaped}*`),
      leaf("schema_name", "glob", `*${escaped}*`)
    ]);
  }, [debouncedQuery]);

  const results = useQuery({
    queryKey: ["governance-table-search", api.token, debouncedQuery],
    queryFn: () =>
      listTables(api, { predicate, limit: SEARCH_RESULT_LIMIT, offset: 0, sort: "table_name" })
  });

  const selectedTable = useQuery({
    queryKey: ["governance-table-by-id", api.token, value],
    queryFn: () => getTable(api, value),
    enabled: value.length > 0
  });

  const options = useMemo(() => {
    const byId = new Map<string, TableRead>();
    for (const table of results.data?.items ?? []) byId.set(table.id, table);
    if (selectedTable.data && !byId.has(selectedTable.data.id)) {
      byId.set(selectedTable.data.id, selectedTable.data);
    }
    return Array.from(byId.values());
  }, [results.data, selectedTable.data]);

  const total = results.data?.total ?? 0;
  const helperText = !debouncedQuery
    ? `Showing ${options.length} of ${total} tables — search by name to narrow down.`
    : total === 0
      ? "No tables match this search."
      : `${total} table${total === 1 ? "" : "s"} match${total === 1 ? "es" : ""} "${debouncedQuery}"${
          total > options.length ? ` — showing first ${options.length}` : ""
        }.`;

  return (
    <div className={cn("space-y-1.5", className)}>
      <input
        type="search"
        className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring"
        placeholder={searchPlaceholder}
        value={query}
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
        aria-label={searchPlaceholder}
      />
      <select
        id={id}
        className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
        value={value}
        disabled={disabled || results.isLoading}
        onChange={(event) => onChange(event.target.value)}
        {...ariaProps}
      >
        <option value="">{placeholder}</option>
        {options.map((table) => (
          <option key={table.id} value={table.id}>
            {qualifiedTableName(table)}
          </option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">{helperText}</p>
    </div>
  );
};
