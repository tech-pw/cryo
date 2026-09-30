# Table detail redesign: evidence, metrics and layout

**Date:** 30 September 2026. **Scope:** research supporting the table-detail redesign; this document does not claim that every proposed metric is implemented. Current source files were read directly after codebase-graph discovery.

The page should answer four questions in order: **What condition is this table in? What explains it? What should happen next? Did maintenance help?** A compact identity header and seven stable tabs preserve orientation. Overview should summarize the answer; it should not repeat every diagnostic from the other tabs.

## What competitors establish

| Product and primary evidence | Observed capability | Cryo design implication |
| --- | --- | --- |
| [Apache Amoro table details](https://amoro.apache.org/docs/latest/using-tables/) | Details include file count, average size and commit time; Files exposes partitions/files; Snapshots supports branch/tag filtering; Optimizing shows before/after file counts, average size and duration. | Keep structural diagnosis, snapshot history and maintenance outcomes easy to reach. A successful task needs an understandable result. The linked `latest` docs explicitly describe a development version. |
| [Amoro metrics](https://amoro.apache.org/docs/latest/metrics/) | Separates data, equality-delete and position-delete files and tracks optimization processes/resources. | Preserve distinct data/delete semantics; do not call every file “data” or every ratio “health.” |
| [LakeOps observability](https://lakeops.dev/solutions/observability) | Advertises table findings, file histograms, snapshot growth, active/stale/delete files and operation before/after views. | Pair every prominent signal with evidence and a next step. These are vendor-described capabilities, not independently verified performance results. |
| [OpenMetadata profiler](https://docs.open-metadata.org/v1.12.x/how-to-guides/data-quality-observability/profiler) and [quality interface](https://docs.open-metadata.org/v1.12.x/how-to-guides/data-quality-observability/quality/tab) | Separates table/column profiling and quality results; quality reports distinguish successful, aborted and failed tests. | Keep operational table health distinct from data correctness. Missing or aborted tests must not look like passes. |

The differentiator to pursue is **an explainable diagnosis connected to a safe action and its measured result**. Another score or an attractive chart alone is not evidence that Cryo is better.

## Layout and proportional emphasis

- **Header:** one title, catalog/schema context, owner, scan age and last commit. Keep location behind a compact expandable/copy affordance. Use a short metric strip for data size, data files, small-file candidates and snapshots. Keep one primary maintenance action and explain missing workflow inline.
- **Overview first row:** about 60% width for health, persisted score deductions and the most useful finding; about 40% for what happens next, including selection blockers or an unassigned workflow. A restrained 0–100 gauge reinforces the score without taking the whole card.
- **Second row:** a file-condition card beside a health trend. A small two-segment donut is useful for candidate versus other data files; a time-series chart is useful for change. Both need adjacent exact values. Avoid several unrelated pies competing for attention.
- **Third row:** maintenance outcome and ownership/compliance. Keep diagnostic settings in progressive disclosure. Put detailed cost, queries, schema and snapshot exploration in their tabs.
- **Usage:** show the observation window once, then observed query count, p95 latency and mean scanned bytes. Follow with access patterns and the slow-query list. Empty telemetry should occupy a compact informative state rather than a large blank chart.
- **Responsive sizing:** use content-driven card heights, 16–24 px gaps and `min-width: 0` on grid children. Collapse two columns when their labels/values no longer fit. Contain tab/table overflow within the component at 320 CSS px; do not create page-level horizontal scrolling.

These are design recommendations, not fixed pixel promises. Validate proportions using long table names, real missing data, browser zoom, desktop and mobile. Use the existing tokens, semantic status labels, reduced-motion handling and chart data alternatives required by `PRODUCT.md` and `DESIGN.md`.

## Metrics worth showing

All counts and ratios below refer to the same persisted scan unless an explicit observation window is shown. `null`, absent, non-finite and invalid negative values are **Not measured**, not zero. A known zero denominator means **No data files** or **Not applicable**, not a fabricated percentage.

| Metric / operator question | Definition and available evidence | Recommended presentation and limits |
| --- | --- | --- |
| **1. Health and its change** — why this grade? | `TableStatsRead.overall_score`, grade and persisted `raw` deductions; `/health-trends` supplies recorded scan scores. Change is latest minus earliest valid score in the displayed period, in **points**, with actual dates. | Gauge with grade and numeric score, ranked deductions and a step/line trend. Do not recompute the score, invent deductions or connect missing observations as measured values. A single point has no trend. |
| **2. File condition** — is fragmentation actionable? | `small_files_count / total_files × 100`, where `total_files` counts data files. `small_files_count` is the scanner's count in partitions flagged for compaction, using the scan's small-file threshold. `avg_file_size_mb` is data bytes / data-file count / 2²⁰. | Candidate/other-data-files donut with both counts; average size in **MiB** alongside it. The remainder is “Other data files,” not “Optimized.” This candidate measure differs from the health scorer's count of files below 8 MiB. Do not infer the effective engine writer target from the mean. |
| **3. Delete-file pressure** — are deletes accumulating? | `total_delete_files`, equality and position counts are available. Existing backend `delete_file_pct = min(100, delete_files / max(data_files, 1) × 100)`. It is capped pressure, not percentage of all files. | Exact count with subtype detail. If showing a ratio, label its denominator explicitly. Do not use the capped backend percentage as a composition pie or percentage of rows deleted. |
| **4. Partition imbalance** — is work concentrated? | `partition_skew_ratio = max(files per partition) / max(median(files per partition), 1)`, rounded to one decimal. `max_files_per_partition` and `total_partitions` are available. | “Largest partition has 12× the median file count,” plus the maximum count and a link to partitions. Hide the comparison for unpartitioned/one-partition tables; do not call it byte skew or max/mean. Retained partition rows can be capped, so derive neither fleet totals nor the global median from a truncated UI list. |
| **5. Snapshot and metadata pressure** — is history growing? | Snapshot count, manifest count and recorded snapshot-count history already exist. A snapshot count is not an expiration candidate count. | Counts and trend with a link to Snapshots. Retention eligibility must respect age, minimum history and protected references. Add eligible snapshots/bytes only from an evaluated retention preview, not `count - 1`. |
| **6. Observed query demand** — is the table being read, and how slowly? | `/query-performance` returns full matching-ingestion-window `total`, p50/p95/p99 and mean bytes, plus a separately limited list of the 100 slowest queries. | Count, p95 and mean scan bytes with a 7-day label. A zero count means “No reads observed,” not “Unused.” Coverage is not measured. `failed_count` currently counts only returned slowest rows: it cannot produce the whole-window failure rate. |
| **7. Maintenance outcome** — did the operation help? | Audit receipts expose operation/status/time, nullable input/output file counts, bytes freed, commit ID and `effect_status`. File reduction is `(input - output) / input × 100` only when both counts exist and input > 0. | Before → after, signed change, operation, duration and link to run. Preserve negative reductions when output grew. A successful process without measurements is “Outcome not recorded.” Do not treat file reduction as equivalent bytes saved or faster queries. |
| **8. Storage opportunity** — what might be reclaimed? | Table cost response provides precision, timestamp, estimated reclaimable bytes and projected monthly savings. These are projections with different scan precision levels. | Amount plus “Estimated,” precision and scan time; link to Cost & Savings. Never relabel projected savings as realized. Do not make a storage composition pie unless every slice is disjoint and complete for the same scan. |

### Best additions without a new API

1. **Small-file candidate share and average file size** in one file-condition card. It adds interpretability to numbers the header already has, without extra catalog or engine reads.
2. **Delete-file count and partition imbalance** as compact diagnostic rows when measured and relevant. Use these to direct users to Schema & files; do not create two more large cards.
3. **Maintenance before/after from an existing audit receipt**, where supplied. Missing measurements must remain visible. `/maintenance-status` currently returns an empty `last_actions` list and no `next_nightly_at`, so it cannot supply receipts or a scheduled ETA.

## Important evidence semantics

**Freshness has three meanings.** “Last scan” describes Cryo's observation; “Last commit” describes an Iceberg metadata change; business-data freshness requires a defined event/watermark column and SLA. Compaction can create a snapshot without making the business data newer. Show scan and commit times separately; do not add a “Data fresh” badge from a commit timestamp alone. Amoro's documented watermark is format-specific, so it does not establish equivalent support for ordinary Iceberg tables.

**Storage names need care.** `StorageBreakdown` has separate `dead_bytes` and `delete_bytes`. Dead historical data is not the same thing as a delete file. The frontend cost response does not expose every underlying bucket, and orphan coverage can be absent; a complete 100% pie is therefore not generally justified. [Iceberg maintenance](https://iceberg.apache.org/docs/1.6.1/maintenance/) and [expiration procedures](https://iceberg.apache.org/docs/latest/spark-procedures/) explain why historical files cannot be assumed immediately reclaimable: retained snapshots can still reference them.

**Usage is ingestion-dependent.** The inspected query endpoint reads `QueryLog`. A source using the `iceberg_table` collection mode can populate `table_query_activity` instead, so absent query-log rows do not establish absence of demand. Partition-access evidence may come from a different window/source; label that panel's scope independently.

## Follow-up work with real product value

| Priority | Addition | Engineering prerequisite / acceptance condition |
| --- | --- | --- |
| Next | **File-size distribution with target context** | The scanner already persists `raw.file_size_bins` (40 power-of-two bins, separate data/delete arrays and an unknown count). Normalize it into a documented response with completeness/version and effective target provenance. The Go compatibility adapter currently creates zero-filled arrays for missing legacy payloads; a nonempty table with zero bins is not a measured histogram. Publish category boundaries and reconcile totals before charting. |
| Next | **Retention pressure and safe cleanup preview** | Existing snapshot/retention facilities should be reused. Surface evaluated candidate count, protected count, eligible bytes and evaluation time together; distinguish estimated bytes from actually deleted bytes. Never infer eligibility only from snapshot age/count. |
| Next | **Coverage-aware usage and read pruning** | Unify supported query collectors and expose observation coverage. A pruning metric needs eligible/planned bytes and read bytes for matching queries/snapshots; do not divide mean read bytes by today's table size and call that exact pruning. Add a true whole-window failure aggregate. |
| Later | **Verified improvement and net maintenance value** | Join operation receipts with pre/post snapshots and comparable query cohorts; record maintenance compute cost. Separate file reduction, physical reclaimed bytes, latency change and net money. Workload changes and rate changes require explicit qualification. |
| Later | **Business freshness / quality coverage** | Define event time, lateness and SLA; publish measured column/test coverage. Metadata-derived bounds are useful evidence but do not prove row uniqueness, complete logical row counts after deletes, or that every quality rule passed. |

These additions should be scanner/background computations persisted for read-only UI queries. Do not introduce full-table scans, live manifests or engine SQL on the table-detail request path.

## Local source anchors reviewed

- `web/src/api/types.ts`: table stats, table cost and audit-facing types.
- `web/src/api/client.ts`: health trends and query-performance response contracts.
- `web/src/api/maintenanceStatus.ts` and `cryo/api/routes_table_maintenance.py`: selection eligibility and intentionally unavailable receipt/ETA fields.
- `cryo/api/dashboard_routes.py::get_query_performance`: aggregate versus slow-query subset semantics.
- `cryo/scanner/analyze.py`, `cryo/scanner/go_scan.py`, `cryo/scanner/health.py`: count denominators, size units, histogram compatibility and persisted health deductions.
- `cryo/api/schemas.py::AuditLogRead`: measured versus unverified operation effects.
- `cryo/domain/models.py::StorageBreakdown` and `cryo/cost/model.py`: distinct storage buckets and savings projections.

## Acceptance checks for the presentation

- An operator can identify the table's condition, strongest explanation and next action without opening diagnostics.
- Grade/score, header counts and charts agree with the same stored evidence; unavailable values remain unavailable.
- Empty, stale, partial, failed and true-zero responses each have meaningful states.
- Chart values are available in text; tooltips are optional, statuses never rely on color, and focus/tab navigation is visible.
- Long identifiers, 320 px width, dark mode, reduced motion and 200% zoom retain readable content and usable actions.
- No claimed maintenance result, competitor advantage or cost saving exceeds the available evidence.
