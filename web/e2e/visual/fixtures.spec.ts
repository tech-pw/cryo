import { expect, test, type Page } from "@playwright/test";

import {
  JOB_LIST_TOTAL,
  LONG_SCHEMA_NAME,
  LONG_TABLE_NAME,
  MAX_PAGE_SIZE,
  PREVIOUS_YEAR_COMMITTED_AT,
  SNAPSHOT_HISTORY_TOTAL,
  SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER,
  STALE_AS_OF,
  TABLE_LIST_TOTAL,
  TINY_SIZE_BYTES,
  TINY_SIZE_GB,
  VISUAL_NOW_ISO,
  VISUAL_RUN_ID,
  VISUAL_SCENARIOS,
  VISUAL_TIMEZONE,
  resolveVisualResponse,
  snapshotIdExceedsJsSafeInteger,
  visualHealthz,
  type VisualScenario
} from "./fixtures";
import { installVisualMocks, settlePage } from "./helpers";

// Document is fulfilled here so these assertions do not need Vite (:5173) or the API (:8000).
const STUB_ORIGIN = "http://127.0.0.1:9";

async function openStub(page: Page, scenario: VisualScenario): Promise<void> {
  await page.route(`${STUB_ORIGIN}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/healthz" || path.includes("/api/v1/")) {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><head></head><body></body></html>"
    });
  });
  await installVisualMocks(page, scenario);
  await page.goto(`${STUB_ORIGIN}/`);
  await settlePage(page);
}

async function readJson(
  page: Page,
  path: string,
  init?: { method?: string; body?: unknown }
): Promise<{ status: number; body: any }> {
  return page.evaluate(
    async ({ path, init }) => {
      const response = await fetch(path, {
        method: init?.method ?? "GET",
        headers: init?.body === undefined ? undefined : { "Content-Type": "application/json" },
        body: init?.body === undefined ? undefined : JSON.stringify(init.body)
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : null };
    },
    { path, init }
  );
}

test("installer answers healthz, auth token, and one page of 10000 tables", async ({ page }) => {
  await openStub(page, "tables-10k");

  const health = await readJson(page, "/api/v1/healthz");
  expect(health.status).toBe(200);
  expect(health.body).toEqual(visualHealthz());

  const token = await readJson(page, "/api/v1/auth/token", {
    method: "POST",
    body: { role: "operator", subject: "visual-e2e" }
  });
  expect(token.status).toBe(200);
  expect(token.body.token_type).toBe("bearer");
  expect(typeof token.body.access_token).toBe("string");

  const tables = await readJson(page, "/api/v1/tables?limit=10000&offset=0");
  expect(tables.status).toBe(200);
  expect(tables.body.total).toBe(TABLE_LIST_TOTAL);
  expect(tables.body.limit).toBe(MAX_PAGE_SIZE);
  expect(tables.body.items).toHaveLength(MAX_PAGE_SIZE);

  const tail = await readJson(page, "/api/v1/tables?limit=50&offset=9980");
  expect(tail.body.total).toBe(TABLE_LIST_TOTAL);
  expect(tail.body.items).toHaveLength(20);

  const clock = await page.evaluate(async () => {
    await document.fonts.ready;
    return {
      iso: new Date().toISOString(),
      hours: new Date().getHours(),
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches
    };
  });
  expect(clock.iso).toBe(VISUAL_NOW_ISO);
  expect(clock.timeZone).toBe(VISUAL_TIMEZONE);
  expect(clock.hours).toBe(10);
  expect(clock.reducedMotion).toBe(true);
});

test("installer returns one page of a 1500-job run", async ({ page }) => {
  await openStub(page, "run-1500");
  const jobs = await readJson(page, `/api/v1/runs/${VISUAL_RUN_ID}/jobs?limit=50&offset=0`);
  expect(jobs.status).toBe(200);
  expect(jobs.body.total).toBe(JOB_LIST_TOTAL);
  expect(jobs.body.jobs).toHaveLength(50);
  expect(jobs.body.run.summary.jobs_total).toBe(JOB_LIST_TOTAL);
});

test("installer returns a page of snapshot history with an id above 2^53", async ({ page }) => {
  await openStub(page, "snapshot-history");
  const snapshots = await readJson(
    page,
    "/api/v1/tables/00000000-0000-4000-8000-000000000010/snapshots?page=1&page_size=25"
  );
  expect(snapshots.status).toBe(200);
  expect(snapshots.body.total).toBe(SNAPSHOT_HISTORY_TOTAL);
  expect(snapshots.body.snapshots).toHaveLength(25);
  expect(snapshots.body.snapshots[0].snapshot_id).toBe(SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER);
  expect(typeof snapshots.body.snapshots[0].snapshot_id).toBe("string");
  expect(snapshotIdExceedsJsSafeInteger(snapshots.body.snapshots[0].snapshot_id)).toBe(true);
  expect(snapshots.body.snapshots[0].summary.total_files_size).toBe(TINY_SIZE_BYTES);
  expect(snapshots.body.snapshots[1].committed_at).toBe(PREVIOUS_YEAR_COMMITTED_AT);
  expect(snapshots.body.snapshots[1].parent_snapshot_id).toBe(SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER);
});

test("installer keeps healthz up when data routes fail", async ({ page }) => {
  await openStub(page, "api-failure");
  const health = await readJson(page, "/api/v1/healthz");
  expect(health.status).toBe(200);
  const tables = await readJson(page, "/api/v1/tables");
  expect(tables.status).toBe(503);
  expect(tables.body.detail).toContain("upstream unavailable");
});

test("fixture module covers required states without a live API", () => {
  const healthUrl = new URL("http://127.0.0.1/api/v1/healthz");
  for (const scenario of VISUAL_SCENARIOS) {
    expect(resolveVisualResponse(scenario, "GET", healthUrl).status).toBe(200);
    expect(resolveVisualResponse(scenario, "GET", healthUrl).body).toEqual(visualHealthz());
  }

  const tablesUrl = new URL("http://127.0.0.1/api/v1/tables?limit=50&offset=0");
  const unknown = resolveVisualResponse("unknown", "GET", tablesUrl).body as {
    items: Array<{ stats: { grade: null; total_size_gb: null; total_size_bytes: null } }>;
  };
  expect(unknown.items[0].stats.grade).toBeNull();
  expect(unknown.items[0].stats.total_size_gb).toBeNull();
  expect(unknown.items[0].stats.total_size_bytes).toBeNull();

  const partial = resolveVisualResponse("partial", "GET", new URL("http://127.0.0.1/api/v1/vitals")).body as {
    impact: { measurement_state: string; measured: number; succeeded: number };
  };
  expect(partial.impact).toEqual({ measurement_state: "partial", measured: 8, succeeded: 12, direction: null, gb_freed_yesterday: 1.5 });

  const stale = resolveVisualResponse("stale", "GET", new URL("http://127.0.0.1/api/v1/dashboards/datalake")).body as {
    stale: boolean;
    freshness_as_of: string;
  };
  expect(stale.stale).toBe(true);
  expect(stale.freshness_as_of).toBe(STALE_AS_OF);

  expect(resolveVisualResponse("api-failure", "GET", tablesUrl).status).toBe(503);

  const viewer = resolveVisualResponse("viewer", "POST", new URL("http://127.0.0.1/api/v1/auth/token"), {
    role: "admin"
  }).body as { role: string };
  expect(viewer.role).toBe("viewer");

  const runtime = resolveVisualResponse("write-disabled", "GET", new URL("http://127.0.0.1/api/v1/runtime")).body as {
    maintenance_writes_enabled: boolean;
  };
  expect(runtime.maintenance_writes_enabled).toBe(false);

  const destinations = resolveVisualResponse(
    "unsupported-engine",
    "GET",
    new URL("http://127.0.0.1/api/v1/destinations")
  ).body as Array<{ engine: string; support: string }>;
  expect(destinations[0]).toMatchObject({
    engine: "flink",
    support: "unsupported"
  });
  const matrix = resolveVisualResponse(
    "unsupported-engine",
    "GET",
    new URL("http://127.0.0.1/api/v1/capabilities")
  ).body as { matrix: Array<{ support: string }> };
  expect(matrix.matrix[0].support).toBe("unsupported");

  expect(resolveVisualResponse("first-use-empty", "GET", new URL("http://127.0.0.1/api/v1/sources")).body).toEqual([]);
  const emptyTables = resolveVisualResponse("first-use-empty", "GET", tablesUrl).body as {
    total: number;
    items: unknown[];
  };
  expect(emptyTables).toMatchObject({ total: 0, items: [] });

  const named = resolveVisualResponse("long-names", "GET", tablesUrl).body as {
    items: Array<{ table_name: string; schema_name: string }>;
  };
  expect(named.items[0].table_name).toBe(LONG_TABLE_NAME);
  expect(named.items[0].schema_name).toBe(LONG_SCHEMA_NAME);
  expect(named.items[0].table_name.length).toBeGreaterThan(80);

  const dated = resolveVisualResponse("previous-year", "GET", tablesUrl).body as {
    items: Array<{ stats: { last_snapshot_committed_at: string } }>;
  };
  expect(dated.items[0].stats.last_snapshot_committed_at).toBe(PREVIOUS_YEAR_COMMITTED_AT);
  expect(dated.items.every((item) => item.stats.last_snapshot_committed_at.startsWith("2025-"))).toBe(true);

  const tiny = resolveVisualResponse("tiny-bytes", "GET", tablesUrl).body as {
    items: Array<{ stats: { total_size_bytes: number; total_size_gb: number } }>;
  };
  expect(tiny.items[0].stats.total_size_bytes).toBe(TINY_SIZE_BYTES);
  expect(tiny.items[0].stats.total_size_bytes).toBe(40 * 1024 * 1024);
  expect(tiny.items[0].stats.total_size_gb).toBe(TINY_SIZE_GB);
  expect(tiny.items[0].stats.total_size_gb).toBeGreaterThan(0);
  expect(tiny.items[0].stats.total_size_gb).toBeLessThan(1);

  const paged = resolveVisualResponse(
    "tables-10k",
    "GET",
    new URL("http://127.0.0.1/api/v1/tables?limit=10000&offset=9980")
  ).body as { total: number; limit: number; items: unknown[] };
  expect(paged.total).toBe(TABLE_LIST_TOTAL);
  expect(paged.limit).toBe(MAX_PAGE_SIZE);
  expect(paged.items).toHaveLength(20);

  const jobs = resolveVisualResponse(
    "run-1500",
    "GET",
    new URL(`http://127.0.0.1/api/v1/runs/${VISUAL_RUN_ID}/jobs?limit=50&offset=0`)
  ).body as { total: number; jobs: unknown[]; run: { summary: { jobs_total: number } } };
  expect(jobs.total).toBe(JOB_LIST_TOTAL);
  expect(jobs.jobs).toHaveLength(50);
  expect(jobs.run.summary.jobs_total).toBe(JOB_LIST_TOTAL);

  const history = resolveVisualResponse(
    "snapshot-history",
    "GET",
    new URL("http://127.0.0.1/api/v1/tables/visual/snapshots?page=1&page_size=25")
  ).body as {
    total: number;
    current_snapshot_id: string;
    snapshots: Array<{ snapshot_id: string; summary: { total_files_size: number } }>;
  };
  expect(history.total).toBe(SNAPSHOT_HISTORY_TOTAL);
  expect(history.snapshots).toHaveLength(25);
  expect(history.current_snapshot_id).toBe(SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER);
  expect(snapshotIdExceedsJsSafeInteger(history.snapshots[0].snapshot_id)).toBe(true);
  expect(history.snapshots[0].summary.total_files_size).toBe(TINY_SIZE_BYTES);
});
