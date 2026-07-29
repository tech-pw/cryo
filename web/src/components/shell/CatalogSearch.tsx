import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { searchCatalog } from "../../api/client";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useAuth } from "../../lib/auth";
import { Input } from "../ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "../ui/popover";
import { pushRecentItem } from "./recent";
import {
  formatColumnSearchMeta,
  formatMatchReason,
  formatSearchDisplayName,
  formatSearchSubtitle,
  getSearchResultHref,
  SEARCH_GROUP_LABELS
} from "./search-utils";

const PREVIEW_LIMIT = 5;

export const CatalogSearch = () => {
  const { api, token } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const debouncedQuery = useDebouncedValue(query.trim(), 200);

  const searchQuery = useQuery({
    queryKey: ["catalog-search-preview", token, debouncedQuery],
    queryFn: ({ signal }) =>
      searchCatalog(api, { q: debouncedQuery, limit: PREVIEW_LIMIT, signal }),
    enabled: debouncedQuery.length >= 2,
    staleTime: 10_000
  });

  useEffect(() => {
    setOpen(debouncedQuery.length >= 2);
  }, [debouncedQuery]);

  const onSelectHit = (href: string, label: string) => {
    pushRecentItem({ type: "table", label, href });
    setOpen(false);
    setQuery("");
    navigate(href);
  };

  const groups = searchQuery.data?.groups ?? [];
  const hasResults = groups.some((group) => group.items.length > 0);
  const showDropdown = open && debouncedQuery.length >= 2;

  return (
    <Popover open={showDropdown} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="cryo-shell__search-wrap">
          <div className="cryo-shell__search-input-wrap">
            <Search size={15} className="cryo-shell__search-icon" aria-hidden />
            <Input
              className="cryo-shell__search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onFocus={() => {
                if (debouncedQuery.length >= 2) setOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && debouncedQuery.length > 0) {
                  event.preventDefault();
                  setOpen(false);
                  navigate(`/search?q=${encodeURIComponent(debouncedQuery)}`);
                }
                if (event.key === "Escape") {
                  setOpen(false);
                }
              }}
              placeholder="Search tables, schemas, columns…"
              aria-label="Search catalogs"
              aria-expanded={showDropdown}
              aria-controls="catalog-search-preview"
              autoComplete="off"
            />
          </div>
        </div>
      </PopoverAnchor>

      <PopoverContent id="catalog-search-preview" align="center" sideOffset={6}>
        {searchQuery.isLoading ? (
          <div className="cryo-shell__search-loading">Searching…</div>
        ) : searchQuery.isError ? (
          <div className="cryo-shell__search-empty">
            Search unavailable. Try again or open the full results page.
          </div>
        ) : !hasResults ? (
          <div className="cryo-shell__search-empty">No matches for &ldquo;{debouncedQuery}&rdquo;</div>
        ) : (
          groups.map((group) => {
            if (group.items.length === 0) return null;
            const label = SEARCH_GROUP_LABELS[group.type] ?? group.label;
            return (
              <div key={group.type} className="cryo-shell__search-group">
                <div className="cryo-shell__search-group-label">{label}</div>
                {group.items.map((hit) => {
                  const href = getSearchResultHref(hit);
                  const subtitle = formatSearchSubtitle(hit);
                  const meta =
                    group.type === "columns"
                      ? [subtitle, formatColumnSearchMeta(hit)].filter(Boolean).join(" · ")
                      : [subtitle, formatMatchReason(hit)].filter(Boolean).join(" · ");
                  return (
                    <button
                      key={`${group.type}-${hit.id}`}
                      type="button"
                      className="cryo-shell__search-item"
                      onClick={() => onSelectHit(href, formatSearchDisplayName(hit))}
                    >
                      <span>{formatSearchDisplayName(hit)}</span>
                      <span className="cryo-shell__search-item-meta">{meta}</span>
                    </button>
                  );
                })}
                {group.total > group.items.length ? (
                  <Link
                    className="cryo-shell__search-see-all"
                    to={`/search?q=${encodeURIComponent(debouncedQuery)}&group=${group.type}`}
                    onClick={() => setOpen(false)}
                  >
                    See all {group.total} results →
                  </Link>
                ) : null}
              </div>
            );
          })
        )}
        {hasResults ? (
          <Link
            className="cryo-shell__search-see-all"
            to={`/search?q=${encodeURIComponent(debouncedQuery)}`}
            onClick={() => setOpen(false)}
          >
            See all {searchQuery.data?.total ?? 0} results →
          </Link>
        ) : null}
      </PopoverContent>
    </Popover>
  );
};
