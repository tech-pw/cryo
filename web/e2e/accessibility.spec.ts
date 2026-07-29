import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { applyTheme, type VisualTheme } from "./visual/helpers";

/**
 * WCAG 2.2 AA gate via axe-core — serious/critical violations block CI.
 * Color-contrast is not blocking at v1 (see disableRules below).
 */
const protectedRoutes = [
  { path: "/health", name: "Health" },
  { path: "/catalogs", name: "Catalogs" },
  { path: "/operations", name: "Operations" },
  { path: "/operations/scanner", name: "Scanner" },
  { path: "/queries", name: "Queries" },
  { path: "/governance", name: "Governance" },
  { path: "/ai", name: "Intelligence" },
  { path: "/cost", name: "Cost" },
  { path: "/connections", name: "Connections" },
  { path: "/smart-maintenance", name: "Smart Maintenance" },
  { path: "/sla", name: "SLA" },
  { path: "/search", name: "Search" }
];

const scanRoute = async (page: import("@playwright/test").Page, path: string) => {
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
      description: `${minor.length} moderate/minor a11y issues on ${path}: ${minor.map((v) => v.id).join(", ")}`
    });
  }

  expect(seriousOrCritical).toEqual([]);
};

test.describe("WCAG 2.2 AA gate (axe-core, light)", () => {
  test.beforeEach(async ({ request }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for axe-core scan");
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) has no serious/critical a11y violations`, async ({ page }) => {
      await scanRoute(page, route.path);
    });
  }
});

const darkRoutes: typeof protectedRoutes = [
  { path: "/health", name: "Health" },
  { path: "/operations", name: "Operations" },
  { path: "/catalogs", name: "Catalogs" }
];

test.describe("WCAG 2.2 AA gate (axe-core, dark)", () => {
  test.beforeEach(async ({ request }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for axe-core scan");
  });

  for (const route of darkRoutes) {
    test(`${route.name} (${route.path}) dark theme`, async ({ page }) => {
      await applyTheme(page, "dark");
      await scanRoute(page, route.path);
    });
  }
});
