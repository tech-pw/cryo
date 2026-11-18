import type { StorageOverview } from "../../api/storageOverview";

const TB = 1e12;

/** Test fixture: a measured day on a partly listed fleet. */
export const storageFixture = (overrides: Partial<StorageOverview> = {}): StorageOverview => ({
  generated_at: "2026-11-13T12:00:00Z",
  breakdown: {
    live_bytes: 180 * TB,
    retained_history_bytes: 22 * TB,
    dead_bytes: 14 * TB,
    orphan_bytes: 6 * TB,
    metadata_bytes: 2 * TB,
    total_bytes: 224 * TB,
    compaction_savings_bytes: 3 * TB,
    tables_total: 1044,
    tables_with_cost: 1040,
    tables_with_listing: 812,
    tables_listed_recently: 300,
    orphan_bytes_estimated: 4 * TB,
    retained_history_bytes_estimated: 15 * TB,
    listing_fresh_days: 7,
    price_per_gb_month: 0.023
  },
  freed_24h: {
    bytes_freed: 1.5 * TB,
    actions_succeeded: 400,
    actions_measured: 300,
    tables_cleaned: 212,
    window_start: "2026-11-12T12:00:00Z",
    window_end: "2026-11-13T12:00:00Z",
    bytes_by_source: null,
    savings_usd_month: 35.33
  },
  cumulative: { bytes_freed: 41 * TB, since: "2026-09-28", days_recorded: 46, days_unmeasured: 3 },
  trend: Array.from({ length: 60 }, (_, i) => {
    const day = new Date(Date.UTC(2026, 8, 15 + i)).toISOString().slice(0, 10);
    return {
      day,
      total_bytes: Math.round((260 - i * 0.6) * TB),
      live_bytes: i < 20 ? null : Math.round((176 + i * 0.07) * TB),
      bytes_freed: Math.round(0.7 * TB),
      cumulative_freed_bytes: Math.round(i * 0.7 * TB),
      measurement_state: "complete"
    };
  }),
  versioning_findings: null,
  ...overrides
});
