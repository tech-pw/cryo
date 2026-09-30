import type { TrendPoint } from "./gradeReasons";

const BANDS = [
  { grade: "A", from: 90, to: 100 },
  { grade: "B", from: 75, to: 90 },
  { grade: "C", from: 60, to: 75 },
  { grade: "D", from: 40, to: 60 },
  { grade: "F", from: 0, to: 40 }
] as const;

const W = 600;
const H = 180;

const tick = (epoch: number) => new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(epoch);

/**
 * Score line over tinted grade bands, so the reader sees which grade each stretch of
 * history sat in without reading the axis. Missing observations break the line instead
 * of being interpolated.
 */
export const ScoreTrend = ({ points, compact = false }: { points: TrendPoint[]; compact?: boolean }) => {
  const ordered = [...points]
    .map((point) => ({ ...point, t: Date.parse(point.at) }))
    .filter((point) => Number.isFinite(point.t))
    .sort((a, b) => a.t - b.t);
  const measured = ordered.filter((point) => point.value != null);
  if (measured.length < 2) return null;
  const t0 = ordered[0].t;
  const t1 = ordered[ordered.length - 1].t;
  const span = Math.max(t1 - t0, 1);
  const height = compact ? 56 : H;
  const x = (t: number) => ((t - t0) / span) * W;
  const y = (v: number) => height - (Math.min(100, Math.max(0, v)) / 100) * height;

  const segments: string[] = [];
  let current: string[] = [];
  for (const point of ordered) {
    if (point.value == null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      continue;
    }
    current.push(`${current.length ? "L" : "M"}${x(point.t).toFixed(1)},${y(point.value).toFixed(1)}`);
  }
  if (current.length > 1) segments.push(current.join(" "));
  const last = measured[measured.length - 1];
  const lastGrade = (last.grade ?? "").toLowerCase();

  return (
    <div className={compact ? "td-trend td-trend--compact" : "td-trend"} aria-hidden="true">
      <div className="td-trend-plot">
        {compact ? null : <div className="td-trend-scale">{[100, 75, 50, 25, 0].map((value) => <span key={value} style={{ top: `${100 - value}%` }}>{value}</span>)}</div>}
        <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none">
          {compact
            ? null
            : BANDS.map((band) => (
                <rect
                  key={band.grade}
                  x={0}
                  width={W}
                  y={y(band.to)}
                  height={y(band.from) - y(band.to)}
                  className={`td-trend-band td-trend-band--${band.grade.toLowerCase()}`}
                />
              ))}
          {segments.map((d, index) => (
            <path key={index} d={d} className="td-trend-line" vectorEffect="non-scaling-stroke" />
          ))}
        </svg>
        <span
          className="td-trend-dot"
          style={{
            left: `${(x(last.t) / W) * 100}%`,
            top: `${(y(last.value as number) / height) * 100}%`,
            background: lastGrade ? `var(--grade-${lastGrade})` : "var(--color-chart-primary)"
          }}
        />
        {compact ? null : (
          <div className="td-trend-grades">
            {BANDS.map((band) => (
              <span key={band.grade} style={{ top: `${(y((band.from + band.to) / 2) / height) * 100}%` }}>
                {band.grade}
              </span>
            ))}
          </div>
        )}
      </div>
      {compact ? null : (
        <div className="td-trend-axis">
          <span>{tick(t0)}</span>
          <span>{tick(t0 + span / 2)}</span>
          <span>{tick(t1)}</span>
        </div>
      )}
    </div>
  );
};
