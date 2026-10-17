import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { test, type Page, type Route } from "@playwright/test";

import { applyTheme, installVisualMocks, settlePage } from "../visual/helpers";
import {
  DEMO_RUN,
  DEMO_RUN_HISTORY,
  DEMO_RUN_ID,
  demoMaintenanceControls,
  demoSafetySummary,
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
import demoTableConfig from "./demo-table-config.json" with { type: "json" };

// Writes docs-site screenshots of the real UI against synthetic demo data.
// Run: npx playwright test -c e2e/docs-capture/playwright.config.ts
const OUT = fileURLToPath(new URL("../../../site/assets/img/screens/", import.meta.url));
const THEMES = ["light", "dark"] as const;

const stageConfigBatch = async (page: Page): Promise<void> => {
  const row = (key: string) => page.getByTestId(`setting-${key}`);
  await row("write.target-file-size-bytes").getByRole("button", { name: /Stage recommendation/ }).click();
  await row("write.distribution-mode").getByRole("button", { name: /Stage recommendation/ }).click();
  const age = row("history.expire.max-snapshot-age-ms");
  await age.getByRole("button", { name: /^Edit / }).click();
  await age.getByRole("spinbutton").fill("1");
  await age.getByRole("button", { name: "Stage change" }).click();
  await page.getByRole("button", { name: "Review and apply" }).click();
  await page.getByText("This batch contains a high-risk change", { exact: false }).waitFor();
};

const SHOTS: Array<{
  name: string;
  path: string;
  waitFor?: string;
  height?: number;
  act?: (page: Page) => Promise<void>;
}> = [
  { name: "health", path: "/", waitFor: "Your lake at a glance", height: 1000 },
  { name: "tables", path: "/tables", height: 1320 },
  { name: "table-detail", path: `/tables/${DEMO_TABLES[0].id}`, height: 960 },
  {
    name: "table-config",
    path: `/tables/${DEMO_TABLES[0].id}?tab=config`,
    waitFor: "Differs from recommendation",
    height: 2200
  },
  {
    name: "table-config-review",
    path: `/tables/${DEMO_TABLES[0].id}?tab=config`,
    waitFor: "Differs from recommendation",
    height: 1100,
    act: stageConfigBatch
  },
  { name: "runs", path: "/runs", waitFor: "Success rate · 7d", height: 1100 },
  { name: "runs-holds", path: "/runs?tab=holds", waitFor: "Active holds", height: 900 },
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
      if (/\/api\/v1\/tables\/[^/]+\/config\/preview$/.test(p)) return json(route, demoTableConfig.preview);
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
        if (kind === "config") return json(route, { ...demoTableConfig.config, table_id: id });
        if (kind === "effective-params") {
          const params = { file_size_mb: 256, retention_days: 7, min_small_files: 20 };
          return json(route, {
            table_id: id,
            preset_name: "nightly-standard",
            preset_params: params,
            param_overrides: {},
            effective_params: params
          });
        }
      }
      if (p.endsWith("/api/v1/engine-load")) return json(route, demoEngineLoad());
      if (p.endsWith("/api/v1/governor/offpeak-window")) {
        return json(route, { start_hour_ist: 1, end_hour_ist: 7, enabled: true });
      }
      if (p.endsWith("/api/v1/multi-catalog")) return json(route, demoMultiCatalog());
      if (p.includes("/api/v1/advanced/silent-data-loss/")) return json(route, null);
      const one = p.match(/\/api\/v1\/tables\/([^/]+)$/);
      if (one && one[1] !== "export.csv" && one[1] !== "group-by") {
        return json(route, DEMO_TABLES.find((t) => t.id === one[1]) ?? DEMO_TABLES[0]);
      }
      if (p.endsWith("/api/v1/dashboards/datalake")) return json(route, demoDashboardBody());
      if (p.endsWith("/api/v1/vitals")) return json(route, demoVitalsBody());
      if (p.endsWith("/api/v1/runs")) return json(route, DEMO_RUN_HISTORY);
      const jobs = p.match(/\/api\/v1\/runs\/([^/]+)\/jobs$/);
      if (jobs) return json(route, demoJobsBody(jobs[1]));
      const run = p.match(/\/api\/v1\/runs\/([^/]+)$/);
      if (run) return json(route, { ...DEMO_RUN, id: run[1] });
      if (p.endsWith("/api/v1/maintenance/control")) return json(route, demoMaintenanceControls());
      if (p.endsWith("/api/v1/maintenance/safety/summary")) return json(route, demoSafetySummary());
      if (p.endsWith("/api/v1/maintenance/safety/intents") || p.endsWith("/api/v1/maintenance/safety/effects")) {
        return json(route, []);
      }
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
      if (shot.act) await shot.act(page);
      await settlePage(page);
      await page.waitForTimeout(600);
      await page.screenshot({ path: join(OUT, `${shot.name}-${theme}.png`), animations: "disabled" });
      if (unhandled.size) console.log(`[${shot.name}] unhandled:`, [...unhandled].join(", "));
    });
  }
}
