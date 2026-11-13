import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight, HardDrive, Sparkles, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { Skeleton } from "../../components/ui/skeleton";
import { formatBytesGb, formatNumber } from "../../lib/format";
import {
  LAST_VISIT_KEY,
  MILESTONES_KEY,
  SESSION_BASELINE_KEY,
  firstUnseen,
  resolveBaseline,
  type Baseline,
  type ChangeSummary,
  type LakeScore,
  type Milestone,
  type NextAction
} from "./briefing";
import { formatAgo } from "./homeSignals";
import { useCountUp } from "./useCountUp";

const storage = (kind: "local" | "session"): Storage | null => {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
};

/** Reads the previous visit once per mount and records this one. */
export const useVisitBaseline = (): Baseline => {
  const [baseline] = useState(() => {
    const now = Date.now();
    const resolved = resolveBaseline(now, storage("local")?.getItem(LAST_VISIT_KEY) ?? null, storage("session")?.getItem(SESSION_BASELINE_KEY) ?? null);
    storage("session")?.setItem(SESSION_BASELINE_KEY, JSON.stringify(resolved));
    return resolved;
  });
  useEffect(() => {
    storage("local")?.setItem(LAST_VISIT_KEY, new Date().toISOString());
  }, []);
  return baseline;
};

