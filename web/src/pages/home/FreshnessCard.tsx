import { Link } from "react-router-dom";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber, formatTime } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export interface FreshnessRow {
  key: string;
  label: string;
  count: number;
  percentage: number;
  href: string;
}
const SHORT_LABELS: Record<string, string> = {
  within_24h: "Last 24 hours", "1_3d": "1–3 days", "3_7d": "3–7 days",
  "7_15d": "7–15 days", "15_30d": "15–30 days", "1_3m": "1–3 months",
  "3_6m": "3–6 months", "6_12m": "6–12 months", over_1y: "Over a year",
  unknown: "No timestamp", failed: "Failed / corrupt"
};
export const FreshnessCard = ({ rows, asOf, loading }: {
  rows: FreshnessRow[]; asOf: string | null; loading: boolean;
}) => (
  <HomeCard title="Table freshness" description="Time since the latest snapshot. Older does not always mean unhealthy." busy={loading} className="home-freshness">
    {loading ? <div className="space-y-3">{[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-6 w-full" />)}</div> : rows.length === 0 ? <div className="home-empty"><p>No snapshot ages yet.</p><Link className="home-text-link" to="/runs/scanner?tab=config">Run a catalog scan</Link></div> : <>
      <div className="home-freshness-columns" aria-hidden="true"><span>Snapshot age</span><span>Tables · share</span></div>
      <ul className="home-freshness-rows" aria-label="Tables by latest snapshot age">
        {rows.map((row) => <li key={row.key}>
          <Link to={row.href} aria-label={`${row.label}: ${formatNumber(row.count)} ${row.count === 1 ? "table" : "tables"}. ${row.percentage.toFixed(1)}% of tables. Open matching tables.`}>
            <span className="home-freshness-label">{SHORT_LABELS[row.key] ?? row.label}</span>
            <span className="home-freshness-track" aria-hidden="true"><span data-freshness-bar="" style={{ width: `${Math.min(100, Math.max(0, row.percentage))}%`, background: row.key === "failed" ? "var(--color-status-danger)" : row.key === "unknown" ? "var(--color-muted-subtle)" : "var(--color-chart-primary)" }} /></span>
            <span className="home-freshness-count">{formatNumber(row.count, true)}<span>{row.percentage.toFixed(1)}%</span></span>
          </Link>
        </li>)}
      </ul>
      <p className="home-caption home-freshness-footnote">{asOf ? `As of ${formatTime(asOf)}. ` : ""}Bars show each bucket’s share of tables. Select a row to explore.</p>
    </>}
  </HomeCard>
);
