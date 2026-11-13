import { storageFixture } from "../../src/pages/home/storageFixture";
import type { Page, Route } from "@playwright/test";

import { VISUAL_NOW_ISO, visualUuid } from "../visual/fixtures/constants";

// A larger synthetic fleet for Home captures: about 1,200 tables, 12 weeks of impact
// history and two weeks of nightly runs. Deterministic; nothing comes from a real install.

const NOW = Date.parse(VISUAL_NOW_ISO);
const DAY = 86_400_000;
const GB = 1024 ** 3;
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const isoDay = (offset: number) => new Date(NOW - offset * DAY).toISOString().slice(0, 10);

let seed = 42;
const rand = () => {
  seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
  return seed / 2_147_483_648;
};

const SCHEMAS = ["analytics", "billing", "crm", "ops", "sales", "product", "marketing", "finance", "growth", "risk"];
const NAMES = [
  "events", "page_views", "sessions", "clickstream", "orders", "order_items", "invoices", "payments",
  "ledger_entries", "audit_log", "metrics", "inventory_snapshots", "campaign_touches", "user_profiles",
  "feature_flags", "search_queries", "shipments", "refunds", "fx_rates", "risk_scores"
];

const GRADES = { A: 312, B: 401, C: 286, D: 121, F: 64 };
const TOTAL = Object.values(GRADES).reduce((a, b) => a + b, 0) + 18;

const hot = Array.from({ length: 20 }, (_, i) => {
  const grade = i % 3 === 0 ? "F" : "D";
  const queries = Math.round(60_000 / (1 + i * 0.9) * (0.6 + rand() * 0.8));
  const small = Math.round(400 + rand() ** 1.6 * 58_000);
  return {
    table_id: visualUuid(0x200 + i),
    schema_name: SCHEMAS[i % SCHEMAS.length],
    table_name: NAMES[(i * 7) % NAMES.length],
    grade,
    overall_score: grade === "F" ? 20 + Math.round(rand() * 14) : 42 + Math.round(rand() * 16),
    total_size_gb: Math.round((8 + rand() ** 1.4 * 900) * 10) / 10,
    total_files: small + Math.round(rand() * 4000),
    small_files_count: small,
    estimated_monthly_s3_cost_usd: 12 + rand() * 200,
    query_count_30d: queries
  };
});

const improved = [
  ["sales", "orders", "D", 49, "B", 81, 40],
  ["analytics", "sessions", "F", 31, "C", 66, 95],
  ["billing", "invoices", "C", 63, "A", 91, 180],
  ["ops", "metrics", "D", 52, "B", 77, 260],
  ["crm", "user_profiles", "C", 68, "B", 84, 420],
  ["finance", "ledger_entries", "F", 27, "D", 55, 700]
].map(([schema, table, pg, ps, g, s, minutes], i) => ({
  table_id: visualUuid(0x300 + i),
  schema_name: schema,
  table_name: table,
  previous_grade: pg,
  previous_score: ps,
  grade: g,
  overall_score: s,
  improved_at: ago(minutes as number)
}));

export function homeDashboard(hotLimit = 5) {
  return {
    totals: {
      total_tables: TOTAL,
      total_size_gb: 486_210,
      total_files: 18_420_000,
      small_files_count: 6_912_000,
      running_jobs: 7,
      estimated_monthly_s3_cost_usd: 11_183,
      projected_monthly_s3_cost_usd: 9_120,
      projected_savings_usd_month: 2_063,
      reclaimable_bytes: Math.round(61_400 * GB),
      needs_compaction_count: 241
    },
    grade_distribution: Object.entries(GRADES).map(([grade, count]) => ({ grade, count })),
    freshness_distribution: [
      ["within_24h", "Updated in last 24 hours", 642], ["1_3d", "Not updated 1-3 days", 188],
      ["3_7d", "Not updated 3-7 days", 121], ["7_15d", "Not updated 7-15 days", 77],
      ["15_30d", "Not updated 15-30 days", 52], ["1_3m", "Not updated 1-3 months", 63],
      ["3_6m", "Not updated 3-6 months", 21], ["6_12m", "Not updated 6-12 months", 12],
      ["over_1y", "Not updated over 1 year", 18], ["unknown", "No snapshot timestamp", 6], ["failed", "Failed / corrupt tables", 2]
    ].map(([key, label, count]) => ({ key, label, count })),
    freshness_as_of: VISUAL_NOW_ISO,
    hot_at_risk: hot.slice(0, hotLimit),
    hot_at_risk_count: 37,
    recently_improved: improved,
    recently_improved_count: 58,
    top_offenders: [],
    drift: []
  };
}

