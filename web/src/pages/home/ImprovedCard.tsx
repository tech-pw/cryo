import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { DashboardImprovedTable } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";
import { formatAgo } from "./homeSignals";

const Grade = ({ grade }: { grade: string | null | undefined }) => (
  <span className="home-grade-letter" style={{ borderColor: grade ? `var(--grade-${grade.toLowerCase()})` : undefined }}>{grade ?? "?"}</span>
);

export const ImprovedCard = ({ tables, total, loading, error }: {
  tables: readonly DashboardImprovedTable[] | null;
  total: number | null;
  loading: boolean;
  error: boolean;
}) => (
  <HomeCard title="Fixed by Cryo" description="Grades that rose after a successful maintenance job in the last 24 hours." busy={loading} className="home-improved">
    {loading ? <div className="space-y-2">{[0, 1, 2].map((key) => <Skeleton key={key} className="h-9 w-full" />)}</div> : error ? <p className="home-empty">Unavailable</p> : !tables?.length ? (
      <div className="home-empty"><p>No grade improvements yet today.</p><p className="home-caption">A table counts once it is rescanned after its maintenance job finishes.</p></div>
    ) : <>
      <ul className="home-table-rows home-improved-rows" aria-label="Tables improved by maintenance">
        {tables.map((table) => {
          const gained = table.overall_score != null && table.previous_score != null ? table.overall_score - table.previous_score : null;
          return (
            <li key={table.table_id}>
              <Link to={`/tables/${table.table_id}`} aria-label={`${table.schema_name}.${table.table_name} improved from grade ${table.previous_grade ?? "unknown"} to ${table.grade ?? "unknown"}`}>
                <span className="home-table-name">{table.schema_name}.{table.table_name}</span>
                <span className="home-improved-grades"><Grade grade={table.previous_grade} /><ArrowRight size={13} aria-hidden="true" /><Grade grade={table.grade} /></span>
                <span className="home-table-num home-tone-success">{gained != null && gained > 0 ? `+${formatNumber(gained)} pts` : ""}</span>
                <span className="home-table-num home-table-sub">{formatAgo(table.improved_at) ?? ""}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      {total != null && total > tables.length ? <p className="home-caption home-table-more">{formatNumber(total)} tables improved in total.</p> : null}
    </>}
  </HomeCard>
);