/** Shows each milestone once: the first render that sees it marks it as seen. */
export const useMilestone = (all: readonly Milestone[]): Milestone | null => {
  const [shown, setShown] = useState<Milestone | null>(null);
  const key = all.map((milestone) => milestone.id).join("|");
  useEffect(() => {
    if (shown || !all.length) return;
    let seen: string[] = [];
    try {
      seen = JSON.parse(storage("local")?.getItem(MILESTONES_KEY) ?? "[]") as string[];
    } catch {
      seen = [];
    }
    const next = firstUnseen(all, Array.isArray(seen) ? seen : []);
    if (!next) return;
    setShown(next);
    storage("local")?.setItem(MILESTONES_KEY, JSON.stringify([...seen, next.id].slice(-50)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return shown;
};

const baselineLabel = (baseline: Baseline) =>
  baseline.kind === "first"
    ? "In the last 7 days"
    : baseline.kind === "recent"
      ? "In the last 24 hours"
      : `Since your last visit, ${formatAgo(new Date(baseline.since).toISOString()) ?? "earlier"}`;

const ScoreRing = ({ score, display }: { score: number; display: number }) => {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg className="home-brief-ring" viewBox="0 0 132 132" aria-hidden="true">
      <defs>
        <linearGradient id="home-brief-ring-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--color-chart-primary)" />
          <stop offset="100%" stopColor="var(--color-status-success)" />
        </linearGradient>
      </defs>
      <circle cx="66" cy="66" r={radius} fill="none" stroke="var(--home-brief-track)" strokeWidth="11" />
      <circle
        cx="66"
        cy="66"
        r={radius}
        fill="none"
        stroke="url(#home-brief-ring-gradient)"
        strokeWidth="11"
        strokeLinecap="round"
        strokeDasharray={`${(Math.max(0, Math.min(100, display)) / 100) * circumference} ${circumference}`}
        transform="rotate(-90 66 66)"
      />
      <title>{`Lake score ${score} of 100`}</title>
    </svg>
  );
};

const AreaSpark = ({ values }: { values: readonly number[] }) => {
  if (values.length < 2) return null;
  const width = 160;
  const height = 40;
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  const points = values.map((value, index) => [(index / (values.length - 1)) * width, height - 4 - ((value - min) / span) * (height - 10)]);
  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lastX, lastY] = points[points.length - 1];
  return (
    <svg className="home-brief-spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="home-brief-spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--color-chart-primary)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--color-chart-primary)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,${height} ${line} ${width},${height}`} fill="url(#home-brief-spark-fill)" />
      <polyline points={line} fill="none" stroke="var(--color-chart-primary)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={lastX} cy={lastY} r="3" fill="var(--color-chart-primary)" />
    </svg>
  );
};

const Change = ({ icon, tone, value, text, href }: { icon: ReactNode; tone: "good" | "bad" | "plain"; value: string; text: string; href: string }) => (
  <li>
    <Link to={href} className={`home-brief-change home-brief-change-${tone}`}>
      <span className="home-brief-change-icon" aria-hidden="true">{icon}</span>
      <span className="home-brief-change-body"><strong>{value}</strong> <span>{text}</span></span>
      <ArrowRight size={14} aria-hidden="true" className="home-brief-change-go" />
    </Link>
  </li>
);

export interface DailyBriefingProps {
  baseline: Baseline;
  score: LakeScore | null;
  changes: ChangeSummary;
  action: NextAction | null;
  milestone: Milestone | null;
  loading: boolean;
}

export const DailyBriefing = ({ baseline, score, changes, action, milestone, loading }: DailyBriefingProps) => {
  const display = useCountUp(score?.score ?? null);
  const items = useMemo(() => {
    const out: Array<Parameters<typeof Change>[0] & { key: string }> = [];
    if (changes.improved && changes.improved.count > 0) {
      const top = changes.improved.top;
      out.push({
        key: "improved",
        icon: <TrendingUp size={16} />,
        tone: "good",
        value: `${formatNumber(changes.improved.count)}${changes.improved.capped ? "+" : ""}`,
        text: `${changes.improved.count === 1 ? "table" : "tables"} improved a grade${top ? `, led by ${top.schema_name}.${top.table_name} (${top.previous_grade ?? "?"} → ${top.grade ?? "?"})` : ""}`,
        href: "#home-improved"
      });
    }
    if (changes.dfChange != null && changes.dfChange !== 0) {
      const better = changes.dfChange < 0;
      out.push({
        key: "df",
        icon: better ? <ArrowDownRight size={16} /> : <ArrowUpRight size={16} />,
        tone: better ? "good" : "bad",
        value: `${better ? "−" : "+"}${formatNumber(Math.abs(changes.dfChange))}`,
        text: `${better ? "fewer" : "more"} tables at D or F`,
        href: "/tables?grade=D,F"
      });
    }
    if (changes.freed && changes.freed.bytes > 0) {
      out.push({
        key: "freed",
        icon: <HardDrive size={16} />,
        tone: "good",
        value: formatBytesGb(changes.freed.bytes),
        text: `freed by Cryo${changes.freed.partial ? " (measured actions only)" : ""}`,
        href: "#home-work"
      });
    }
    return out.slice(0, 3);
  }, [changes]);

  return (
    <section className="home-brief" aria-label="Daily briefing">
      <div className="home-brief-glow" aria-hidden="true" />
      {milestone ? (
        <p className="home-brief-milestone" role="status">
          <Sparkles size={14} aria-hidden="true" />
          <strong>{milestone.title}</strong>
          <span>{milestone.detail}</span>
        </p>
      ) : null}
      <div className="home-brief-grid">
        <div className="home-brief-score">
          {loading && !score ? <Skeleton className="h-32 w-32 rounded-full" /> : score ? (
            <>
              <div className="home-brief-ring-wrap">
                <ScoreRing score={score.score} display={display ?? score.score} />
                <div className="home-brief-ring-label">
                  <strong aria-hidden="true">{Math.round(display ?? score.score)}</strong>
                  <span>Lake score</span>
                </div>
                <span className="sr-only">Lake score {score.score} out of 100.</span>
              </div>
              <div className="home-brief-score-side">
                {score.weekDelta != null ? (
                  <span className={`home-brief-chip ${score.weekDelta > 0 ? "home-brief-chip-up" : "home-brief-chip-down"}`}>
                    {score.weekDelta > 0 ? "▲" : "▼"} {Math.abs(score.weekDelta).toLocaleString(undefined, { maximumFractionDigits: 1 })} pts this week
                  </span>
                ) : <span className="home-brief-chip">Steady this week</span>}
                <AreaSpark values={score.series} />
                <span className="home-brief-caption">
                  {score.series.length > 1
                    ? `${Math.round(score.series[0])} → ${score.score} over ${score.series.length} days. Average table health, 0 to 100.`
                    : "Average table health, 0 to 100."}
                </span>
              </div>
            </>
          ) : <p className="home-brief-caption">The lake score appears after the first daily trend point is recorded.</p>}
        </div>
        <div className="home-brief-changes">
          <h2>{baselineLabel(baseline)}</h2>
          {loading && !items.length ? (
            <div className="space-y-2">{[0, 1, 2].map((key) => <Skeleton key={key} className="h-11 w-full" />)}</div>
          ) : items.length ? (
            <ul>{items.map(({ key, ...item }) => <Change key={key} {...item} />)}</ul>
          ) : <p className="home-brief-quiet">A quiet stretch. No grade changes or measured cleanup in this window.</p>}
        </div>
        {action ? (
          <div className={`home-brief-action home-brief-action-${action.tone}`}>
            <span className="home-brief-eyebrow">Next best action</span>
            <strong>{action.title}</strong>
            <p>{action.detail}</p>
            <Link to={action.href} className="home-brief-cta">{action.cta}<ArrowRight size={15} aria-hidden="true" /></Link>
          </div>
        ) : null}
      </div>
    </section>
  );
};
