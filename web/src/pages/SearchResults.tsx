import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ChevronLeft, ChevronRight, Search as SearchIcon } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, searchCatalog, type SearchAssetType, type SearchHit } from "../api/client";
import {
  formatColumnTableContext,
  formatMatchReason,
  formatSearchDisplayName,
  formatSearchSubtitle,
  getSearchResultHref,
  SEARCH_GROUP_LABELS,
  searchResultsHref
} from "../components/shell/search-utils";
import { PageHeader } from "../components/common";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Button } from "../components/ui/button";
import { DataTable } from "../components/ui/data-table";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Segmented, type SegmentedOption } from "../components/ui/segmented";
import { useAuth } from "../lib/auth";
import "../components/shell/shell.css";

const PAGE_LIMIT = 50;
const GROUP_ORDER: SearchAssetType[] = ["catalogs", "schemas", "tables", "columns"];

type GroupFilterValue = SearchAssetType | "all";

const searchErrorMessage = (error: unknown): string => {
  if (error instanceof ApiError) return String(error.detail ?? error.message);
  if (error instanceof Error) return error.message;
  return "Unknown error";
};

function SearchPaginationBar({
  pageIndex,
  pageSize,
  totalRows,
  onPageChange
}: {
  pageIndex: number;
  pageSize: number;
  totalRows: number;
  onPageChange: (pageIndex: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));
  const rangeStart = totalRows === 0 ? 0 : pageIndex * pageSize + 1;
  const rangeEnd = Math.min(totalRows, (pageIndex + 1) * pageSize);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2 text-xs text-muted-foreground">
      <p className="[font-variant-numeric:tabular-nums]">
        {totalRows === 0
          ? "No rows"
          : `Showing ${rangeStart.toLocaleString()}–${rangeEnd.toLocaleString()} of ${totalRows.toLocaleString()}`}
      </p>
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
  );
}

const columnResultColumns: ColumnDef<SearchHit, unknown>[] = [
  {
    id: "column",
    header: "Column",
    cell: ({ row }) => (
      <Link to={getSearchResultHref(row.original)} className="cryo-search-page__link">
        {formatSearchDisplayName(row.original)}
      </Link>
    )
  },
  {
    id: "table",
    header: "Table",
    cell: ({ row }) => formatColumnTableContext(row.original) ?? "—"
  },
  {
    id: "type",
    header: "Type",
    cell: ({ row }) => row.original.data_type ?? "—",
    size: 140
  },
  {
    id: "match",
    header: "Match",
    cell: ({ row }) => formatMatchReason(row.original),
    size: 160
  }
];

