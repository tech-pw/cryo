import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { applyTheme } from "./visual/helpers";

/**
 * Move 21 — WCAG 2.2 AA gate via axe-core.
 *
 * Scans every canonical launch route in the navigation (including the admin-only Skillsets
 * route) for accessibility violations and fails the build when any serious/critical issue is
 * found. Lower-severity violations (moderate/minor) are tracked as test annotations but do not
 * block.
 *
 * Per docs/PRODUCT.md (accessibility target) and docs/RATING_2026.md §10 — this gate turns the
 * WCAG target from aspiration into an enforced one.
 *
 * Determinism: every `/api/v1/*` request is intercepted via Playwright route handlers (see
 * `mockApi` below), so this suite never depends on a live backend and never silently skips.
 * Auth (`/auth/config`, `/auth/token`) is always mocked so sign-in works with no backend at all.
 * Primary landing-page endpoints get accurate, type-shaped empty/zeroed fixtures (fields taken
 * directly from `api/client.ts` / `api/types.ts` — nothing invented); any request without a
 * specific fixture falls back to `[]`, which is itself either a valid empty-list response or
 * causes the shared `ErrorBoundary` to render its own accessible fallback — either way the run
 * is deterministic and every page reaches a stable, scannable state.
 *
 * Routes covered:
 *   /health, /catalogs, /scans, /runs, /presets, /queries, /governance,
 *   /ai, /cost, /operations, /connections, /smart-maintenance, /sla, /search, /skillsets
 *
 * Color-contrast is enabled (not disabled) — this is the P0 accessibility gate.
 */
const protectedRoutes = [
  { path: "/health", name: "Health" },
  { path: "/catalogs", name: "Catalogs" },
  { path: "/scans", name: "Scanner" },
  { path: "/runs", name: "Runs" },
  { path: "/presets", name: "Presets" },
  { path: "/queries", name: "Queries" },
  { path: "/governance", name: "Governance" },
  { path: "/ai", name: "Intelligence" },
  { path: "/cost", name: "Cost" },
  { path: "/operations", name: "Operations" },
  { path: "/connections", name: "Connections" },
  { path: "/smart-maintenance", name: "Smart Maintenance" },
  { path: "/sla", name: "SLA" },
  { path: "/search?q=iceberg", name: "Search" },
  { path: "/skillsets", name: "Skillsets" }
];

const jsonRoute = (page: Page, pattern: string, body: unknown) =>
  page.route(pattern, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) })
  );

const SCANNER_CONFIG_FIXTURE = {
  id: 1,
  enabled: true,
  schedule_cron: "0 * * * *",
  timezone: "UTC",
  num_chunks: 4,
  full_scan_default: false,
  cost_full_scan_default: false,
  max_concurrent_runs: 1,
  created_at: new Date(0).toISOString(),
  updated_at: new Date(0).toISOString()
};

/**
 * Deterministically intercepts every API request the app can make from the canonical routes,
 * so this suite runs identically whether or not a real backend is reachable. Registered in
 * dependency order: the generic catch-all first, then specific fixtures — Playwright resolves
 * the most-recently-registered matching handler first, so the specific routes below take
 * priority over the catch-all.
 */
async function mockApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );

  // Auth — removes the only hard dependency a sign-in flow has on a live backend.
  await jsonRoute(page, "**/api/v1/auth/config", {
    mode: "dev",
    dev_token_enabled: true,
    oidc: null
  });
  await jsonRoute(page, "**/api/v1/auth/token", { access_token: "e2e-fixture-token" });

  // Health (default landing route: getDatalakeDashboard, listTables, getSmartMaintenanceOverview,
  // getFleetTrends).
  await jsonRoute(page, "**/api/v1/dashboards/datalake*", {
    totals: {
      total_tables: 0,
      total_size_gb: 0,
      small_files_count: 0,
      estimated_monthly_s3_cost_usd: 0,
      needs_compaction_count: 0
    },
    grade_distribution: [],
    top_offenders: [],
    drift: []
  });
  await jsonRoute(page, "**/api/v1/tables*", { items: [], total: 0, limit: 50, offset: 0 });
  await jsonRoute(page, "**/api/v1/smart-maintenance/overview", {
    generated_at: new Date(0).toISOString(),
    tables_with_stats: 0,
    tiers: { hot: 0, warm: 0, cold: 0, dormant: 0, unknown: 0 },
    roi_shadow_decisions_30d: 0,
    roi_enforced: false,
    pending_layout_recommendations: 0
  });
  await jsonRoute(page, "**/api/v1/dashboard/fleet-trends*", {
    days: 30,
    health_trend: [],
    cost_trend: []
  });

  // Catalogs (getMultiCatalogDashboard).
  await jsonRoute(page, "**/api/v1/multi-catalog", {
    catalogs: [],
    total_catalogs: 0,
    total_tables: 0,
    total_cost_usd: 0
  });

  // Cost (getCostDashboard).
  await jsonRoute(page, "**/api/v1/dashboards/cost", {
    totals: {
      current_monthly_cost_usd: 0,
      projected_monthly_cost_usd: 0,
      projected_savings_usd_month: 0,
      current_storage_bytes: 0,
      reclaimable_bytes: 0
    },
    savings_by_operation: [],
    cost_by_schema: [],
    top_opportunities: []
  });

  // Queries (getQueryPerformanceDashboard).
  await jsonRoute(page, "**/api/v1/query-performance*", {
    queries: [],
    total: 0,
    p50_latency_ms: null,
    p95_latency_ms: null,
    p99_latency_ms: null,
    avg_bytes_scanned: null,
    failed_count: 0
  });

  // SLA (getSlaStatusDashboard).
  await jsonRoute(page, "**/api/v1/sla-status*", {
    slas: [],
    total_breached: 0,
    total_monitored: 0
  });

  // Smart Maintenance (getSmartMaintenanceOverview above, plus detection tiers/recommendations).
  await jsonRoute(page, "**/api/v1/smart-maintenance/detection-tiers*", { items: [] });
  await jsonRoute(page, "**/api/v1/smart-maintenance/layout-recommendations*", { items: [] });

  // Operations / Scanner / Runs / Presets (getScannerStatus, getScannerConfig; listRuns and
  // listPresets are plain arrays already satisfied by the catch-all).
  await jsonRoute(page, "**/api/v1/scanner/status", {
    config: SCANNER_CONFIG_FIXTURE,
    total_tables: 0,
    tables_with_stats: 0
  });
  await jsonRoute(page, "**/api/v1/scanner/config", SCANNER_CONFIG_FIXTURE);

  // Intelligence (getBanditStatus, getCostModelEvaluate, getQPPConfidence; getCopilotQuestions
  // and listMcpTools are plain arrays already satisfied by the catch-all).
  await jsonRoute(page, "**/api/v1/governor/bandit/status", {
    observations: 0,
    confident: false,
    config: {}
  });
  await jsonRoute(page, "**/api/v1/ai/cost-model/evaluate", {
    mae_learned: 0,
    mae_heuristic: 0,
    beats_heuristic: false,
    band_coverage: 0,
    n_samples: 0
  });
  await jsonRoute(page, "**/api/v1/governor/qpp/confidence*", {
    observations: 0,
    confidence: 0,
    min_observations: 0,
    using_trained_model: false,
    qpp_enabled: false
  });

  // Search (owned page) — a realistic single-hit fixture so the populated view is exercised
  // rather than the empty state.
  await jsonRoute(page, "**/api/v1/search*", {
    query: "iceberg",
    groups: [
      {
        asset_type: "tables",
        total: 1,
        hits: [
          {
            asset_type: "table",
            name: "iceberg_events",
            qualified_name: "prod.raw.iceberg_events",
            catalog_name: "prod",
            schema_name: "raw",
            table_id: "11111111-1111-1111-1111-111111111111",
            match_reason: "name"
          }
        ]
      }
    ]
  });

  // Skillsets (admin-only canonical route).
  await jsonRoute(page, "**/api/v1/advanced/skillsets", {
    skillsets: [],
    generated_at: new Date(0).toISOString()
  });
}

