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
  demoFastPathFindings,
  demoFleetTrends,
  demoHealthTrends,
  demoPendingApprovals,
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
  demoTablesFunnel,
  demoVitalsBody
} from "./demo-data";
import { demoArchivePreflight, demoTableDetail, demoTableHistory, demoTableRuns, demoTableTabResponse } from "./demo-table-detail";
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

const stageOperationExclusion = async (page: Page): Promise<void> => {
  const section = page.locator("#tc-operations");
  await section.getByRole("switch", { name: "Run Compact small files" }).click();
  await section.getByText("Will be excluded").waitFor();
  await section.evaluate((el) => el.scrollIntoView({ block: "start" }));
};

const openPropertyInfo = async (page: Page): Promise<void> => {
  await page.getByRole("button", { name: /All table properties/ }).click();
  const button = page.getByRole("button", { name: "About write.parquet.bloom-filter-enabled.column.user_id" });
  await button.evaluate((el) => el.closest(".tc-props-wrap")?.scrollIntoView({ block: "start" }));
  await button.click();
  await page.getByRole("button", { name: /Show its setting/ }).waitFor();
};

const openMaintenanceMenu = async (page: Page): Promise<void> => {
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("dialog", { name: "Maintenance actions" }).waitFor();
};

const openTableSettings = async (page: Page): Promise<void> => {
  await page.getByRole("button", { name: "Table settings" }).click();
  await page.getByRole("dialog").getByText("Ownership", { exact: true }).first().waitFor();
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.getSelection()?.removeAllRanges();
  });
};

const EXPLORER = "#table-explorer";
const EXPLORER_TOOLBAR = `${EXPLORER} > :nth-child(-n+2)`;
const POPOVER = "[data-radix-popper-content-wrapper]";

const clearPointer = async (page: Page): Promise<void> => {
  await page.mouse.move(0, 0);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
};

const waitForExplorer = (page: Page) => page.getByRole("link", { name: "analytics.events", exact: true }).waitFor();

const openMoreFilters = async (page: Page): Promise<void> => {
  await waitForExplorer(page);
  await page.getByRole("button", { name: "More filters" }).click();
  await page.locator(POPOVER).waitFor();
  await clearPointer(page);
};

const openColumnsMenu = async (page: Page): Promise<void> => {
  await waitForExplorer(page);
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.locator(POPOVER).waitFor();
};

const selectRows = async (page: Page, rowIndexes: number[]): Promise<void> => {
  await waitForExplorer(page);
  const rows = page.getByRole("checkbox", { name: "Select row" });
  for (const i of rowIndexes) await rows.nth(i).check();
  await page.getByTestId("bulk-assign-bar").waitFor();
  await clearPointer(page);
};

