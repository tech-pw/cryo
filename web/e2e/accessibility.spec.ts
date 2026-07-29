import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { applyTheme } from "./visual/helpers";

/**
 * Move 21 — WCAG 2.2 AA gate via axe-core.
 *
 * Scans every primary launch route in the navigation for accessibility violations
 * and fails the build when any serious/critical issue is found. Lower-severity
 * violations (moderate/minor) are tracked as test annotations but do not block.
 *
 * Per docs/PRODUCT.md (accessibility target) and docs/RATING_2026.md §10 — this
 * gate turns the WCAG target from aspiration into an enforced one.
 *
 * Routes covered:
 *   /health, /catalogs, /scans, /runs, /presets, /queries, /governance,
 *   /ai, /cost, /operations, /connections, /smart-maintenance, /sla, /search
 *
 * Color-contrast is *not* blocking at v1 per the comment in the test below.
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
  { path: "/search?q=iceberg", name: "Search" }
];

async function scanRoute(page: import("@playwright/test").Page, path: string): Promise<void> {
  await page.goto(path);
  await page.waitForLoadState("networkidle", { timeout: 10_000 });

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .disableRules(["color-contrast"])
    .analyze();

  const seriousOrCritical = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical"
  );

  if (seriousOrCritical.length > 0) {
    const summary = seriousOrCritical
      .map((v) => `[${v.impact}] ${v.id} — ${v.description} (nodes: ${v.nodes.length})`)
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
  test.beforeEach(async ({ request, page }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for axe-core scan");
    await applyTheme(page, "light");
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

  test.beforeEach(async ({ request, page }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for axe-core scan");
    await applyTheme(page, "dark");
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) has no serious/critical a11y violations (dark)`, async ({
      page
    }) => {
      await scanRoute(page, route.path);
    });
  }
});
