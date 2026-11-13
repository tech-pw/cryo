import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { TableRead } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatGb, formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";
import { layoutFixFirst, type MapPoint } from "./fixFirst";

const BOX = { width: 640, height: 300, left: 52, right: 14, top: 14, bottom: 34 };
const LIST = 5;

const pointLabel = (point: MapPoint) =>
  `${point.name}, grade ${point.grade}: ${formatNumber(point.queries)} queries in 30 days, ${formatNumber(point.smallFiles)} small files, ${formatGb(point.sizeGb)}${point.fixFirst ? ". In the fix-first quarter" : ""}`;

export const FixFirstMap = ({ tables, total, loading, error }: { tables: readonly TableRead[] | null; total: number | null; loading: boolean; error: boolean }) => {
  const navigate = useNavigate();
  const layout = useMemo(() => (tables ? layoutFixFirst(tables, BOX) : null), [tables]);
  const [hover, setHover] = useState<string | null>(null);
  const labelled = useMemo(() => new Set([...(layout?.points ?? [])].sort((a, b) => b.priority - a.priority).slice(0, 3).map((point) => point.id)), [layout]);
  const ranked = useMemo(() => [...(layout?.points ?? [])].sort((a, b) => b.priority - a.priority).slice(0, LIST), [layout]);
  const fixCount = layout?.points.filter((point) => point.fixFirst).length ?? 0;
  const hovered = layout?.points.find((point) => point.id === hover) ?? null;
  return (
    <HomeCard id="home-hot" title="Fix-first map" description="Queried tables at D or F. Further right means more queries, higher means more small files, bigger means more data." action={{ label: "All D and F tables", href: "/tables?grade=D,F" }} busy={loading} className="home-hot home-fixmap">
      {loading ? <Skeleton className="h-72 w-full" /> : error ? <p className="home-empty">Unavailable</p> : !layout ? (
        <div className="home-empty"><p>No queried tables at D or F.</p><p className="home-caption">Tables with query activity in the last 30 days and a grade of D or F show up here.</p></div>
      ) : (
        <>
          <p className="home-fixmap-headline">
            <strong>{formatNumber(fixCount)}</strong> {fixCount === 1 ? "table sits" : "tables sit"} in the fix-first corner{total != null && total > layout.points.length ? <span className="home-caption"> · showing the {formatNumber(layout.points.length)} most queried of {formatNumber(total)}</span> : null}
          </p>
          <div className="home-fixmap-plot">
            <svg viewBox={`0 0 ${BOX.width} ${BOX.height}`} role="group" aria-label="Fix-first map of queried tables at grade D or F">
              <rect x={layout.splitX} y={BOX.top} width={BOX.width - BOX.right - layout.splitX} height={layout.splitY - BOX.top} className="home-fixmap-zone" rx={6} />
              <text x={BOX.width - BOX.right - 8} y={BOX.top + 16} textAnchor="end" className="home-fixmap-zone-label">Fix first</text>
              <text x={BOX.left + 8} y={BOX.height - BOX.bottom - 8} className="home-fixmap-quiet-label">Can wait</text>
              {layout.xTicks.map((tick) => <g key={`x${tick.value}`}><line x1={tick.x} x2={tick.x} y1={BOX.top} y2={BOX.height - BOX.bottom} className="home-fixmap-grid" /><text x={tick.x} y={BOX.height - BOX.bottom + 16} textAnchor="middle" className="home-fixmap-tick">{formatNumber(tick.value, true)}</text></g>)}
              {layout.yTicks.map((tick) => <g key={`y${tick.value}`}><line x1={BOX.left} x2={BOX.width - BOX.right} y1={tick.y} y2={tick.y} className="home-fixmap-grid" /><text x={BOX.left - 8} y={tick.y} dy="0.35em" textAnchor="end" className="home-fixmap-tick">{formatNumber(tick.value, true)}</text></g>)}
              <line x1={layout.splitX} x2={layout.splitX} y1={BOX.top} y2={BOX.height - BOX.bottom} className="home-fixmap-split" />
              <line x1={BOX.left} x2={BOX.width - BOX.right} y1={layout.splitY} y2={layout.splitY} className="home-fixmap-split" />
              <text x={(BOX.left + BOX.width - BOX.right) / 2} y={BOX.height - 4} textAnchor="middle" className="home-fixmap-axis">Queries, last 30 days →</text>
              <text transform={`translate(12 ${(BOX.top + BOX.height - BOX.bottom) / 2}) rotate(-90)`} textAnchor="middle" className="home-fixmap-axis">Small files →</text>
              {layout.points.map((point) => (
                <g
                  key={point.id}
                  role="link"
                  tabIndex={0}
                  aria-label={pointLabel(point)}
                  className={`home-fixmap-point ${hover && hover !== point.id ? "home-fixmap-dim" : ""}`}
                  onMouseEnter={() => setHover(point.id)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(point.id)}
                  onBlur={() => setHover(null)}
                  onClick={() => navigate(`/tables/${point.id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") navigate(`/tables/${point.id}`);
                  }}
                >
                  <circle cx={point.x} cy={point.y} r={point.r} fill={`var(--grade-${point.grade.toLowerCase()})`} className="home-fixmap-dot" />
                  {labelled.has(point.id) && hover == null ? <text x={point.x + point.r + 5} y={point.y} dy="0.35em" className="home-fixmap-label" textAnchor={point.x > BOX.width * 0.7 ? "end" : "start"} dx={point.x > BOX.width * 0.7 ? -(2 * point.r + 10) : 0}>{point.name}</text> : null}
                </g>
              ))}
            </svg>
            {hovered ? (
              <div className="home-fixmap-tip" style={{ left: `${(hovered.x / BOX.width) * 100}%`, top: `${(hovered.y / BOX.height) * 100}%` }} role="presentation">
                <strong>{hovered.name}</strong>
                <span>Grade {hovered.grade} · {formatNumber(hovered.queries, true)} queries · {formatNumber(hovered.smallFiles, true)} small files · {formatGb(hovered.sizeGb)}</span>
              </div>
            ) : null}
          </div>
          <ol className="home-fixmap-list" aria-label="Highest priority tables">
            {ranked.map((point, index) => (
              <li key={point.id}>
                <Link to={`/tables/${point.id}`} aria-label={pointLabel(point)}>
                  <span className="home-fixmap-rank">{index + 1}</span>
                  <span className="home-table-name">{point.name}</span>
                  <span className="home-grade-letter" style={{ borderColor: `var(--grade-${point.grade.toLowerCase()})` }}>{point.grade}</span>
                  <span className="home-table-num">{formatNumber(point.queries, true)}<span className="home-table-sub"> q</span></span>
                  <span className="home-table-num">{formatNumber(point.smallFiles, true)}<span className="home-table-sub"> small</span></span>
                </Link>
              </li>
            ))}
          </ol>
        </>
      )}
    </HomeCard>
  );
};