const openRunDialog = async (page: Page): Promise<void> => {
  // analytics.events (failed) and ops.metrics (running), so the likely-skipped note shows.
  await selectRows(page, [0, 6]);
  await page.getByRole("button", { name: "Run maintenance…" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Run workflow").selectOption({ index: 1 });
  await dialog.getByLabel("Run engine").selectOption({ index: 1 });
  await clearPointer(page);
};

const openArchiveDialog = async (page: Page): Promise<void> => {
  const card = page.locator("#archive");
  await card.scrollIntoViewIfNeeded();
  await card.getByRole("button", { name: /Archive all suggested/ }).click();
  await page.getByRole("dialog").getByText("Permissions", { exact: false }).first().waitFor();
};

const TABLE = `/tables/${DEMO_TABLES[0].id}`;
const ACTIVE_PANEL = "[role='tabpanel'][data-state='active']";
const tabShot = (tab: string, waitFor?: string, part?: { name: string; selector: string }) => ({
  name: part ? `table-tab-${tab}-${part.name}` : `table-tab-${tab}`,
  path: `${TABLE}?tab=${tab}`,
  waitFor,
  height: 1100,
  selector: part ? `${ACTIVE_PANEL} ${part.selector}` : ACTIVE_PANEL
});
const MAINTENANCE = ".td-maintenance-workspace";

const SHOTS: Array<{
  name: string;
  path: string;
  waitFor?: string;
  height?: number;
  /** Screenshot only this element instead of the viewport. */
  selector?: string;
  /** Crop the viewport screenshot to its top N pixels. */
  clip?: number;
  /** Crop a `selector` shot to its top N pixels. */
  maxHeight?: number;
  act?: (page: Page) => Promise<void>;
}> = [
  { name: "health", path: "/", waitFor: "Your lake at a glance", height: 1000 },
  { name: "tables", path: "/tables", waitFor: "Last nightly run", height: 1320 },
  {
    name: "tables-triage",
    path: "/tables",
    waitFor: "Last nightly run",
    height: 1000,
    selector: "section[aria-label='Maintenance triage'], section[aria-label='Nightly maintenance and catalogs']"
  },
  { name: "tables-explorer", path: "/tables", height: 1320, act: waitForExplorer, selector: EXPLORER, maxHeight: 640 },
  { name: "tables-filters", path: "/tables", height: 1320, act: openMoreFilters, selector: `${EXPLORER_TOOLBAR}, ${POPOVER}` },
  { name: "tables-columns", path: "/tables", height: 1320, act: openColumnsMenu, selector: `${EXPLORER_TOOLBAR}, ${POPOVER}` },
  { name: "tables-savings", path: "/tables?view=savings", height: 1320, act: waitForExplorer, selector: EXPLORER },
  { name: "tables-bulk", path: "/tables", height: 1320, act: (page) => selectRows(page, [0, 1]), selector: EXPLORER, maxHeight: 420 },
  { name: "tables-run-dialog", path: "/tables", height: 1000, act: openRunDialog, selector: "[role='dialog']" },
  { name: "table-detail", path: TABLE, height: 960 },
  { name: "table-header", path: TABLE, waitFor: "What's pulling the score down", height: 900, selector: ".table-detail-workspace", maxHeight: 400 },
  {
    name: "table-header-menu",
    path: TABLE,
    waitFor: "What's pulling the score down",
    height: 900,
    selector: ".table-detail-workspace",
    maxHeight: 400,
    act: openMaintenanceMenu
  },
  { name: "table-settings", path: TABLE, waitFor: "What's pulling the score down", height: 900, act: openTableSettings },
  tabShot("overview", "What's pulling the score down"),
  tabShot("files", "Small-file candidates over scans"),
  tabShot("snapshots", "Commits"),
  tabShot("maintenance", "Stored benefit-score candidates", { name: "plan", selector: `${MAINTENANCE} > :nth-child(-n+2)` }),
  tabShot("maintenance", "Stored benefit-score candidates", { name: "runs", selector: `${MAINTENANCE} > :nth-child(3)` }),
  tabShot("maintenance", "Stored benefit-score candidates", { name: "compaction", selector: `${MAINTENANCE} > :nth-child(4)` }),
  tabShot("config", "Differs from recommendation", { name: "settings", selector: ".td-config > section:nth-child(-n+2)" }),
  tabShot("usage", "Partition access"),
  tabShot("overview", "Layout checks", { name: "checks", selector: "[aria-labelledby='overview-checks']" }),
  tabShot("quality", "Freshness vs target"),
  tabShot("cost", "Ways to save"),
  tabShot("cost", "Ways to save", { name: "archive", selector: "#archive" }),
  { name: "table-archive-dialog", path: `${TABLE}?tab=cost`, waitFor: "Ways to save", height: 1000, act: openArchiveDialog },
  {
    ...tabShot("config", "Differs from recommendation", { name: "inventory", selector: ".td-config > section:nth-child(3)" }),
    name: "table-config",
    maxHeight: 1150
  },
  {
    name: "table-config-review",
    path: `/tables/${DEMO_TABLES[0].id}?tab=config`,
    waitFor: "Differs from recommendation",
    height: 1100,
    act: stageConfigBatch
  },
  {
    name: "table-config-operations",
    path: `/tables/${DEMO_TABLES[0].id}?tab=config`,
    waitFor: "Maintenance operations",
    height: 1000,
    act: stageOperationExclusion
  },
  {
    name: "table-config-properties",
    path: `/tables/${DEMO_TABLES[0].id}?tab=config`,
    waitFor: "Differs from recommendation",
    height: 900,
    act: openPropertyInfo
  },
  tabShot("overview", "What maintenance achieved", { name: "history", selector: "[aria-labelledby='overview-history']" }),
  tabShot("files", "Query use · 30 days", { name: "schema", selector: "#files-schema" }),
  tabShot("files", "Delete files over scans", { name: "deletes", selector: "[data-series='delete files']" }),
  tabShot("files", "Suggested partition change", { name: "partitions", selector: "#files-partitions" }),
  {
    name: "table-tab-snapshots-selected",
    path: `${TABLE}?tab=snapshots&snapshot=8812286588`,
    waitFor: "Selected snapshot",
    height: 1100,
    selector: `${ACTIVE_PANEL} .td-snapshots-selection`
  },
  tabShot("config", "More suggestions from the last scan", { name: "suggestions", selector: ".tc-suggestions" }),
  {
    name: "table-settings-hold",
    path: TABLE,
    waitFor: "What's pulling the score down",
    height: 1100,
    act: openTableSettings,
    selector: "[role='dialog'] [aria-labelledby='settings-hold']"
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
      if (p.endsWith("/api/v1/tables/maintenance-funnel")) return json(route, demoTablesFunnel());
      if (/\/api\/v1\/tables\/[^/]+\/config\/preview$/.test(p)) return json(route, demoTableConfig.preview);
      if (/\/api\/v1\/tables\/[^/]+\/tiering\/preflight$/.test(p)) return json(route, demoArchivePreflight(route.request().postDataJSON()));
      const sub = p.match(/\/api\/v1\/tables\/([^/]+)\/([a-z-]+)$/);
      if (sub) {
        const [, id, kind] = sub;
        if (kind === "audit") return json(route, demoAuditBody());
        if (kind === "health-trends") return json(route, demoHealthTrends(id));
        if (kind === "effective-preset") return json(route, demoEffectivePreset(id));
        if (kind === "signals") return json(route, demoSignals(id));
        if (kind === "cost") return json(route, demoTableCost(id));
        if (kind === "compatible-presets") return json(route, demoPresets());
        if (kind === "history") return json(route, demoTableHistory(id));
        if (kind === "runs") return json(route, demoTableRuns(id));
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
      if (one && one[1] !== "export.csv" && one[1] !== "group-by") return json(route, demoTableDetail(one[1]));
      if (p.endsWith("/api/v1/dashboard/fleet-trends")) return json(route, demoFleetTrends());
      if (p.endsWith("/api/v1/advanced/approvals/pending")) return json(route, demoPendingApprovals());
      if (p.endsWith("/api/v1/advanced/fast-path-findings") && !url.searchParams.has("table_id")) {
        return json(route, demoFastPathFindings());
      }
      const tab = demoTableTabResponse(p, url);
      if (tab !== undefined) return json(route, tab);
      if (p.endsWith("/api/v1/dashboards/datalake")) return json(route, demoDashboardBody());
      if (p.endsWith("/api/v1/vitals")) return json(route, demoVitalsBody());
      if (p.endsWith("/api/v1/runs")) {
        const pipeline = url.searchParams.get("pipeline");
        const runs = pipeline ? DEMO_RUN_HISTORY.filter((run) => (run.summary as Record<string, unknown>).pipeline === pipeline) : DEMO_RUN_HISTORY;
        return json(route, runs.slice(0, Number(url.searchParams.get("limit") ?? runs.length)));
      }
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
      const path = join(OUT, `${shot.name}-${theme}.png`);
      if (shot.selector) {
        // The app scrolls inside its own container, so off-screen parts of a tall panel never paint.
        const parts = page.locator(shot.selector);
        const bottom = await parts.evaluateAll((els) => Math.max(...els.map((el) => el.getBoundingClientRect().top + el.scrollHeight)));
        await page.setViewportSize({ width: 1440, height: Math.ceil(bottom) + 48 });
        await settlePage(page);
        await page.waitForTimeout(400);
        const boxes = (await Promise.all((await parts.all()).map((part) => part.boundingBox()))).filter((b) => b != null);
        if (!boxes.length) throw new Error(`${shot.selector} has no box`);
        const pad = 16;
        const x = Math.min(...boxes.map((b) => b.x)) - pad;
        const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
        const right = Math.max(...boxes.map((b) => b.x + b.width)) + pad;
        const full = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
        const end = shot.maxHeight ? Math.min(full, y + shot.maxHeight) : full;
        await page.screenshot({ path, animations: "disabled", clip: { x, y, width: right - x, height: end - y } });
      } else {
        const clip = shot.clip ? { x: 0, y: 0, width: 1440, height: shot.clip } : undefined;
        await page.screenshot({ path, animations: "disabled", clip });
      }
      if (unhandled.size) console.log(`[${shot.name}] unhandled:`, [...unhandled].join(", "));
    });
  }
}
