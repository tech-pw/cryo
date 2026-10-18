import { Link } from "react-router-dom";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { Skeleton } from "../../components/ui/skeleton";
import { formatGb, formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export interface GradeCount { grade: string; count: number }
export interface LakeZoneProps {
  tracked: number | null;
  distribution: GradeCount[] | null;
  loading: boolean;
  error: boolean;
  sizeGb: number | null;
  smallFiles: number | null;
  totalFiles?: number | null;
  dfDelta?: number | null;
  dfSeries?: readonly number[];
}

const Sparkline = ({ values }: { values: readonly number[] }) => {
  const width = 96;
  const height = 24;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => `${(index / (values.length - 1)) * width},${height - 2 - ((value - min) / span) * (height - 4)}`);
  const last = points[points.length - 1].split(",");
  return (
    <svg className="home-sparkline" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={`Tables at D or F over the last ${values.length} days: from ${values[0]} to ${values[values.length - 1]}.`}>
      <polyline points={points.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill="currentColor" />
    </svg>
  );
};

const GRADES = ["A", "B", "C", "D", "F"] as const;
const DESCRIPTIONS = { A: "Excellent", B: "Good", C: "Fair", D: "Poor", F: "Critical" };
const valid = (value: number | null): value is number => value != null && Number.isFinite(value) && value >= 0;

export const gradeBreakdown = (distribution: GradeCount[] | null, tracked: number | null) => {
  if (!distribution || distribution.some(({ count }) => !valid(count))) return null;
  const rows = GRADES.map((grade) => ({
    grade,
    count: distribution.filter((row) => row.grade === grade).reduce((sum, row) => sum + row.count, 0),
    label: DESCRIPTIONS[grade],
    color: `var(--grade-${grade.toLowerCase()})`
  }));
  const graded = rows.reduce((sum, row) => sum + row.count, 0);
  const unrecognized = distribution.filter((row) => !GRADES.some((grade) => grade === row.grade))
    .reduce((sum, row) => sum + row.count, 0);
  // Inconsistent snapshots should not produce percentages over 100% or a false healthy fleet.
  if (valid(tracked) && graded + unrecognized > tracked) return null;
  const ungraded = valid(tracked) ? tracked - graded : unrecognized;
  return { rows, graded, ungraded, total: graded + ungraded };
};

export const LakeZone = ({ tracked, distribution, loading, error, sizeGb, smallFiles, totalFiles = null, dfDelta = null, dfSeries = [] }: LakeZoneProps) => {
  const smallShare = valid(smallFiles) && valid(totalFiles) && totalFiles > 0 ? smallFiles / totalFiles * 100 : null;
  const breakdown = gradeBreakdown(distribution, tracked);
  const atRisk = breakdown ? breakdown.rows.filter((row) => row.grade === "D" || row.grade === "F").reduce((sum, row) => sum + row.count, 0) : null;
  const chartRows = breakdown ? [...breakdown.rows, { grade: "Not graded", count: breakdown.ungraded, label: "Not graded", color: "var(--color-muted-subtle)" }].filter((row) => row.count > 0) : [];
  return (
    <HomeCard title="Lake health" description="Table condition across your connected catalogs." action={{ label: "Explore tables", href: "/tables" }} busy={loading} className="home-lake">
      {loading ? <Skeleton className="h-56 w-full" /> : error ? <p className="home-empty">Table health is unavailable. Retry the dashboard above.</p> : (
        <>
          <div className="home-grade-layout">
            <div className="home-donut" aria-hidden="true" inert>
              {chartRows.length > 0 ? <ResponsiveContainer width="100%" height="100%">
                <PieChart accessibilityLayer={false}>
                  <Pie data={chartRows} dataKey="count" nameKey="grade" innerRadius="76%" outerRadius="96%" startAngle={90} endAngle={-270} stroke="var(--primitive-surface)" strokeWidth={3} isAnimationActive={false}>
                    {chartRows.map((row) => <Cell key={row.grade} fill={row.color} />)}
                  </Pie>
                </PieChart>
              </ResponsiveContainer> : <div className="home-donut-empty" />}
              <div className="home-donut-label"><strong>{valid(tracked) ? formatNumber(tracked, true) : "—"}</strong><span>tracked tables</span></div>
            </div>
            <div className="home-grade-key">
              <p className="sr-only">{valid(tracked) ? `${formatNumber(tracked)} tracked tables.` : "Tracked table count unavailable."}</p>
              {breakdown && breakdown.total > 0 ? (
                <ul aria-label="Grade distribution">
                  {breakdown.rows.map((row) => <li key={row.grade}>
                    <Link to={`/tables?grade=${row.grade}`} aria-label={`Grade ${row.grade}, ${row.label}: ${formatNumber(row.count)} tables`}>
                      <span className="home-grade-letter" style={{ borderColor: row.color }}>{row.grade}</span>
                      <span>{row.label}</span><strong>{formatNumber(row.count)}</strong>
                      <span className="home-grade-share">{Math.round(row.count / breakdown.total * 100)}%</span>
                    </Link>
                  </li>)}
                  {breakdown.ungraded > 0 ? <li className="home-ungraded"><span className="home-dot" /><span>Not graded</span><strong>{formatNumber(breakdown.ungraded)}</strong><span className="home-grade-share">{Math.round(breakdown.ungraded / breakdown.total * 100)}%</span></li> : null}
                </ul>
              ) : <div className="home-empty"><p>{tracked === 0 ? "No tracked tables" : "Grade split unknown"}</p><Link className="home-text-link" to={tracked === 0 ? "/settings/connections" : "/runs/scanner"}>{tracked === 0 ? "Connect a catalog" : "Check catalog scans"}</Link></div>}
            </div>
          </div>
          {breakdown && breakdown.total > 0 ? <div className={`home-health-note ${atRisk ? "home-health-note-warning" : ""}`}>
            <div className="home-health-note-text">
              <Link to="/tables?grade=D,F"><strong>{formatNumber(atRisk)}</strong> of {formatNumber(breakdown.total)} tables at D or F</Link>
              {dfDelta ? <span className={`home-delta ${dfDelta > 0 ? "home-delta-worse" : "home-delta-better"}`}>{dfDelta > 0 ? "+" : "−"}{formatNumber(Math.abs(dfDelta))} since yesterday</span> : null}
              <span>{atRisk ? "Prioritize these for maintenance." : breakdown.ungraded ? "Ungraded tables still need a scan." : "No tables in the two lowest grades."}</span>
            </div>
            {dfSeries.length > 1 ? <Sparkline values={dfSeries} /> : null}
          </div> : null}
          <dl className="home-inventory">
            <div><dt>Data size</dt><dd>{valid(sizeGb) ? formatGb(sizeGb) : "Not measured"}</dd></div>
            <div><dt>Small files</dt><dd>{valid(smallFiles) ? formatNumber(smallFiles, true) : "Not measured"}{smallShare != null ? <span className="home-inventory-share">{smallShare.toFixed(0)}% of files</span> : null}</dd></div>
          </dl>
        </>
      )}
    </HomeCard>
  );
};
