import type { SearchHit } from "../../api/client";

export const SEARCH_GROUP_LABELS: Record<string, string> = {
  catalogs: "Catalogs",
  schemas: "Schemas",
  tables: "Tables",
  columns: "Columns"
};

export const getSearchResultHref = (hit: SearchHit): string => {
  if (hit.type === "columns" && hit.table_id) {
    const column = hit.column_name ?? hit.name;
    return `/tables/${hit.table_id}?tab=structure&column=${encodeURIComponent(column)}`;
  }
  if (hit.type === "tables" && hit.table_id) {
    return `/tables/${hit.table_id}`;
  }
  if (hit.type === "schemas" && hit.catalog && hit.schema_name) {
    return `/catalogs?catalog=${encodeURIComponent(hit.catalog)}&schema=${encodeURIComponent(hit.schema_name)}`;
  }
  if (hit.type === "catalogs") {
    return `/catalogs?catalog=${encodeURIComponent(hit.name)}`;
  }
  if (hit.table_id) {
    return `/tables/${hit.table_id}`;
  }
  return `/search?q=${encodeURIComponent(hit.name)}`;
};

export const formatMatchReason = (hit: SearchHit): string => {
  if (hit.match_reason === "semantic" && hit.similarity_score != null) {
    return `Semantic match · ${(hit.similarity_score * 100).toFixed(0)}%`;
  }
  const labels: Record<string, string> = {
    name: "Name match",
    schema: "Schema match",
    catalog: "Catalog match",
    column: "Column match",
    semantic: "Semantic match"
  };
  return labels[hit.match_reason] ?? hit.match_reason;
};

export const formatColumnTableContext = (hit: SearchHit): string | null => {
  const column = hit.column_name ?? hit.name;
  if (hit.qualified_name && column && hit.qualified_name.endsWith(`.${column}`)) {
    return hit.qualified_name.slice(0, -(column.length + 1));
  }
  const parts = [hit.catalog, hit.schema_name].filter(Boolean);
  return parts.length > 0 ? parts.join(".") : null;
};

export const formatSearchDisplayName = (hit: SearchHit): string => {
  if (hit.type === "columns") {
    return hit.column_name ?? hit.name;
  }
  return hit.qualified_name ?? hit.name;
};

export const formatSearchSubtitle = (hit: SearchHit): string | null => {
  if (hit.type === "columns") {
    return formatColumnTableContext(hit);
  }
  if (hit.qualified_name) return hit.qualified_name;
  const parts = [hit.catalog, hit.schema_name].filter(Boolean);
  return parts.length > 0 ? parts.join(".") : null;
};

export const formatColumnSearchMeta = (hit: SearchHit): string => {
  return [hit.data_type, formatMatchReason(hit)].filter(Boolean).join(" · ");
};
