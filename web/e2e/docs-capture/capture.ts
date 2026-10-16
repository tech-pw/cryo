import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { test, type Page, type Route } from "@playwright/test";

import { applyTheme, installVisualMocks, settlePage } from "../visual/helpers";
import {
  DEMO_RUN,
  DEMO_RUN_ID,
  DEMO_TABLES,
  demoAccessToken,
  demoAuditBody,
  demoEffectivePreset,
  demoEngineLoad,
  demoHealthTrends,
  demoMultiCatalog,
  demoSignals,
  demoTableCost,
  demoDashboardBody,
  demoDestinations,
  demoJobsBody,
  demoPresets,
  demoScannerStatus,
  demoSources,
  demoTablesBody,
  demoVitalsBody
} from "./demo-data";

// Writes docs-site screenshots of the real UI against synthetic demo data.
// Run: npx playwright test -c e2e/docs-capture/playwright.config.ts
const OUT = fileURLToPath(new URL("../../../site/assets/img/screens/", import.meta.url));
const THEMES = ["light", "dark"] as const;

const SHOTS: Array<{ name: string; path: string; waitFor?: string; height?: number }> = [
  { name: "health", path: "/", waitFor: "Your lake at a glance", height: 1000 },
  { name: "tables", path: "/tables", height: 1320 },
  { name: "table-detail", path: `/tables/${DEMO_TABLES[0].id}`, height: 960 },
  { name: "runs", path: "/runs", height: 820 },
  { name: "run-detail", path: `/runs/${DEMO_RUN_ID}`, height: 960 },
  { name: "connections", path: "/settings/connections", waitFor: "Iceberg catalogs Cryo reads", height: 820 },
  { name: "connections-engines", path: "/settings/connections?tab=engines", waitFor: "Shipped with Cryo", height: 900 }
];

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });

async function installDemo(page: Page, unhandled: Set<string>): Promise<void> {
  await installVisualMocks(page, "populated");
  await page.addInitScript((token) => {
    localStorage.setItem("cryo.accessToken", token);
    localStorage.setItem("cryo.devSubject", "demo");
    const style = document.createElement("style");
    style.textContent = "[aria-label='Ask Cryo'][class*='fixed'], button.fixed, .fixed.bottom-6.right-6 { display: none !important; }";
    document.addEventListener("DOMContentLoaded", () => document.head.appendChild(style));
  }, demoAccessToken());
  await page.route(
    (url) => url.pathname.includes("/api/v1/"),
    async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname.replace(/\/$/, "");
      if (route.request().method() === "OPTIONS") return route.fallback();
      if (p.endsWith("/api/v1/auth/token")) return json(route, { access_token: demoAccessToken(), token_type: "bearer", role: "operator" });
      if (p.endsWith("/api/v1/tables")) return json(route, demoTablesBody(url));
      const sub = p.match(/\/api\/v1\/tables\/([^/]+)\/([a-z-]+)$/);
      if (sub) {
        const [, id, kind] = sub;
        if (kind === "audit") return json(route, demoAuditBody());
        if (kind === "health-trends") return json(route, demoHealthTrends(id));
        if (kind === "effective-preset") return json(route, demoEffectivePreset(id));
        if (kind === "signals") return json(route, demoSignals(id));
        if (kind === "cost") return json(route, demoTableCost(id));
        if (kind === "compatible-presets") return json(route, demoPresets());
        if (kind === "history" || kind === "runs") return json(route, { items: [] });
      }
      if (p.endsWith("/api/v1/engine-load")) return json(route, demoEngineLoad());
      if (p.endsWith("/api/v1/multi-catalog")) return json(route, demoMultiCatalog());
      if (p.includes("/api/v1/advanced/silent-data-loss/")) return json(route, null);
      const one = p.match(/\/api\/v1\/tables\/([^/]+)$/);
      if (one && one[1] !== "export.csv" && one[1] !== "group-by") {
        return json(route, DEMO_TABLES.find((t) => t.id === one[1]) ?? DEMO_TABLES[0]);
      }
      if (p.endsWith("/api/v1/dashboards/datalake")) return json(route, demoDashboardBody());
      if (p.endsWith("/api/v1/vitals")) return json(route, demoVitalsBody());
      if (p.endsWith("/api/v1/runs")) return json(route, [DEMO_RUN]);
      const jobs = p.match(/\/api\/v1\/runs\/([^/]+)\/jobs$/);
      if (jobs) return json(route, demoJobsBody(jobs[1]));
      const run = p.match(/\/api\/v1\/runs\/([^/]+)$/);
      if (run) return json(route, { ...DEMO_RUN, id: run[1] });
      if (p.endsWith("/api/v1/scanner/status")) return json(route, demoScannerStatus());
      if (p.endsWith("/api/v1/sources")) return json(route, demoSources());
      if (p.endsWith("/api/v1/destinations")) return json(route, demoDestinations());
      if (p.endsWith("/api/v1/presets")) return json(route, demoPresets());
      unhandled.add(`${route.request().method()} ${p}`);
      return route.fallback();
    }
  );
}

for (const theme of THEMES) {
  for (const shot of SHOTS) {
    test(`${shot.name} (${theme})`, async ({ page }) => {
      mkdirSync(OUT, { recursive: true });
      const unhandled = new Set<string>();
      await page.setViewportSize({ width: 1440, height: shot.height ?? 900 });
      page.on("console", async (m) => {
        if (m.type() !== "error" || !process.env.DOCS_CAPTURE_DEBUG) return;
        const stacks = await Promise.all(m.args().map((a) => a.evaluate((v) => (v instanceof Error ? v.stack : null)).catch(() => null)));
        console.log(`[${shot.name}] console:`, m.text().slice(0, 300), stacks.filter(Boolean).join("\n").slice(0, 900));
      });
      await applyTheme(page, theme);
      await installDemo(page, unhandled);
      await page.goto(shot.path);
      if (shot.waitFor) await page.getByText(shot.waitFor).first().waitFor({ timeout: 60_000 });
      await settlePage(page);
      await page.waitForTimeout(600);
      await page.screenshot({ path: join(OUT, `${shot.name}-${theme}.png`), animations: "disabled" });
      if (unhandled.size) console.log(`[${shot.name}] unhandled:`, [...unhandled].join(", "));
    });
  }
}