export function homeFleetTrends() {
  const days = 14;
  return {
    days,
    health_trend: Array.from({ length: days }, (_, i) => ({
      day: isoDay(days - 1 - i),
      avg_health_score: Math.round((63 + i * 0.62 + Math.sin(i) * 0.8) * 10) / 10,
      tables_at_grade_df: Math.round(231 - i * 3.4 + Math.cos(i) * 3),
      is_partial: i === days - 1,
      tables_counted: TOTAL
    })),
    cost_trend: []
  };
}

export function homeImpactHistory(since: string, until: string) {
  seed = 7;
  seed = 7;
  const rows = [];
  for (let offset = 120; offset >= 0; offset -= 1) {
    const day = isoDay(offset);
    if (day < since || day > until) continue;
    const r = rand();
    const weekend = new Date(day).getUTCDay() % 6 === 0;
    const noActions = offset > 80 ? r < 0.35 : r < 0.05;
    const growth = 1 + (120 - offset) / 60;
    const bytes = noActions ? 0 : Math.round((weekend ? 140 : 380) * growth * (0.4 + rand()) * GB);
    const actions = noActions ? 0 : Math.round(40 + rand() * 120);
    const unmeasured = !noActions && rand() < 0.06;
    const partial = !noActions && !unmeasured && rand() < 0.18;
    rows.push({
      report_date: day,
      measurement_state: noActions ? "no_actions" : unmeasured ? "unmeasured" : partial ? "partial" : "complete",
      bytes_freed: unmeasured ? 0 : bytes,
      actions_succeeded: actions,
      actions_with_bytes_evidence: partial ? Math.round(actions * 0.7) : unmeasured ? 0 : actions,
      actions_without_bytes_evidence: partial ? actions - Math.round(actions * 0.7) : unmeasured ? actions : 0,
      tables_touched: noActions ? 0 : Math.round(actions * 0.6),
      optimize_files_in: noActions ? 0 : Math.round(actions * 900 * (0.5 + rand())),
      optimize_files_out: noActions ? 0 : Math.round(actions * 40),
      jobs_succeeded: noActions ? 0 : Math.round(actions * 0.6),
      jobs_failed: !noActions && (offset === 9 || offset === 23 || offset === 41) ? 3 : 0,
      infra_cost_usd: null,
      raw: null
    });
  }
  return { since, until, rows };
}

const SHAPES = ["succeeded", "succeeded", "succeeded", "partial", "succeeded", "succeeded", "succeeded", "succeeded", "failed", "succeeded", "succeeded", "succeeded", "partial", "succeeded"];

export function homeNightlyRuns(limit: number) {
  return SHAPES.slice(0, limit).map((status, i) => {
    const started = 300 + i * 1440;
    const selected = 520 - i * 6;
    const failed = status === "failed" ? 31 : status === "partial" ? 4 : 0;
    const partial = status === "partial" ? 9 : 0;
    const skipped = 18;
    return {
      id: visualUuid(0x400 + i),
      kind: "maintenance",
      status,
      trigger: "schedule",
      scheduled_at: ago(started),
      created_at: ago(started),
      finished_at: ago(started - 110),
      summary: {
        pipeline: "batch",
        selected,
        succeeded: selected - failed - partial - skipped - 6,
        failed,
        partial,
        skipped,
        planning_failed: 0,
        nothing_to_do: 6
      },
      realized_bytes_freed: Math.round(320 * GB)
    };
  });
}

