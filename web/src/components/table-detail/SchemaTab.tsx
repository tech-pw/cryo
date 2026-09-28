import { AlertTriangle, Lock, Table } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { ChartCard } from "./ChartCard";
import { SimpleTablePagination } from "./SimpleTablePagination";

export interface SchemaColumn {
  name: string;
  type: string;
  nullRate?: number;
  lowerBound?: string | number | null;
  upperBound?: string | number | null;
  isPartition?: boolean;
  piiCategory?: string;
  dqNullSpike?: boolean;
  dqValueRangeChanged?: boolean;
  dqValueRangeExplanation?: string;
}

interface SchemaTabProps {
  columns: SchemaColumn[];
  loading: boolean;
  highlightColumn?: string | null;
}

const typeClass = (type: string | undefined): string => {
  const lower = String(type || "unknown").toLowerCase();
  if (
    ["int", "integer", "bigint", "double", "float", "decimal", "long", "short"].some((t) =>
      lower.includes(t)
    )
  ) {
    return "td-type-numeric";
  }
  if (["string", "varchar", "char", "text"].some((t) => lower.includes(t))) {
    return "td-type-textual";
  }
  if (["timestamp", "date", "time", "datetime"].some((t) => lower.includes(t))) {
    return "td-type-temporal";
  }
  if (lower.includes("bool")) return "td-type-boolean";
  return "td-type-other";
};

type SortKey = "name" | "nullRate";

export const SchemaTabLabel = (): ReactNode => (
  <span>
    <Table className="mr-1 inline size-3.5" />
    Schema
  </span>
);

