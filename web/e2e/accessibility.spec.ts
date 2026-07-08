import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

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
 *   /health      → Datalake health overview
 *   /catalogs    → Catalog / table explorer
 *   /scans       → Scanner
 *   /runs        → Runs / jobs
 *   /presets     → Optimisation presets
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
  { path: "/cost", name: "Cost" }
];

test.describe("WCAG 2.2 AA gate (axe-core)", () => {
  test.beforeEach(async ({ request }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for axe-core scan");
  });

  for (const route of protectedRoutes) {
    test(`${route.name} (${route.path}) has no serious/critical a11y violations`, async ({
      page
    }) => {
      await page.goto(route.path);
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
          `${seriousOrCritical.length} serious/critical a11y violations on ${route.path}:\n${summary}`
        );
      }

      const minor = results.violations.filter(
        (v) => v.impact === "moderate" || v.impact === "minor"
      );
      if (minor.length > 0) {
        test.info().annotations.push({
          type: "moderate-minor-violations",
          description: `${minor.length} moderate/minor a11y issues on ${route.path}: ${minor
            .map((v) => v.id)
            .join(", ")}`
        });
      }

      expect(seriousOrCritical).toEqual([]);
    });
  }
});