export function homeFunnel() {
  const nodes: Array<[string, string, number, string | null]> = [
    ["active", "Active tables", 1_184, null],
    ["not_in_run", "Not reached", 96, "not-reached"],
    ["no_work", "No work needed", 512, "healthy"],
    ["needs_work", "Needs work", 576, "needs-work"],
    ["skip_cooldown", "Cooldown", 21, "skipped"],
    ["skip_budget_cap", "Over budget", 29, "skipped"],
    ["skip_excluded", "Not eligible", 6, "skipped"],
    ["selected", "Selected", 520, null],
    ["succeeded", "Succeeded", 483, null],
    ["failed", "Failed", 4, "failed"],
    ["in_progress", "Partial", 33, "in-progress"]
  ];
  const link = (source: string, target: string, value: number) => ({ source, target, value });
  return {
    run_id: visualUuid(0x400),
    run_status: "partial",
    run_count: 1,
    run_started_at: ago(300),
    run_finished_at: ago(190),
    window_started_at: ago(300),
    total_active: 1_184,
    nodes: nodes.map(([id, label, count, view]) => ({ id, label, count, view })),
    links: [
      link("active", "not_in_run", 96),
      link("active", "no_work", 512),
      link("active", "needs_work", 576),
      link("needs_work", "skip_cooldown", 21),
      link("needs_work", "skip_budget_cap", 29),
      link("needs_work", "skip_excluded", 6),
      link("needs_work", "selected", 520),
      link("selected", "succeeded", 483),
      link("selected", "failed", 4),
      link("selected", "in_progress", 33)
    ]
  };
}

export function homeVitals() {
  return {
    generated_at: VISUAL_NOW_ISO,
    score: 86,
    status: "HEALTHY",
    headline: "Cryo is healthy",
    deductions: [
      { reason: "jobs: 4 tables failed in the latest nightly run.", points: 8 },
      { reason: "dispatch: Admission waited 3 minutes for a Trino slot.", points: 6 }
    ],
    impact: { direction: "improving", measurement_state: "partial", gb_freed_yesterday: 612.4, wow_pct: 18.2, measured: 118, succeeded: 131 },
    north_star: { byte_actions_measured: 118, byte_actions_succeeded: 131 }
  };
}

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });

/** Overrides the Home endpoints with the larger fleet. Call after `installDemo`. */
export async function installHomeDemo(page: Page, lastVisitDaysAgo = 2): Promise<void> {
  await page.addInitScript((lastVisit) => {
    localStorage.setItem("cryo.home.lastVisit", lastVisit);
  }, new Date(NOW - lastVisitDaysAgo * DAY).toISOString());
  await page.route(
    (url) => /\/api\/v1\/(dashboards\/datalake|dashboard\/fleet-trends|impact\/history|vitals|runs|tables\/maintenance-funnel|impact\/storage-overview)$/.test(url.pathname.replace(/\/$/, "")),
    async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname.replace(/\/$/, "");
      if (p.endsWith("/dashboards/datalake")) return json(route, homeDashboard(Number(url.searchParams.get("hot_limit") ?? 5)));
      if (p.endsWith("/dashboard/fleet-trends")) return json(route, homeFleetTrends());
      if (p.endsWith("/impact/history")) {
        return json(route, homeImpactHistory(url.searchParams.get("since") ?? isoDay(83), url.searchParams.get("until") ?? isoDay(0)));
      }
      if (p.endsWith("/impact/storage-overview")) return json(route, storageFixture());
      if (p.endsWith("/vitals")) return json(route, homeVitals());
      if (p.endsWith("/tables/maintenance-funnel")) return json(route, homeFunnel());
      if (p.endsWith("/runs") && url.searchParams.get("pipeline") === "nightly") {
        return json(route, homeNightlyRuns(Number(url.searchParams.get("limit") ?? 14)));
      }
      return route.fallback();
    }
  );
}
