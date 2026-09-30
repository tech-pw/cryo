import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { applyTheme, installVisualMocks, settlePage } from "./visual/helpers";
import { VISUAL_TABLE_ID, VISUAL_PRESET_ID, VISUAL_SOURCE_ID, VISUAL_NOW_ISO } from "./visual/fixtures/constants";

const root = `/api/v1/tables/${VISUAL_TABLE_ID}`;
const bins = Array.from({ length: 40 }, (_, i) => ({ 21: 150, 23: 260, 25: 180, 27: 80, 28: 37 }[i] ?? 0));
const table = {
  id: VISUAL_TABLE_ID, source_id: VISUAL_SOURCE_ID, source_name: "Production lake", catalog_name: "lake",
  schema_name: "analytics", table_name: "daily_video_performance", owner_email: "data-platform@example.com",
  is_active: true, location: "s3://example-lake/analytics/daily_video_performance", partition_columns: ["event_date"],
  stats: {
    scan_at: VISUAL_NOW_ISO, overall_score: 64, grade: "C", total_size_gb: 42.6, total_files: 707,
    small_files_count: 330, total_snapshots: 123, avg_file_size_mb: 61.7, total_partitions: 90,
    total_delete_files: 12, manifest_count: 24, partition_skew_ratio: 4.2, query_count_30d: 18240,
    total_records: 8400000, last_snapshot_committed_at: VISUAL_NOW_ISO,
    raw: {
      reduction_target_bytes: 268435456, file_size_bins: { data: bins }, commits_per_day: 8.4,
      health_deductions: [
        { label: "Small-file fragmentation", deduction: 20, severity: "critical", reason: "Many data files are below the configured target size." },
        { label: "Snapshot retention buildup", deduction: 8, severity: "warning", reason: "123 snapshots retained across the recorded history." },
        { label: "Metadata overhead", deduction: 8, severity: "warning", reason: "Manifest fragmentation increases planning work." }
      ]
    }
  }
};

async function detail(page: Page, theme: "light" | "dark" = "light", missing = false) {
  await installVisualMocks(page);
  await applyTheme(page, theme);
  const responses: Record<string, unknown> = {
    [root]: missing ? { ...table, stats: { ...table.stats, raw: {} } } : table,
    [`${root}/effective-preset`]: { preset_id: VISUAL_PRESET_ID, preset_name: "Nightly standard", provenance: "assignment" },
    [`${root}/audit`]: { items: [{ id: 1, action: "optimize", status: "OK", ts: "2026-09-27T02:00:00Z", duration_ms: 94000, input_files_count: 980, output_files_count: 707, actual_bytes_freed: 2147483648, engine: "trino" }] },
    [`${root}/signals`]: { table_id: VISUAL_TABLE_ID, signals: [], any_triggered: false },
    [`${root}/maintenance-status`]: { eligible: true, blocking_reasons: [], evaluated_at: VISUAL_NOW_ISO, last_actions: [], thresholds: {}, omitted: [] },
    [`${root}/health-trends`]: { trend_points: [48, 48, 56, 56, 64, 64].map((score, i) => ({ timestamp: `2026-09-${20 + i}T04:30:00Z`, score, grade: score < 60 ? "D" : "C" })) },
    [`${root}/cost`]: { table_id: VISUAL_TABLE_ID, current_monthly_cost_usd: 28.75, request_cost_usd_month: 12.40, live_bytes: 42 * 2 ** 30, dead_bytes: 8 * 2 ** 30, orphan_bytes: 2 * 2 ** 30, metadata_bytes: 2 ** 28, projected_monthly_savings_usd: 3.75, recommendations: [] },
    [`${root}/compliance`]: { abac_policy_count: 1, pending_deletion: null },
    [`/api/v1/advanced/silent-data-loss/${VISUAL_TABLE_ID}`]: null
  };
  await page.route(url => Object.hasOwn(responses, url.pathname), route => route.fulfill({ contentType: "application/json", body: JSON.stringify(responses[new URL(route.request().url()).pathname]) }));
  await page.goto(`/tables/${VISUAL_TABLE_ID}`);
  await expect(page.getByRole("heading", { name: "daily_video_performance", exact: true })).toBeVisible();
  await settlePage(page);
}

for (const theme of ["light", "dark"] as const) {
  test(`Table overview ${theme}: evidence, layout and accessibility`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await detail(page, theme);
    await expect(page.getByRole("heading", { name: "What's pulling the score down" })).toBeVisible();
    await expect(page.getByRole("region", { name: "File layout" })).toContainText("undersized ranges");
    await expect(page.getByRole("region", { name: "History & activity" })).toContainText("980 → 707 files");
    const result = await new AxeBuilder({ page }).include(".table-detail-workspace").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations).toEqual([]);
    await page.screenshot({ path: `/tmp/cryo-table-${theme}.png`, fullPage: true });
  });
}

for (const width of [320, 768, 1280]) {
  test(`Table overview fits ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await detail(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `/tmp/cryo-table-${width}.png`, fullPage: true });
  });
}

test("Missing diagnosis stays unknown", async ({ page }) => {
  await detail(page, "light", true);
  await expect(page.getByRole("heading", { name: "Score explanation unavailable" })).toBeVisible();
  await expect(page.getByText("Every health check passed on the last scan.")).toHaveCount(0);
});