const SearchResults = () => {
  const { api, token } = useAuth();
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const groupFilter = (params.get("group") as SearchAssetType | null) ?? null;
  const rawOffset = Number(params.get("offset") ?? "0");
  const offset = groupFilter && Number.isFinite(rawOffset) && rawOffset > 0 ? Math.floor(rawOffset) : 0;

  const [queryInput, setQueryInput] = useState(q);
  useEffect(() => {
    setQueryInput(q);
  }, [q]);

  const searchQuery = useQuery({
    queryKey: ["catalog-search-page", token, q, groupFilter, offset],
    queryFn: ({ signal }) =>
      searchCatalog(api, {
        q,
        limit: PAGE_LIMIT,
        group: groupFilter ?? undefined,
        offset: groupFilter ? offset : undefined,
        signal
      }),
    enabled: q.length > 0,
    placeholderData: keepPreviousData
  });

  const groups = searchQuery.data?.groups ?? [];
  const visibleGroups = groupFilter ? groups.filter((group) => group.type === groupFilter) : groups;
  const columnTableData = useMemo(
    () => visibleGroups.find((group) => group.type === "columns")?.items ?? [],
    [visibleGroups]
  );

  const totalForView =
    groupFilter != null
      ? (groups.find((group) => group.type === groupFilter)?.total ?? 0)
      : (searchQuery.data?.total ?? 0);

  const groupOptions: SegmentedOption<GroupFilterValue>[] = [
    {
      value: "all",
      label: searchQuery.data ? `All · ${searchQuery.data.total}` : "All"
    },
    ...GROUP_ORDER.map((type) => {
      const total = searchQuery.data?.groups.find((group) => group.type === type)?.total;
      return {
        value: type,
        label: total != null ? `${SEARCH_GROUP_LABELS[type]} · ${total}` : SEARCH_GROUP_LABELS[type]
      };
    })
  ];

  const updateParams = (mutate: (next: URLSearchParams) => void) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        mutate(next);
        return next;
      },
      { replace: true }
    );
  };

  const handleSearchSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = queryInput.trim();
    updateParams((next) => {
      if (trimmed) next.set("q", trimmed);
      else next.delete("q");
      next.delete("offset");
    });
  };

  const handleGroupChange = (next: GroupFilterValue) => {
    updateParams((nextParams) => {
      if (next === "all") nextParams.delete("group");
      else nextParams.set("group", next);
      nextParams.delete("offset");
    });
  };

  const handlePageChange = (pageIndex: number) => {
    updateParams((next) => {
      const nextOffset = pageIndex * PAGE_LIMIT;
      if (nextOffset > 0) next.set("offset", String(nextOffset));
      else next.delete("offset");
    });
  };

  const liveStatus = (() => {
    if (q.length === 0) return "";
    if (searchQuery.isError) return `Search failed for "${q}".`;
    if (searchQuery.isFetching && !searchQuery.data) return `Searching for "${q}"…`;
    if (totalForView === 0) return `No results for "${q}".`;
    return `${totalForView.toLocaleString()} result${totalForView === 1 ? "" : "s"} for "${q}".`;
  })();

  return (
    <div className="cryo-search-page min-w-0 max-w-full">
      <PageHeader
        title="Search results"
        subtitle="Search catalogs, schemas, tables, and columns across every connected source."
      />

      <form onSubmit={handleSearchSubmit} className="mb-4 flex max-w-xl gap-2">
        <div className="flex-1">
          <Label htmlFor="search-page-input" className="sr-only">
            Search query
          </Label>
          <Input
            id="search-page-input"
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="Search catalogs, schemas, tables, columns…"
            autoComplete="off"
          />
        </div>
        <Button type="submit" variant="primary">
          <SearchIcon aria-hidden="true" />
          Search
        </Button>
      </form>

      {q.length > 0 ? (
        <div className="mb-4">
          <Segmented
            value={groupFilter ?? "all"}
            onChange={handleGroupChange}
            options={groupOptions}
            className="w-full sm:w-auto"
          />
        </div>
      ) : null}

      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {liveStatus}
      </div>

      {q.length === 0 ? (
        <p className="cryo-search-page__subtitle">
          Enter a query above (or from the topbar search) to explore catalogs, schemas, tables, and
          columns.
        </p>
      ) : searchQuery.isError ? (
        <div role="alert" className="rounded-[var(--radius-default)] border border-border bg-card p-4">
          <p className="text-sm font-medium text-foreground">Search is unavailable right now.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Check your connection and try again. Contact your administrator if this continues.
          </p>
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none">Technical details</summary>
            <pre className="mt-1 whitespace-pre-wrap break-words">{searchErrorMessage(searchQuery.error)}</pre>
          </details>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            onClick={() => void searchQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : (
        <AsyncBoundary
          isLoading={searchQuery.isLoading}
          isEmpty={!searchQuery.isLoading && totalForView === 0}
          emptyTitle="No matches"
          emptyDescription={`No results for "${q}". Check spelling or try a broader term.`}
        >
          {visibleGroups.map((group) => {
            if (group.items.length === 0) return null;
            const label = SEARCH_GROUP_LABELS[group.type] ?? group.label;
            const isPaginated = groupFilter === group.type;
            return (
              <section key={group.type} className="cryo-search-page__group">
                <h2 className="cryo-search-page__group-title">
                  {label}
                  <span className="cryo-shell__search-item-meta"> · {group.total}</span>
                </h2>
                {group.type === "columns" ? (
                  <DataTable
                    columns={columnResultColumns}
                    data={columnTableData}
                    density="compact"
                    getRowId={(hit) => hit.id}
                    emptyTitle="No column matches"
                    emptyDescription="Try a different column name or description keyword."
                    pagination={
                      isPaginated
                        ? {
                            pageIndex: Math.floor(offset / PAGE_LIMIT),
                            pageSize: PAGE_LIMIT,
                            totalRows: group.total,
                            onPageChange: handlePageChange
                          }
                        : undefined
                    }
                  />
                ) : (
                  <div className="cryo-search-page__table-scroller w-full max-w-full overflow-x-auto">
                    <table className="cryo-search-page__table">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Context</th>
                          <th>Match</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.items.map((hit) => {
                          const href = getSearchResultHref(hit);
                          const subtitle = formatSearchSubtitle(hit);
                          return (
                            <tr key={`${group.type}-${hit.id}`}>
                              <td>
                                <Link to={href} className="cryo-search-page__link">
                                  {formatSearchDisplayName(hit)}
                                </Link>
                              </td>
                              <td>{subtitle ?? "—"}</td>
                              <td>{formatMatchReason(hit)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {isPaginated ? (
                      <SearchPaginationBar
                        pageIndex={Math.floor(offset / PAGE_LIMIT)}
                        pageSize={PAGE_LIMIT}
                        totalRows={group.total}
                        onPageChange={handlePageChange}
                      />
                    ) : null}
                  </div>
                )}
                {!isPaginated && group.total > group.items.length ? (
                  <Link
                    className="cryo-shell__search-see-all"
                    to={searchResultsHref(q, group.type)}
                  >
                    Showing {group.items.length} of {group.total} — view all {label.toLowerCase()}
                  </Link>
                ) : null}
              </section>
            );
          })}
        </AsyncBoundary>
      )}
    </div>
  );
};

export { SearchResults };
