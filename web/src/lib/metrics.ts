export const METRIC_PROVENANCE = ["measured", "modeled", "estimated-pricing", "partial"] as const;

export type MetricProvenance = (typeof METRIC_PROVENANCE)[number];

export const PROVENANCE_LABEL: Record<MetricProvenance, string> = {
  measured: "Measured",
  modeled: "Modelled",
  "estimated-pricing": "Estimated pricing",
  partial: "Partial"
};

export const provenanceLabel = (provenance: MetricProvenance): string => PROVENANCE_LABEL[provenance];

export const METRIC_IDS = [
  "tracked_tables",
  "scanned_tables",
  "graded_tables",
  "tables_grade_d_or_f",
  "needs_compaction",
  "storage_gb",
  "modeled_s3_storage_cost",
  "catalogs_modeled_s3_storage_cost",
  "catalogs_tables_with_stats",
  "modeled_savings",
  "measured_reclaim_gb",
  "awaiting_person",
  "failed_runs_24h",
  "running_runs"
] as const;

export type MetricId = (typeof METRIC_IDS)[number];

export interface MetricDefinition {
  id: MetricId;
  label: string;
  definition: string;
  unit: string;
  denominator: string;
  provenance: MetricProvenance;
  source: string;
}

const METRIC_ENTRIES = {
  tracked_tables: {
    label: "Tracked tables",
    definition:
      "Every table row Cryo stores, including inactive and never-scanned tables; a missing scan is not zero tables.",
    unit: "tables",
    denominator: "all Table rows",
    provenance: "measured",
    source: "GET /api/v1/dashboards/datalake totals.total_tables"
  },
  scanned_tables: {
    label: "Scanned tables",
    definition:
      "Active tables with a TableStats row from a scan; not the catalogs tables-with-stats total, which counts tables with stats on enabled sources and does not require is_active.",
    unit: "tables",
    denominator: "active tables",
    provenance: "measured",
    source: "GET /api/v1/scanner/status tables_with_stats"
  },
  graded_tables: {
    label: "Graded tables",
    definition:
      "Active tables whose TableStats.grade is A, B, C, D, or F; a null or unknown grade is not graded and is not zero.",
    unit: "tables",
    denominator: "tracked tables",
    provenance: "measured",
    source: "GET /api/v1/dashboards/datalake grade_distribution (sum of counts where grade is A, B, C, D, or F)"
  },
  tables_grade_d_or_f: {
    label: "Tables at grade D/F",
    definition: "Active tables graded D or F; ungraded and unknown tables are excluded, not counted as zero.",
    unit: "tables",
    denominator: "graded tables (A–F)",
    provenance: "measured",
    source: "GET /api/v1/dashboards/datalake grade_distribution (grades D and F)"
  },
  needs_compaction: {
    label: "Needs compaction",
    definition:
      "Active tables with TableStats.needs_compaction true; not a small-file threshold, and not the catalogs sum of that flag over enabled sources.",
    unit: "tables",
    denominator: "active tables",
    provenance: "measured",
    source: "GET /api/v1/dashboards/datalake totals.needs_compaction_count"
  },
  storage_gb: {
    label: "Storage",
    definition:
      "Sum of recorded TableStats.total_size_gb for active tables; a table with no recorded size is excluded and is not 0 GB.",
    unit: "GB",
    denominator: "active tables with a recorded total_size_gb",
    provenance: "measured",
    source: "GET /api/v1/dashboards/datalake totals.total_size_gb"
  },
  modeled_s3_storage_cost: {
    label: "Estimated storage cost",
    definition:
      "Estimated storage cost, not a bill: over active tables, TableCost.current_monthly_cost_usd when a cost row exists, otherwise TableStats.estimated_monthly_s3_cost_usd; a missing estimate is not $0. Not the catalogs fleet cost.",
    unit: "USD/month",
    denominator: "active tables with a cost row or a scanner estimate",
    provenance: "modeled",
    source: "GET /api/v1/dashboards/datalake totals.estimated_monthly_s3_cost_usd"
  },
  catalogs_tables_with_stats: {
    label: "Tables with stats",
    definition:
      "Tables with a stats row on an enabled catalog. Not every tracked table, and not the scanner's active-table scanned count.",
    unit: "tables",
    denominator: "tables with stats on enabled sources",
    provenance: "measured",
    source: "GET /api/v1/multi-catalog total_tables"
  },
  catalogs_modeled_s3_storage_cost: {
    label: "Catalogs estimated storage cost",
    definition:
      "Estimated storage cost, not a bill: sum of TableStats.estimated_monthly_s3_cost_usd for tables with stats on enabled sources; a missing estimate is not $0. Not modeled_s3_storage_cost, which prefers TableCost over active tables.",
    unit: "USD/month",
    denominator: "tables with stats on enabled sources",
    provenance: "modeled",
    source: "GET /api/v1/multi-catalog total_cost_usd"
  },
  modeled_savings: {
    label: "Modelled savings",
    definition:
      "Estimated storage-cost reduction if recommended maintenance ran, not actual savings and not measured reclaim; over active tables, TableCost.projected_savings_usd_month when a cost row exists, otherwise TableStats.projected_savings_usd_month; a missing projection is not $0.",
    unit: "USD/month",
    denominator: "active tables with a projected savings figure",
    provenance: "modeled",
    source: "GET /api/v1/dashboards/datalake totals.projected_savings_usd_month"
  },
  measured_reclaim_gb: {
    label: "Measured reclaim",
    definition:
      "Bytes maintenance actually freed, in GB; not bytes rewritten, snapshot size, or reclaimable_bytes. Unmeasured is not 0 GB; a partial day is a subtotal of actions with byte evidence among actions succeeded, not a precise total.",
    unit: "GB",
    denominator: "succeeded actions with byte evidence, in the stated window",
    provenance: "measured",
    source:
      "GET /api/v1/vitals impact.gb_freed_yesterday (GB, with impact.measurement_state); a window uses GET /api/v1/impact/history rows.bytes_freed (bytes, convert to GB) and rows.measurement_state"
  },
  awaiting_person: {
    label: "Awaiting a person",
    definition:
      "Pending approval requests that have not expired and are waiting on a person; failed runs, failed scans, and SLA breaches are not included.",
    unit: "requests",
    denominator: "unexpired pending approval requests",
    provenance: "measured",
    source: "GET /api/v1/advanced/approvals/pending (count of the list)"
  },
  failed_runs_24h: {
    label: "Failed runs (24 h)",
    definition:
      "Maintenance runs with status failed whose created_at is within the last 24 hours; waiting for engine capacity is not a failure, and a count from one full GET /api/v1/runs page (limit, max 500) is partial — show 500+, not a precise total.",
    unit: "runs",
    denominator: "runs created in the last 24 hours",
    provenance: "partial",
    source: "GET /api/v1/runs status and created_at"
  },
  running_runs: {
    label: "Running runs",
    definition:
      "Maintenance runs whose status is running; queued, admitted, and waiting for engine capacity are not running, and a count from one full GET /api/v1/runs page is partial.",
    unit: "runs",
    denominator: "runs with status running",
    provenance: "partial",
    source: "GET /api/v1/runs status"
  }
} as const satisfies Record<MetricId, Omit<MetricDefinition, "id">>;

export const METRICS: readonly MetricDefinition[] = METRIC_IDS.map((id) => ({
  id,
  ...METRIC_ENTRIES[id]
}));

const BY_ID = new Map(METRICS.map((entry) => [entry.id, entry]));

export const metric = (id: MetricId): MetricDefinition => {
  const found = BY_ID.get(id);
  if (!found) {
    throw new Error(`Unknown metric: ${id}`);
  }
  return found;
};

export const metricLabel = (id: MetricId): string => metric(id).label;

export const metricTooltip = (id: MetricId): string => {
  const entry = metric(id);
  return `${entry.definition} Unit: ${entry.unit}. Provenance: ${provenanceLabel(entry.provenance)}. Denominator: ${entry.denominator}.`;
};