export const SchemaTab = ({ columns, loading, highlightColumn }: SchemaTabProps) => {
  const normalizedHighlight = highlightColumn?.trim().toLowerCase() ?? null;
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortAsc, setSortAsc] = useState(true);
  const pageSize = 12;

  const sortedColumns = useMemo(() => {
    const copy = [...columns];
    copy.sort((a, b) => {
      const aKnown = a.nullRate != null && Number.isFinite(a.nullRate);
      const bKnown = b.nullRate != null && Number.isFinite(b.nullRate);
      if (aKnown !== bKnown) return aKnown ? -1 : 1;
      if (sortKey === "name") {
        return sortAsc ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
      }
      const aRate = a.nullRate ?? 0;
      const bRate = b.nullRate ?? 0;
      return sortAsc ? aRate - bRate : bRate - aRate;
    });
    return copy;
  }, [columns, sortAsc, sortKey]);

  const pagedColumns = sortedColumns.slice(page * pageSize, (page + 1) * pageSize);

  useEffect(() => {
    setPage(0);
  }, [columns.length, sortKey, sortAsc]);

  useEffect(() => {
    if (!normalizedHighlight || loading) return;
    const timer = window.setTimeout(() => {
      document.getElementById("schema-highlight-column")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [normalizedHighlight, loading, columns.length]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((prev) => !prev);
      return;
    }
    setSortKey(key);
    setSortAsc(true);
  };

  return (
    <div className="td-tab-content">
      <ChartCard
        title="Columns & Schema Metrics"
        subtitle={`${columns.length} Columns Detected`}
      >
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading column metadata…</p>
        ) : columns.length === 0 ? (
          <EmptyState
            title="No Column Metadata Yet"
            description="Ensure this table has successfully run its initial metadata scan."
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="td-schema-table">
                <thead>
                  <tr>
                    <th style={{ width: 220 }}>
                      <button
                        type="button"
                        className="cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-[inherit]"
                        onClick={() => toggleSort("name")}
                      >
                        Name {sortKey === "name" ? (sortAsc ? "↑" : "↓") : ""}
                      </button>
                    </th>
                    <th style={{ width: 140 }}>Type</th>
                    <th style={{ width: 200 }}>
                      <button
                        type="button"
                        className="cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-[inherit]"
                        onClick={() => toggleSort("nullRate")}
                      >
                        Nullability {sortKey === "nullRate" ? (sortAsc ? "↑" : "↓") : ""}
                      </button>
                    </th>
                    <th style={{ width: 220 }}>Value Range / Bounds</th>
                    <th style={{ width: 110, textAlign: "center" }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedColumns.map((item) => {
                    const isHighlighted =
                      normalizedHighlight != null &&
                      item.name.toLowerCase() === normalizedHighlight;
                    const nullKnown = item.nullRate != null && Number.isFinite(item.nullRate);
                    const rate = nullKnown ? item.nullRate! * 100 : null;
                    const popRate = rate == null ? null : 100 - rate;
                    const hasBounds =
                      item.lowerBound !== undefined &&
                      item.upperBound !== undefined &&
                      item.lowerBound !== null &&
                      item.upperBound !== null;
                    const hasIssues = item.dqNullSpike || item.dqValueRangeChanged;

                    return (
                      <tr
                        key={item.name}
                        id={isHighlighted ? "schema-highlight-column" : undefined}
                        className={isHighlighted ? "td-schema-row-highlight" : undefined}
                      >
                        <td>
                          <div className="td-schema-col-name-wrap">
                            <span className="td-schema-col-name">{item.name}</span>
                            {item.isPartition ? (
                              <Badge className="td-schema-badge td-schema-badge-partition">
                                Partition Key
                              </Badge>
                            ) : null}
                            {item.piiCategory && item.piiCategory !== "NONE" ? (
                              <Badge variant="warning" className="td-schema-badge td-schema-badge-pii">
                                <Lock className="mr-0.5 inline size-3" />
                                PII: {item.piiCategory}
                              </Badge>
                            ) : null}
                          </div>
                        </td>
                        <td>
                          <span className={`td-type-badge ${typeClass(item.type)}`}>
                            {item.type || "unknown"}
                          </span>
                        </td>
                        <td>
                          {rate == null || popRate == null ? (
                            <div className="td-schema-null-wrap">
                              <div
                                className="td-schema-null-bar-container"
                                data-testid="schema-null-hatch"
                                style={{
                                  backgroundImage:
                                    "repeating-linear-gradient(-45deg, transparent, transparent 2px, currentColor 2px, currentColor 3px)"
                                }}
                              />
                              <span className="td-schema-null-text">Not measured</span>
                            </div>
                          ) : (
                            <div className="td-schema-null-wrap">
                              <div className="td-schema-null-bar-container">
                                <div className="td-schema-null-bar-pop" style={{ width: `${popRate}%` }} />
                                <div className="td-schema-null-bar-null" style={{ width: `${rate}%` }} />
                              </div>
                              <span className="td-schema-null-text">{rate.toFixed(1)}% Null</span>
                              {item.dqNullSpike ? (
                                <Badge variant="danger" className="td-schema-badge-spike">
                                  <AlertTriangle className="mr-0.5 inline size-3" />
                                  Spike
                                </Badge>
                              ) : null}
                            </div>
                          )}
                        </td>
                        <td>
                          {!hasBounds ? (
                            <span className="td-bounds-empty">—</span>
                          ) : (
                            <div className="td-schema-bounds">
                              <span
                                className="td-bounds-text"
                                title={`${item.lowerBound} to ${item.upperBound}`}
                              >
                                {String(item.lowerBound)}{" "}
                                <span className="td-bounds-sep">to</span> {String(item.upperBound)}
                              </span>
                              {item.dqValueRangeChanged ? (
                                <span
                                  className="td-bounds-warning"
                                  title={item.dqValueRangeExplanation || "Value range shifted"}
                                >
                                  <AlertTriangle
                                    className="ml-1.5 inline size-3"
                                    style={{ color: "var(--warning, #f0a020)" }}
                                  />
                                </span>
                              ) : null}
                            </div>
                          )}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <Badge
                            variant={hasIssues ? "warning" : "success"}
                            className="td-schema-status-tag"
                          >
                            {hasIssues ? "Warning" : "Healthy"}
                          </Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <SimpleTablePagination
              page={page}
              pageSize={pageSize}
              total={columns.length}
              onPageChange={setPage}
            />
          </>
        )}
      </ChartCard>
    </div>
  );
};

export default SchemaTab;
