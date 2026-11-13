import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { DashboardImprovedTable } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";
import { layoutSlope } from "./fixFirst";

const W = 460;
const H = 270;
const X0 = 70;
const X1 = 200;
const BOX = { top: 26, bottom: H - 18 };

export const WinsSlope = ({ tables, total, loading, error }: { tables: readonly DashboardImprovedTable[] | null; total: number | null; loading: boolean; error: boolean }) => {
  const navigate = useNavigate();
  const [hover, setHover] = useState<string | null>(null);
  const { lines, totalGain, min } = useMemo(() => layoutSlope(tables ?? [], BOX), [tables]);
  const scale = (score: number) => BOX.bottom - ((score - min) / (100 - min)) * (BOX.bottom - BOX.top);
  const ticks = [min, Math.round((min + 100) / 2), 100];
  return (
    <HomeCard id="home-improved" title="Fixed by Cryo" description="Health score before and after maintenance, for grades that rose in the last 24 hours." busy={loading} className="home-improved home-wins">
      {loading ? <Skeleton className="h-64 w-full" /> : error ? <p className="home-empty">Unavailable</p> : !lines.length ? (
        <div className="home-empty"><p>No grade gains in the last 24 hours.</p><p className="home-caption">Tables show up here when a maintenance job lifts their grade.</p></div>
      ) : (
        <>
          <p className="home-wins-headline">
            <strong>+{formatNumber(totalGain)} points</strong> across {formatNumber(lines.length)} {lines.length === 1 ? "table" : "tables"}
            {total != null && total > lines.length ? <span className="home-caption"> · {formatNumber(total)} improved in all</span> : null}
          </p>
          <svg viewBox={`0 0 ${W} ${H}`} className="home-wins-chart" role="group" aria-label="Score before and after maintenance">
            <text x={X0} y={12} textAnchor="middle" className="home-wins-axis">Before</text>
            <text x={X1} y={12} textAnchor="middle" className="home-wins-axis">Now</text>
            {ticks.map((tick) => <g key={tick}><line x1={X0 - 34} x2={X1} y1={scale(tick)} y2={scale(tick)} className="home-wins-grid" /><text x={X0 - 38} y={scale(tick)} dy="0.35em" textAnchor="end" className="home-wins-tick">{tick}</text></g>)}
            <line x1={X0} x2={X0} y1={BOX.top - 6} y2={BOX.bottom} className="home-wins-rail" />
            <line x1={X1} x2={X1} y1={BOX.top - 6} y2={BOX.bottom} className="home-wins-rail" />
            {lines.map((line) => {
              const color = `var(--grade-${line.to.grade.toLowerCase()})`;
              const dim = hover != null && hover !== line.id;
              return (
                <g
                  key={line.id}
                  role="link"
                  tabIndex={0}
                  aria-label={`${line.name}: ${line.from.grade} ${line.from.score} to ${line.to.grade} ${line.to.score}, up ${line.gain} points`}
                  className={`home-wins-line ${dim ? "home-wins-dim" : ""}`}
                  onMouseEnter={() => setHover(line.id)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(line.id)}
                  onBlur={() => setHover(null)}
                  onClick={() => navigate(`/tables/${line.id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") navigate(`/tables/${line.id}`);
                  }}
                >
                  <line x1={X0} y1={line.from.y} x2={X1} y2={line.to.y} stroke="transparent" strokeWidth={14} />
                  <line x1={X0} y1={line.from.y} x2={X1} y2={line.to.y} stroke={color} strokeWidth={hover === line.id ? 3.5 : 2.5} strokeLinecap="round" />
                  <circle cx={X0} cy={line.from.y} r={4} fill="var(--primitive-surface)" stroke={`var(--grade-${line.from.grade.toLowerCase()})`} strokeWidth={2} />
                  <circle cx={X1} cy={line.to.y} r={5} fill={color} />
                  <path d={`M${X1 + 7},${line.to.y} L${X1 + 14},${line.labelY}`} className="home-wins-leader" />
                  <text x={X1 + 18} y={line.labelY} dy="0.35em" className="home-wins-label">
                    <tspan className="home-wins-gain">+{line.gain}</tspan>
                    <tspan dx={6} className="home-wins-grades">{line.from.grade}→{line.to.grade}</tspan>
                    <tspan dx={6}>{line.name.length > 26 ? `${line.name.slice(0, 25)}…` : line.name}</tspan>
                  </text>
                </g>
              );
            })}
          </svg>
        </>
      )}
    </HomeCard>
  );
};