async function signInAsAdmin(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("a11y-e2e");
  await page.getByLabel("Role").selectOption("admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Datalake health").first()).toBeVisible({ timeout: 15_000 });
}

async function scanRoute(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();

  const seriousOrCritical = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical"
  );

  if (seriousOrCritical.length > 0) {
    const summary = seriousOrCritical
      .map((violation) => {
        const nodes = violation.nodes
          .map(
            (node) =>
              `  ${node.target.join(" ")}: ${node.failureSummary ?? "No failure summary available"}`
          )
          .join("\n");
        return `[${violation.impact}] ${violation.id} — ${violation.description}\n${nodes}`;
      })
      .join("\n");
    throw new Error(
      `${seriousOrCritical.length} serious/critical a11y violations on ${path}:\n${summary}`
    );
  }

  const minor = results.violations.filter((v) => v.impact === "moderate" || v.impact === "minor");
  if (minor.length > 0) {
    test.info().annotations.push({
      type: "moderate-minor-violations",
      description: `${minor.length} moderate/minor a11y issues on ${path}: ${minor
        .map((v) => v.id)
        .join(", ")}`
    });
  }

  expect(seriousOrCritical).toEqual([]);
}

test.describe("WCAG 2.2 AA gate (axe-core, light)", () => {
  test.beforeEach(async ({ page }) => {
    await applyTheme(page, "light");
    await mockApi(page);
    await signInAsAdmin(page);
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) has no serious/critical a11y violations`, async ({
      page
    }) => {
      await scanRoute(page, route.path);
    });
  }
});

test.describe("WCAG 2.2 AA gate (axe-core, dark)", () => {
  test.use({ colorScheme: "dark" });

  test.beforeEach(async ({ page }) => {
    await applyTheme(page, "dark");
    await mockApi(page);
    await signInAsAdmin(page);
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) has no serious/critical a11y violations (dark)`, async ({
      page
    }) => {
      await scanRoute(page, route.path);
    });
  }
});

test.describe("320 CSS px reflow gate", () => {
  test.use({ viewport: { width: 320, height: 800 } });

  test.beforeEach(async ({ page }) => {
    await applyTheme(page, "light");
    await mockApi(page);
    await signInAsAdmin(page);
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) contains horizontal overflow within components`, async ({
      page
    }) => {
      await page.goto(route.path);
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);

      const viewportOverflow = await page.evaluate(() => {
        const root = document.documentElement;
        const offenders = Array.from(document.querySelectorAll<HTMLElement>("body *"))
          .map((element) => {
            const rect = element.getBoundingClientRect();
            return {
              tag: element.tagName.toLowerCase(),
              className: element.className?.toString().slice(0, 160) ?? "",
              text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) ?? "",
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              width: Math.round(rect.width)
            };
          })
          .filter((element) => element.right > root.clientWidth + 1 || element.left < -1)
          .sort((a, b) => b.right - a.right)
          .slice(0, 8);
        return {
          clientWidth: root.clientWidth,
          scrollWidth: root.scrollWidth,
          offenders
        };
      });

      expect(
        viewportOverflow.scrollWidth,
        `Page-level overflow offenders: ${JSON.stringify(viewportOverflow.offenders)}`
      ).toBeLessThanOrEqual(viewportOverflow.clientWidth + 1);
    });
  }
});
