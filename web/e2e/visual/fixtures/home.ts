import { VISUAL_NOW_ISO, VISUAL_RUN_ID } from "./constants";

/** Synthetic Home data for browser checks; never used by the application. */
export const homeResponses: Record<string, unknown> = {
  "/api/v1/dashboards/datalake": {
    totals: { total_tables: 1284, total_size_gb: 146432, small_files_count: 386400, needs_compaction_count: 76, reclaimable_bytes: 7832 * 1024 ** 3 },
    grade_distribution: [{ grade: "A", count: 738 }, { grade: "B", count: 294 }, { grade: "C", count: 146 }, { grade: "D", count: 52 }, { grade: "F", count: 24 }],
    freshness_as_of: VISUAL_NOW_ISO,
    freshness_distribution: [
      ["within_24h", "Updated in last 24 hours", 660], ["1_3d", "Not updated 1-3 days", 228],
      ["3_7d", "Not updated 3-7 days", 142], ["7_15d", "Not updated 7-15 days", 86],
      ["15_30d", "Not updated 15-30 days", 48], ["1_3m", "Not updated 1-3 months", 32],
      ["3_6m", "Not updated 3-6 months", 22], ["6_12m", "Not updated 6-12 months", 18],
      ["over_1y", "Not updated over 1 year", 8], ["unknown", "No snapshot timestamp", 30], ["failed", "Failed / corrupt tables", 10]
    ].map(([key, label, count]) => ({ key, label, count })), top_offenders: [], drift: []
  },
  "/api/v1/vitals": {
    generated_at: VISUAL_NOW_ISO, score: 86, status: "DEGRADED",
    deductions: [{ reason: "scanner: Scan coverage is behind schedule.", points: 10 }, { reason: "jobs: Recent maintenance failures need review.", points: 4 }],
    impact: { measurement_state: "partial", gb_freed_yesterday: 1843.2, actions_with_bytes_evidence: 38, actions_succeeded: 42 }
  },
  "/api/v1/runs": [{ id: VISUAL_RUN_ID, kind: "maintenance", status: "partial", created_at: "2026-09-28T02:30:00Z", finished_at: "2026-09-28T04:15:00Z", summary: { pipeline: "batch", selected: 500, succeeded: 462, partial: 8, failed: 6, skipped: 12, nothing_to_do: 10, planning_failed: 2 } }],
  "/api/v1/engine-load": { engines: [{ destination_id: "test-engine", name: "Demo Spark", engine: "spark_emr", slots_used: 4, slots_limit: 8, admission: "open", live_probe: "healthy" }] },
  "/api/v1/advanced/approvals/pending": [{ id: "approval-demo-1" }, { id: "approval-demo-2" }],
  "/api/v1/advanced/fast-path-findings": [
    { table_id: "table-demo-1", table_name: "analytics.customer_events", finding_type: "small_files", severity: "critical", reason: "A high number of small files needs review.", detected_at: VISUAL_NOW_ISO },
    { table_id: "table-demo-2", table_name: "warehouse.daily_order_items", finding_type: "missing_target_file_size", severity: "warning", reason: "No target file size has been configured.", detected_at: VISUAL_NOW_ISO }
  ]
};
