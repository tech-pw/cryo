import type { DashboardImprovedTable, FleetHealthTrendPoint, MaintenanceControlRead } from "../../api/types";
import type { ImpactDay } from "../../lib/impactHistory";

export const LAST_VISIT_KEY = "cryo.home.lastVisit";
export const SESSION_BASELINE_KEY = "cryo.home.baseline";
export const MILESTONES_KEY = "cryo.home.milestones";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const MAX_BASELINE_DAYS = 30;

export interface Baseline {
  since: number;
  kind: "first" | "recent" | "visit";
}

const parse = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
};

const parseBaseline = (raw: string | null): Baseline | null => {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<Baseline>;
    if (typeof value.since === "number" && Number.isFinite(value.since) && ["first", "recent", "visit"].includes(value.kind ?? "")) {
      return { since: value.since, kind: value.kind as Baseline["kind"] };
    }
  } catch {
    return null;
  }
  return null;
};

/**
 * The moment "what changed" is measured from. A session keeps one baseline so reloads do not
 * erase the news; a visit under an hour ago falls back to the last day, a first visit to a week.
 */
export const resolveBaseline = (now: number, lastVisit: string | null, sessionBaseline: string | null): Baseline => {
  const session = parseBaseline(sessionBaseline);
  if (session && session.since <= now) return session;
  const previous = parse(lastVisit);
  if (previous == null || previous > now) return { since: now - 7 * DAY, kind: "first" };
  if (now - previous < HOUR) return { since: now - DAY, kind: "recent" };
  return { since: Math.max(previous, now - MAX_BASELINE_DAYS * DAY), kind: "visit" };
};

const utcDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

const sortedTrend = (trend: readonly FleetHealthTrendPoint[] | null | undefined) =>
  [...(trend ?? [])].filter((point) => finite(point.avg_health_score)).sort((a, b) => a.day.localeCompare(b.day));

export interface LakeScore {
  score: number;
  /** Change against the point closest to 7 days earlier; null when the trend is too short. */
  weekDelta: number | null;
  series: number[];
  best: boolean;
}

export const lakeScore = (trend: readonly FleetHealthTrendPoint[] | null | undefined): LakeScore | null => {
  const points = sortedTrend(trend);
  if (!points.length) return null;
  const latest = points[points.length - 1];
  const weekAgoDay = utcDay(Date.parse(`${latest.day}T00:00:00Z`) - 7 * DAY);
  const prior = [...points].reverse().find((point) => point.day <= weekAgoDay) ?? (points.length > 1 ? points[0] : null);
  const series = points.map((point) => point.avg_health_score);
  const delta = prior && prior !== latest ? Math.round((latest.avg_health_score - prior.avg_health_score) * 10) / 10 : null;
  return {
    score: Math.round(latest.avg_health_score),
    weekDelta: delta === 0 ? null : delta,
    series,
    best: series.length >= 7 && latest.avg_health_score >= Math.max(...series) && latest.avg_health_score > series[0]
  };
};

export interface ChangeSummary {
  improved: { count: number; capped: boolean; top: DashboardImprovedTable | null } | null;
  dfChange: number | null;
  freed: { bytes: number; partial: boolean; tables: number } | null;
}

export const changesSince = (
  since: number,
  input: {
    improved?: readonly DashboardImprovedTable[] | null;
    improvedTotal?: number | null;
    trend?: readonly FleetHealthTrendPoint[] | null;
    currentDf?: number | null;
    impact?: readonly ImpactDay[] | null;
  }
): ChangeSummary => {
  let improved: ChangeSummary["improved"] = null;
  if (input.improved) {
    const recent = input.improved
      .filter((row) => (parse(row.improved_at) ?? 0) >= since)
      .sort((a, b) => (b.overall_score ?? 0) - (b.previous_score ?? 0) - ((a.overall_score ?? 0) - (a.previous_score ?? 0)));
    const capped = recent.length === input.improved.length && (input.improvedTotal ?? 0) > input.improved.length;
    improved = { count: recent.length, capped, top: recent[0] ?? null };
  }
  const sinceDay = utcDay(since);
  const prior = sortedTrend(input.trend).filter((point) => point.day <= sinceDay && !point.is_partial && finite(point.tables_at_grade_df)).pop();
  const dfChange = prior && finite(input.currentDf) ? input.currentDf - prior.tables_at_grade_df : null;
  let freed: ChangeSummary["freed"] = null;
  const days = (input.impact ?? []).filter((day) => day.report_date >= sinceDay);
  const measured = days.filter((day) => day.measurement_state === "complete" || day.measurement_state === "partial");
  if (measured.length) {
    freed = {
      bytes: measured.reduce((sum, day) => sum + (finite(day.bytes_freed) ? day.bytes_freed : 0), 0),
      partial: days.some((day) => day.measurement_state === "partial" || day.measurement_state === "unmeasured"),
      tables: days.reduce((sum, day) => sum + (finite(day.tables_touched) ? day.tables_touched : 0), 0)
    };
  }
  return { improved, dfChange, freed };
};

