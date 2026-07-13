# Scanner data points (parity contract)

This document is the field-level contract for Cryo's Iceberg metadata scanner.
Any replacement engine (Go `scan-stats`, future streaming paths) must produce the
same values for the fields marked **parity-critical**. Types and examples come from
[`cryo/scanner/analyze.py`](../cryo/scanner/analyze.py), persistence into
[`cryo/db/models.py`](../cryo/db/models.py), and fixtures in
[`tests/test_scanner.py`](../tests/test_scanner.py).

## Scan flow (current-snapshot stats)

```text
catalog.load_table
  → current_snapshot.manifests(io)
    → for each manifest: stream entries (skip DELETED)
      → DataFile { size, records, content, format, partition, column stats }
  → aggregate partitions + table totals
  → health score + (optional) cost tier
  → persist table_stats / table_partitions / table_cost
```

Cost Tier-1 (historical path map) and orphan LIST are **separate** from current-snapshot
stats and are out of scope for the Go `scan-stats` v1 path.

## Small-file thresholds (two rules)

| Use | Threshold | Default | Counts toward |
|-----|-----------|---------|---------------|
| Health grade | `HEALTH_SMALL_FILE_BYTES` | **8 MiB** | `health_small_files` input to `compute_health_score` |
| Compaction / planner | `scan_small_file_mb` | **89.6** (70% of 128 MB) | per-partition `small`, table `small_files_count` |

Per partition:

- `compaction_status = "F"` iff `small > 1` **and** `files > 1`, else `"T"`.
- Table `small_files_count` = sum of `small_files_in_partition_count` **only** for status `"F"`.

## Partition value encoding (parity-critical)

Partition values stay in Iceberg **raw** form (not rendered dates):

| Transform | Example raw value | Example `partition_key` |
|-----------|-------------------|-------------------------|
| `day` on date | `20643` (epoch-day) or ISO date string from some writers | `event_date_day=20643` |
| `month` | epoch-month int | `event_date_month=678` |
| `year` | epoch-year int | `event_date_year=56` |
| `identity` | native column value | `tenant_id=acme` |
| unpartitioned | _(empty values)_ | `_unpartitioned` |

Keys are `|`-joined `field=value` pairs with field names sorted.
See `partition_key()` / `data_file_partition_values()` in `analyze.py` and
[`cryo/planner/partitions.py`](../cryo/planner/partitions.py).

---

## `TableAnalysis` (in-memory DTO)

Produced by `analyze_table` / `analyze_catalog_table`.

| Field | Type | How produced | Example |
|-------|------|--------------|---------|
| `schema_name` | `str` | catalog namespace | `"demo"` |
| `table_name` | `str` | table name | `"events"` |
| `scan_at` | `datetime` (UTC) | scan clock | `2026-06-30T00:00:00+00:00` |
| `needs_compaction` | `bool` | `small_files > 0.2 * total_files AND max_files_per_partition > 2` | `true` |
| `compaction_priority` | `str` | HIGH / MEDIUM / LOW (see below) | `"HIGH"` |
| `grade` | `str` | A–F from health score | `"B"` |
| `overall_score` | `int` | 0–100 after WARN−8 / CRIT−20 | `76` |
| `total_files` | `int` | count of non-DELETED entries | `4` |
| `small_files_count` | `int` | sum of small counts on status `"F"` partitions | `3` |
| `total_partitions` | `int` | distinct partition keys | `2` |
| `max_files_per_partition` | `int` | max files in any partition | `3` |
| `avg_files_per_partition` | `float` | rounded 1 decimal | `2.0` |
| `partition_skew_ratio` | `float` | `max / max(median, 1)` | `1.5` |
| `total_records` | `int` | sum `record_count` | `160` |
| `total_size_gb` | `float` | `total_size_bytes / GiB`, 6 decimals | `0.308594` |
| `avg_file_size_mb` | `float` | mean file size / MiB, 3 decimals | `79.0` |
| `total_snapshots` | `int` | `len(snapshots)` | `2` |
| `total_delete_files` | `int` | equality + position | `0` |
| `delete_file_pct` | `float` | `(deletes / max(files,1)) * 100`, 2 decimals | `0.0` |
| `manifest_count` | `int` | current snapshot manifests | `1` |
| `partition_col` | `str \| None` | primary field `base_column`, or `"_unpartitioned"` | `"event_date"` |
| `partition_col_type` | `str \| None` | source column type, or `""` | `"date"` |
| `write_distribution_mode` | `str \| None` | `write.distribution-mode` | `"hash"` |
| `has_sort_order` | `bool \| None` | truthy `write.sort-order` | `true` |
| `target_file_size_bytes` | `int \| None` | `write.target-file-size-bytes` | `268435456` |
| `estimated_monthly_s3_cost_usd` | `float` | Tier-0 heuristic | `2.78` |
| `cost` | `TableCostAnalysis \| None` | Tier-1/2 when enabled | see below |
| `partitions` | `list[PartitionAnalysis]` | one per key (may be huge before persist cap) | |
| `raw` | `dict` | JSON blob persisted on `table_stats.raw` | see below |

