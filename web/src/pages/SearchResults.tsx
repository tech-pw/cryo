import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { searchCatalog, type SearchAssetType, type SearchHit } from "../api/client";
import {
  formatColumnTableContext,
  formatMatchReason,
  formatSearchDisplayName,
  formatSearchSubtitle,
  getSearchResultHref,
  SEARCH_GROUP_LABELS
} from "../components/shell/search-utils";
import { DataTable } from "../components/ui/data-table";
import { useAuth } from "../lib/auth";
import "../components/shell/shell.css";

const PAGE_LIMIT = 50;

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
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const groupFilter = params.get("group") as SearchAssetType | null;

  const searchQuery = useQuery({
    queryKey: ["catalog-search-page", token, q, groupFilter],
    queryFn: ({ signal }) =>
      searchCatalog(api, {
        q,
        limit: PAGE_LIMIT,
        group: groupFilter ?? undefined,
        signal
      }),
    enabled: q.length > 0
  });

  const groups = searchQuery.data?.groups ?? [];
  const visibleGroups = groupFilter ? groups.filter((group) => group.type === groupFilter) : groups;
  const columnTableData = useMemo(
    () => visibleGroups.find((group) => group.type === "columns")?.items ?? [],
    [visibleGroups]
  );

  return (
    <div className="cryo-search-page">
      <header className="cryo-search-page__header">
        <h1 className="cryo-search-page__title">Search results</h1>
        <p className="cryo-search-page__subtitle">
          {q ? (
            <>
              Showing matches for <strong>&ldquo;{q}&rdquo;</strong>
              {searchQuery.data ? ` · ${searchQuery.data.total} total` : null}
            </>
          ) : (
            "Enter a query in the topbar search to explore catalogs, schemas, tables, and columns."
          )}
        </p>
      </header>

      {q.length === 0 ? (
        <p className="cryo-search-page__subtitle">Try searching from the global catalog search in the topbar.</p>
      ) : searchQuery.isLoading ? (
        <p className="cryo-search-page__subtitle">Searching…</p>
      ) : searchQuery.isError ? (
        <p className="cryo-search-page__subtitle">
          Search is unavailable right now. The <code>GET /api/v1/search</code> endpoint may not be deployed yet.
        </p>
      ) : visibleGroups.every((group) => group.items.length === 0) ? (
        <p className="cryo-search-page__subtitle">
          No matches for &ldquo;{q}&rdquo;. Check spelling or try a broader term.
        </p>
      ) : (
        visibleGroups.map((group) => {
          if (group.items.length === 0) return null;
          const label = SEARCH_GROUP_LABELS[group.type] ?? group.label;
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
                />
              ) : (
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
              )}
              {group.total > group.items.length ? (
                <Link
                  className="cryo-shell__search-see-all"
                  to={`/search?q=${encodeURIComponent(q)}&group=${group.type}`}
                >
                  Showing {group.items.length} of {group.total} — refine with group filter
                </Link>
              ) : null}
            </section>
          );
        })
      )}
    </div>
  );
};

export { SearchResults };