export type ActionTone = "danger" | "warning" | "info" | "success";

export interface NextAction {
  id: string;
  tone: ActionTone;
  title: string;
  detail: string;
  href: string;
  cta: string;
}

const plural = (count: number, one: string, many = `${one}s`) => `${count.toLocaleString()} ${count === 1 ? one : many}`;

/** The single most useful thing to do now, most urgent first. */
export const nextBestAction = (input: {
  pauses?: readonly MaintenanceControlRead[] | null;
  platformCritical?: string | null;
  approvals?: number | null;
  failed?: number | null;
  runHref?: string | null;
  hotAtRisk?: number | null;
  needsCompaction?: number | null;
}): NextAction => {
  const global = input.pauses?.find((pause) => pause.scope_type === "global");
  if (global) {
    return { id: "pause", tone: "danger", title: "Maintenance is paused for the whole lake", detail: global.reason || "Nothing new runs until it resumes.", href: "/runs?tab=holds", cta: "Review the hold" };
  }
  if (input.platformCritical) {
    return { id: "platform", tone: "danger", title: "Cryo itself needs attention", detail: input.platformCritical, href: "#home-platform", cta: "See platform health" };
  }
  if (input.approvals && input.approvals > 0) {
    return { id: "approvals", tone: "warning", title: `${plural(input.approvals, "change")} waiting for your approval`, detail: "Cryo prepared these and is holding them for a person to confirm.", href: "/decisions?tab=approvals#operation-approvals", cta: "Review now" };
  }
  if (input.failed && input.failed > 0) {
    return { id: "failed", tone: "danger", title: `${plural(input.failed, "table")} failed last night`, detail: "Open the run to see the error and retry the failed tables.", href: input.runHref ?? "/runs", cta: "Open the run" };
  }
  if (input.hotAtRisk && input.hotAtRisk > 0) {
    return { id: "hot", tone: "warning", title: `${plural(input.hotAtRisk, "busy table")} at D or F`, detail: "People query these every day. Fixing them pays back first.", href: "#home-hot", cta: "See the fix-first map" };
  }
  if (input.needsCompaction && input.needsCompaction > 0) {
    return { id: "compaction", tone: "info", title: `${plural(input.needsCompaction, "table")} would benefit from compaction`, detail: "Cryo will pick them up in the next window, or you can start one now.", href: "/tables?needs=compaction", cta: "Plan compaction" };
  }
  return { id: "clear", tone: "success", title: "All clear", detail: "Nothing needs a person right now. Cryo keeps working overnight.", href: "/runs", cta: "See what's planned" };
};

export interface Milestone {
  id: string;
  title: string;
  detail: string;
}

const TB = 1024 ** 4;
const FREED_STEPS: Array<[number, string]> = [[100 * TB, "100 TB"], [10 * TB, "10 TB"], [TB, "1 TB"], [100 * 1024 ** 3, "100 GB"]];

/** Every milestone currently true, most impressive first. The caller shows the first unseen one. */
export const milestones = (input: {
  windowFreedBytes?: number | null;
  windowDays?: number;
  gradeF?: number | null;
  graded?: number | null;
  score?: LakeScore | null;
  dfSeries?: readonly number[];
}): Milestone[] => {
  const out: Milestone[] = [];
  const freed = input.windowFreedBytes;
  if (finite(freed)) {
    const step = FREED_STEPS.find(([bytes]) => freed >= bytes);
    if (step) out.push({ id: `freed-${step[1]}`, title: `Over ${step[1]} freed`, detail: `Cryo has given back more than ${step[1]} of storage in the last ${input.windowDays ?? 84} days.` });
  }
  if (input.gradeF === 0 && finite(input.graded) && input.graded > 0) {
    out.push({ id: "zero-f", title: "No tables at F", detail: "Every graded table is at D or better." });
  }
  if (input.score?.best) {
    out.push({ id: `best-score-${input.score.score}`, title: "Best lake score in two weeks", detail: `The fleet scores ${input.score.score} today, its highest in the trend window.` });
  }
  const df = input.dfSeries ?? [];
  if (df.length >= 7 && df[df.length - 1] < Math.min(...df.slice(0, -1))) {
    out.push({ id: `fewest-df-${df[df.length - 1]}`, title: "Fewest D/F tables in two weeks", detail: `${df[df.length - 1].toLocaleString()} tables at D or F, the lowest in the trend window.` });
  }
  return out;
};

export const firstUnseen = (all: readonly Milestone[], seen: readonly string[]): Milestone | null =>
  all.find((milestone) => !seen.includes(milestone.id)) ?? null;