### Compaction priority

- **HIGH**: `small > 25% of files` OR (`avg_file_size_mb < 32` and `small > 1`)
- **MEDIUM**: `small > 10% of files`
- else **LOW**

---

## `PartitionAnalysis`

| Field | Type | Example |
|-------|------|---------|
| `partition_value` | `str` | first value or key: `"2026-06-29"` / `"20643"` |
| `partition_values` | `dict[str, Any]` | `{"event_date_day": 20643}` |
| `partition_fields` | `list[dict]` | see `partition_fields` below |
| `partition_spec_id` | `int \| None` | `0` |
| `partition_path` | `str \| None` | same as key historically |
| `partition_key` | `str` | `"event_date_day=20643"` |
| `base_column` | `str \| None` | `"event_date"` |
| `base_column_type` | `str \| None` | `"date"` |
| `transform` | `str \| None` | `"day"` (raw transform string) |
| `files_per_partition` | `int` | `3` |
| `small_files_in_partition_count` | `int` | `3` (compaction threshold) |
| `partition_size_gb` | `float` | `0.058594` |
| `compaction_status` | `str` | `"F"` or `"T"` |
| `raw` | `dict` | `{"partition_values": {...}}` |

### `partition_fields[]` element

```json
{
  "base_column": "event_date",
  "partition_field": "event_date_day",
  "transform": "day",
  "transform_param": null,
  "col_type": "date"
}
```

---

## DB: `cryo.table_stats`

Upserted 1:1 from `TableAnalysis` (+ detection tier / watermark at persist time).

| Column | Source | Example |
|--------|--------|---------|
| `scan_at` | analysis | ISO timestamptz |
| `needs_compaction` | analysis | `true` |
| `compaction_priority` | analysis | `"HIGH"` |
| `grade` / `overall_score` | health | `"B"` / `76` |
| `total_files` … `target_file_size_bytes` | analysis | (see TableAnalysis) |
| `equality_delete_files` / `position_delete_files` | `raw` | `0` / `1` |
| `detection_tier` | persist (`hot`/`warm`/`cold`/`dormant`) | `"warm"` |
| `snapshot_watermark` | `raw.current_snapshot_id` | `918273645` |
| `last_full_reconcile_at` | this `scan_at` | |
| `estimated_monthly_s3_cost_usd` | Tier-0 | `2.78` |
| `projected_monthly_s3_cost_usd` | cost analysis | `1.90` |
| `projected_savings_usd_month` | cost analysis | `0.88` |
| `reclaimable_bytes` | cost analysis | `123456789` |
| `query_count_30d` | **not** set by Iceberg walk (telemetry) | `0` |
| `raw` | analysis.raw (+ preserved smart_maintenance) | JSONB |

## DB: `cryo.table_partitions`

Latest scan only (prior rows deleted). Cap:
`scanner_max_partitions_per_table` (default **5000**), keeping highest
`small_files_in_partition_count`.

Benefit-score columns (`query_frequency`, `compaction_benefit_score`, …) are filled later,
not by analyze.

## DB: `cryo.table_stats_history`

Subset: `scan_at`, `small_files_count`, `total_files`, `total_size_gb`, `grade`,
`overall_score`, plus `raw` with snapshots/deletes/manifests/quality/streaming keys.

