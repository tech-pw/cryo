import { useMemo, useState, type KeyboardEvent } from "react";
import { Flame } from "lucide-react";
import { Skeleton } from "../../components/ui/skeleton";
import { formatBytesGb, formatNumber } from "../../lib/format";
import type { ImpactDay } from "../../lib/impactHistory";
import { HomeCard } from "./HomeCard";
import { useCountUp } from "./useCountUp";
import { buildCalendar, weekOverWeek, workStreak, workTotals, type CalendarCell } from "./calendarModel";

export const WORK_WEEKS = 26;
const CELL = 15;
const GAP = 3;
const LEFT = 26;
const TOP = 16;
const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""];

const monthLabel = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleString(undefined, { month: "short", timeZone: "UTC" });
const longDate = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

export const cellDescription = (cell: CalendarCell): string => {
  const when = longDate(cell.date);
  const day = cell.day;
  if (cell.state === "future") return `${when}: not yet`;
  if (!day || cell.state === "missing") return `${when}: no record`;
  if (cell.state === "no_actions") return `${when}: nothing needed doing`;
  const failed = day.jobs_failed > 0 ? ` · ${formatNumber(day.jobs_failed)} failed ${day.jobs_failed === 1 ? "job" : "jobs"}` : "";
  const tables = `${formatNumber(day.tables_touched)} ${day.tables_touched === 1 ? "table" : "tables"}`;
  if (cell.state === "unmeasured") return `${when}: ${tables} maintained, bytes not measured${failed}`;
  const partial = cell.state === "partial" ? ` (${formatNumber(day.actions_with_bytes_evidence)} of ${formatNumber(day.actions_succeeded)} actions measured)` : "";
  return `${when}: ${formatBytesGb(day.bytes_freed)} freed${partial} · ${tables}${failed}`;
};

