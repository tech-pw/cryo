import type { Page, Route } from "@playwright/test";

import { installVisualMocks } from "../visual/helpers";
import {
  DEMO_RUN,
  DEMO_RUN_HISTORY,
  demoMaintenanceControls,
  demoSafetySummary,
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
import demoPathToA from "./demo-path-to-a.json" with { type: "json" };
import demoTableConfig from "./demo-table-config.json" with { type: "json" };

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });

/** Answers every API call the docs captures make from the synthetic demo lake. Unanswered calls land in `unhandled`. */
export async function installDemo(page: Page, unhandled: Set<string>): Promise<void> {
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
        if (kind === "path-to-a") return json(route, { ...demoPathToA, table_id: id });
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
      if (p.endsWith("/api/v1/version")) return json(route, { version: "0.2.0", git_sha: null, built_at: null, schema_revision: null });
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
