import { Link } from "react-router-dom";
import type { TableRead } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatGb, formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export const HotTablesCard = ({ tables, total, loading, error }: {
  tables: readonly TableRead[] | null;
  total: number | null;
  loading: boolean;
  error: boolean;
}) => (
  <HomeCard id="home-hot" title="Worst tables people query" description="D and F tables ranked by queries in the last 30 days. Fixing these pays back first." action={{ label: "All D and F tables", href: "/tables?grade=D,F" }} busy={loading} className="home-hot">
    {loading ? <div className="space-y-2">{[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-9 w-full" />)}</div> : error ? <p className="home-empty">Unavailable</p> : !tables?.length ? (
      <div className="home-empty"><p>No queried tables at D or F.</p><p className="home-caption">Tables with query activity in the last 30 days and a grade of D or F show up here.</p></div>
    ) : <>
      <div className="home-table-columns" aria-hidden="true"><span>Table</span><span>Grade</span><span>Queries · 30d</span><span>Small files</span></div>
      <ul className="home-table-rows" aria-label="Queried tables at grade D or F">
        {tables.map((table) => {
          const grade = table.stats?.grade ?? "?";
          return (
            <li key={table.id}>
              <Link to={`/tables/${table.id}`} aria-label={`${table.schema_name}.${table.table_name}, grade ${grade}, ${formatNumber(table.stats?.query_count_30d ?? 0)} queries in 30 days`}>
                <span className="home-table-name">{table.schema_name}.{table.table_name}</span>
                <span className="home-grade-letter" style={{ borderColor: `var(--grade-${grade.toLowerCase()})` }}>{grade}</span>
                <span className="home-table-num">{formatNumber(table.stats?.query_count_30d ?? 0, true)}</span>
                <span className="home-table-num">{formatNumber(table.stats?.small_files_count ?? 0, true)}<span className="home-table-sub"> · {formatGb(table.stats?.total_size_gb ?? 0)}</span></span>
              </Link>
            </li>
          );
        })}
      </ul>
      {total != null && total > tables.length ? <p className="home-caption home-table-more">Showing {formatNumber(tables.length)} of {formatNumber(total)}.</p> : null}
    </>}
  </HomeCard>
);