## DB: `cryo.table_cost` / `table_cost_history`

From `TableCostAnalysis` when cost tier runs:

| Field | Example |
|-------|---------|
| `precision` | `"metadata"` / `"heuristic"` / `"storage_truth"` |
| `live_bytes` / `dead_bytes` / `metadata_bytes` | ints |
| `orphan_bytes` / `listed_bytes` | Tier-2 or carry-forward |
| `reclaim_expire_bytes` / `reclaim_optimize_bytes` / … | ints |
| `current_monthly_cost_usd` / `projected_*` | floats |
| `query_request_savings_usd_month` | from query_count × Δfiles model |

S3 **request** rollups (`get_requests_30d`, …) come from
`s3_request_cost_ingest`, not the manifest walk.

---

## `table_stats.raw` keys

| Key | Type | Notes / example |
|-----|------|-----------------|
| `partition_fields` | `list` | see above |
| `partitions_to_rewrite` | `list` | PartitionAnalysis dicts; **memory hotspot** — capped for Go/Python streaming paths |
| `file_formats` | `list[str]` | `["PARQUET"]` |
| `snapshot_overhead_gb` | `float` | Tier-0 overhead |
| `health_deductions` | `list` | `[{"label":"…","deduction":8,"severity":"warning","reason":"…"}]` |
| `column_quality` | `list` | per-field null/value/bounds |
| `avg_null_rate` / `max_null_rate` / `worst_null_column` | quality summary | `0.1` / `0.1` / `"user_id"` |
| `first_snapshot_at` / `last_snapshot_committed_at` | ISO | |
| `current_snapshot_id` | `int` | watermark source |
| `equality_delete_files` / `position_delete_files` | `int` | |
| `location` | `str` | `s3://bucket/warehouse/db/table` |
| `manifest_read_errors` | `list[str]` | optional |
| `manifest_stats` | `dict` | `{manifest_count, avg_files_per_manifest}` |
| `puffin_blobs` | `list` | optional sketch locators |
| `cost` | `dict` | cost.raw when Tier-1 ran |
| `commit_event_history` | `list[str]` | preserved across scans |
| `smart_maintenance` | `dict` | incremental path; preserved |

### Intended signal keys (health / governor)

`enrich_stats_signals` computes for **health scoring**:

- `format_version`, `v3_upgrade_candidate`
- `metadata_to_data_ratio`
- `is_streaming`, `commits_per_day`

**Contract:** these should also land in `table_stats.raw` so history/API/governor see them.
If a path computes them only in the ephemeral health dict, treat that as a bug to fix.

---

## Delete file classification (parity-critical)

From `DataFile.content`:

| Content | Kind |
|---------|------|
| contains `equality` or content `2` | equality |
| contains `position` / ends with `deletes` / content `1` | position |

---

## Consumers

| Consumer | Fields used |
|----------|-------------|
| Planner | `small_files_count`, `total_size_gb`, `delete_file_pct`, partition col/fields/values, per-partition small/size, streaming signals |
| API / UI | grades, sizes, `raw` (deductions, quality), partitions |
| Reporting | grade distribution, needs_compaction, estimated / projected cost |
| Governor / ROI | `detection_tier`, streaming, savings |
| Incremental | watermark + `smart_maintenance` without full walk |

---

## Go `scan-stats` v1 scope

**Must match Python for:** file/partition aggregates, small-file counts (both thresholds),
delete kinds, partition keys/values/fields, snapshot timestamps/id, manifests count,
formats, table properties, location, column_quality (+ null-rate summary), and
full-map `total_partitions` / skew stats even when partition rows are capped.

**Left to Python after Go walk:** health score, Tier-0/1/2 cost, puffin sketches,
persistence, detection tier, benefit scores.

**Full-field parity suite:** `tests/scanner_parity/test_full_field_parity.py` runs
`analyze_table` vs `cryo-engine-go aggregate-fixture` + mapper with hard equality
across unpartitioned, day/month/year (ISO + epoch), identity (string/date/timestamptz),
multi-field, bucket, MOR deletes, mixed formats, column quality, empty tables, and
partition-cap truncation. No soft skips — every contract field must match.