export const WorkCalendar = ({ days, today, loading, error }: { days: readonly ImpactDay[] | null; today: string; loading: boolean; error: boolean }) => {
  const weeks = useMemo(() => buildCalendar(days ?? [], today, WORK_WEEKS), [days, today]);
  const cells = useMemo(() => weeks.flatMap((week) => week.cells), [weeks]);
  const totals = useMemo(() => workTotals(days ?? []), [days]);
  const streak = useMemo(() => workStreak(days ?? []), [days]);
  const wow = useMemo(() => weekOverWeek(days ?? [], today), [days, today]);
  const todayIndex = cells.findIndex((cell) => cell.date === today);
  const [active, setActive] = useState<number | null>(null);
  const freed = useCountUp(days?.length ? totals.bytes : null);
  const shown = active ?? (todayIndex >= 0 ? todayIndex : cells.length - 1);
  const width = LEFT + WORK_WEEKS * (CELL + GAP);
  const height = TOP + 7 * (CELL + GAP);

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    const step = { ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7 }[event.key];
    if (step == null) return;
    event.preventDefault();
    setActive((current) => Math.max(0, Math.min(cells.length - 1, (current ?? shown) + step)));
  };

  return (
    <HomeCard id="home-work" title="Cryo's work, day by day" description={`Storage Cryo gave back each day over the last ${WORK_WEEKS} weeks. Darker means more freed.`} action={{ label: "Impact history", href: "/cost" }} busy={loading} className="home-work">
      {loading ? <Skeleton className="h-48 w-full" /> : error ? <p className="home-empty">Impact history is unavailable right now.</p> : !days?.length ? (
        <div className="home-empty"><p>No daily impact recorded yet.</p><p className="home-caption">Each night's cleanup lands here once the daily ledger has run.</p></div>
      ) : (
        <div className="home-work-body">
          <div className="home-work-stats">
            <div className="home-work-hero">
              <span className="home-caption">Freed in {WORK_WEEKS} weeks</span>
              <strong aria-hidden="true">{formatBytesGb(freed ?? totals.bytes)}</strong>
              <span className="sr-only">{formatBytesGb(totals.bytes)} freed in {WORK_WEEKS} weeks.</span>
              <span className="home-caption">
                {totals.partial ? "Measured actions only; some days were partly measured." : "Every working day fully measured."}
                {wow != null ? <> <span className={wow >= 0 ? "home-work-up" : "home-work-down"}>{wow >= 0 ? "▲" : "▼"} {Math.abs(wow)}% vs last week</span></> : null}
              </span>
            </div>
            <dl className="home-work-figures">
              <div><dt>Table runs</dt><dd>{formatNumber(totals.tables, true)}</dd></div>
              <div><dt>Files compacted</dt><dd>{formatNumber(totals.filesCompacted, true)}</dd></div>
              <div><dt>Working days</dt><dd>{formatNumber(totals.activeDays)}</dd></div>
            </dl>
            <div className={`home-work-streak ${streak.current >= 3 ? "home-work-streak-hot" : ""}`}>
              <Flame size={18} aria-hidden="true" />
              <div>
                <strong>{streak.current > 0 ? `${formatNumber(streak.current)}-day clean streak` : "Streak starts with the next clean night"}</strong>
                <span className="home-caption">
                  {streak.brokeRecently ? "The last working day had failed jobs. " : ""}Best: {formatNumber(streak.best)} days. Quiet days don't break it.
                </span>
              </div>
            </div>
          </div>
          <div className="home-work-calendar">
            <svg
              viewBox={`0 0 ${width} ${height}`}
              role="grid"
              aria-label={`Daily bytes freed, last ${WORK_WEEKS} weeks. Use arrow keys to move between days.`}
              tabIndex={0}
              onKeyDown={onKeyDown}
              onMouseLeave={() => setActive(null)}
              aria-activedescendant={`home-work-cell-${shown}`}
            >
              <defs>
                <pattern id="home-work-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="4" height="4" fill="var(--color-chart-grid)" />
                  <line x1="0" y1="0" x2="0" y2="4" stroke="var(--color-chart-primary)" strokeWidth="1.6" strokeOpacity="0.6" />
                </pattern>
              </defs>
              {DAY_LABELS.map((label, row) => label ? <text key={row} x={0} y={TOP + row * (CELL + GAP) + CELL - 3} className="home-work-axis">{label}</text> : null)}
              {weeks.map((week, w) => {
                const first = week.cells[0].date;
                const showMonth = w === 0 || first.slice(5, 7) !== weeks[w - 1].cells[0].date.slice(5, 7);
                return showMonth && w < WORK_WEEKS - 1 ? <text key={`m-${week.start}`} x={LEFT + w * (CELL + GAP)} y={10} className="home-work-axis">{monthLabel(week.cells[6].date)}</text> : null;
              })}
              {cells.map((cell, index) => {
                const w = Math.floor(index / 7);
                const d = index % 7;
                const x = LEFT + w * (CELL + GAP);
                const y = TOP + d * (CELL + GAP);
                if (cell.state === "future") return null;
                return (
                  <g key={cell.date} id={`home-work-cell-${index}`} role="gridcell" aria-label={cellDescription(cell)} aria-selected={index === shown} onMouseEnter={() => setActive(index)}>
                    <rect
                      x={x}
                      y={y}
                      width={CELL}
                      height={CELL}
                      rx={3}
                      className={`home-work-cell home-work-level-${cell.level} home-work-${cell.state}`}
                      fill={cell.state === "unmeasured" ? "url(#home-work-hatch)" : undefined}
                    />
                    {cell.failed ? <path d={`M${x + CELL - 6},${y} H${x + CELL - 3} Q${x + CELL},${y} ${x + CELL},${y + 3} V${y + 6} Z`} className="home-work-failed" /> : null}
                    {index === shown ? <rect x={x - 1.5} y={y - 1.5} width={CELL + 3} height={CELL + 3} rx={4} className="home-work-focus" /> : null}
                  </g>
                );
              })}
            </svg>
            <div className="home-work-legend">
              <p className="home-work-detail" aria-live="polite">{cells[shown] ? cellDescription(cells[shown]) : ""}</p>
              <div className="home-work-scale" aria-hidden="true">
                <span>Less</span>
                {[0, 1, 2, 3, 4].map((level) => <i key={level} className={`home-work-cell home-work-level-${level} home-work-measured`} />)}
                <span>More</span>
                <i className="home-work-cell home-work-swatch-hatch" /><span>Not measured</span>
                <i className="home-work-cell home-work-swatch-failed" /><span>Failed jobs</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </HomeCard>
  );
};
