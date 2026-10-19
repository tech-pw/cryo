# Table detail insights (2026-10-18)

Nine additions to the table page, picked from the ranked research in
`tmp/reports/Table detail page feature ideas.md` (not committed). Each one answers a question an
operator used to need SQL or logs for. Every number on screen comes from something Cryo measured
or the engine reported; where nothing was recorded, the page says so rather than showing zero.

## What was added

| Feature | Where | Data source |
|---|---|---|
| Passed-over nights in the run grid | Maintenance → Runs grid | `GET /tables/{id}/skip-evaluations` (`skip_evaluations` rows) |
| What maintenance achieved | Overview → History & activity | `GET /tables/{id}/maintenance-impact?window_days=30` |
| Per-step effect in run detail | Maintenance → run detail steps | Audit receipts on the run's `job_id` |
| Full commit summary | Snapshots → table columns and Selected snapshot | Iceberg snapshot `summary` map |
| Delete-file debt chip and trend | Schema & files → Delete files | `TableStats` plus `GET /tables/{id}/history` |
| Manifests & metadata strip | Overview → Layout checks | `raw.manifest_stats`, `raw.iceberg_properties`, `raw.manifest_read_errors` |
| Suggested partition change | Schema & files → Partitions | `GET /advanced/partition-evolution/{id}` (was fetched, never shown) |
| More suggestions from the last scan | Config → Table configuration | `GET /tables/{id}/property-recommendations` (was orphaned) |
| Column filter/join heatmap | Schema & files → Columns, "Query use · 30 days" | `GET /tables/{id}/column-usage` (`query_patterns`) |
| Maintenance hold with reason and expiry | Table settings drawer | `GET/PUT/DELETE /tables/{id}/maintenance-hold` |

## Fix: metadata cost counted files in carried-over manifests as dead

`cryo/scanner/cost.py::account_metadata_cost` walked every retained snapshot's manifest list and
skipped a manifest path it had already seen. Iceberg carries unchanged manifests forward into
every later snapshot's manifest list, so a manifest written by an old snapshot is also part of the
current one. The old code credited its files only to the first snapshot that listed it. When that
snapshot was past retention, files that are still live looked expire-reclaimable.

Effect before the fix, on any table with history:

- `live_bytes` was too low and `dead_bytes` too high.
- `metadata_to_data_ratio` was inflated, which fired the "Metadata overhead" deduction on tables
  with normal metadata. This was the spurious deduction.
- `reclaim_expire_bytes` and the "expire would free" estimate were too high.

Now each manifest is recorded once, with the set of snapshots that list it, and its files
belong to all of them. The manifest bound (`cost_scan_max_manifests`) applies to unique manifests.
Regression test: `tests/test_scanner_cost.py::test_metadata_accounting_counts_files_in_manifests_carried_into_current_snapshot`.

**Expect this after deploy:** the next scan of each table can lower its reclaim estimate, lower
its metadata ratio, and remove a −8 metadata deduction, which raises some grades. That is the
correction, not a regression. Fleet-level "reclaimable" totals on Cost will drop.

## Maintenance hold

A hold is a `maintenance_exclusions` row with the new nullable `expires_at` (migration
`20261018_1300`). `NULL` keeps the old open-ended exclusion behaviour, and a set value makes it time-boxed.
Enforcement stays in one place: `table_is_excluded(now)` and `excluded_table_ids` both filter on
`expires_at IS NULL OR expires_at > now`, and scheduled selection and manual runs both use them.

- `PUT /tables/{id}/maintenance-hold` requires the operator role. The reason must be 3–500
  characters. The expiry must be in the future and at most 90 days out (`MAX_HOLD_DAYS`); otherwise
  the API returns 422. It replaces the table's own hold, and writes a `maintenance_exclude` audit row
  with the reason and end time.
- `DELETE` releases only the table's own hold, and is audited. A schema-wide rule covering the
  table is shown in the drawer, but can't be released from one table (404).
- An expired hold no longer blocks creating a new exclusion: `create_exclusion` replaces it instead
  of returning 409.
- Selection records a held table as skip reason `held` (`REASON_HELD`). It's checked right after
  the inactive check, so the run grid says "On hold" and doesn't fall back to a generic "Excluded".
- Scans keep running while a table is held, so the grade shows what the hold costs.

Frontend permission: `holdMaintenance` (operator). It isn't in `MAINTENANCE_WRITES`, because
pausing maintenance must still work when maintenance writes are switched off.

## Maintenance impact

`cryo/maintenance/impact.py::table_maintenance_impact` sums successful audit rows (`OK`,
`SUCCESS`, `SUCCEEDED`) per operation, for the window and lifetime, in `CORRECT_ORDER`.

- Bytes count only where `actual_bytes_freed` is set.
- Files count only where both `input_files_count` and `output_files_count` are set.
- `ops_with_bytes` / `ops_with_files` say how many operations reported anything. The card prints
  "N of M reported their effect", so an engine that doesn't report (for example Trino expiry) shows
  as unmeasured, not as zero.

## Snapshot summary

`SnapshotSummaryRead` now carries the following, all read from the Iceberg snapshot summary map
and left `null` when the writer didn't record them:

- added and deleted records;
- bytes added and removed;
- delete files added, split into position, equality and deletion vectors;
- total delete files, total position deletes and total equality deletes;
- `changed-partition-count`.

`writer` is inferred from engine-specific keys: `engine-name` / `engine-version` when present,
otherwise Trino (`trino_query_id`, `trino_user`), Spark (`spark.app.id`), Flink (`flink.job-id`) or
Kafka Connect (`kafka.connect.commit-id`). Absent keys show "Not recorded by this writer".

## Delete-file debt

The chip uses the health check's own cut-offs (`HEALTH_THRESHOLDS`: warn at 5% or 100 delete files,
critical at 20% or 1,000), so it can't disagree with the grade.

- **Minor** points to rewrite position deletes. That's Spark only, because Trino has no
  `rewrite_position_delete_files`.
- **Major** points to convert deletes, which works on both engines.

History rows now expose `total_delete_files`, `delete_file_pct`, `manifest_count` and
`total_snapshots`, lifted from `table_stats_history.raw` by `TableStatsHistoryRead._lift_raw`.

## Manifests & metadata strip

The scanner now writes `raw.manifest_stats` from `cryo/scanner/signals.py::manifest_stats`, so the
Python and Go scan paths produce the same shape. Its fields are `manifest_count`,
`avg_files_per_manifest`, `manifest_total_bytes` and `avg_manifest_bytes`. The strip compares
average manifest size with Iceberg's 8 MiB `commit.manifest.target-size-bytes` default, and warns
at the health check's 100-manifest threshold. It also reads
`write.metadata.delete-after-commit.enabled` and `write.metadata.previous-versions-max`. With
delete-after-commit off (Iceberg's default), every commit leaves a `metadata.json` behind until
orphan cleanup. The scan doesn't count those files, so the strip shows the setting, not a count.

## Recommendations revived

- **Partition evolution:** shows the current spec, the suggested spec, the trigger, confidence,
  queries analysed and warnings. The pruning gain is labelled an estimate, because the analyzer
  derives it from a heuristic. Cryo never runs partition evolution; the card says to apply it with
  the engine's `ALTER TABLE`.
- **Property suggestions:** `config/ScanSuggestions.tsx` shows only the suggestions that no Config
  inventory row already answers. Where a row has its own `recommended_value`, that row wins, so a
  property never gets two answers. "Stage" adds the change to the same preview, review and audited
  apply as a row edit, and bloom-column suggestions stage onto the bloom-columns setting. The old
  `PropertyRecommendations.tsx` (direct apply, no review) is still unreferenced by any page; its
  only importer is `writeGates.test.tsx`.

## Column heatmap

`GET /tables/{id}/column-usage` returns the latest `query_patterns` aggregation: rows within 6
hours of the newest `last_seen_at`, because a column not seen in a tick keeps its old row. They're
sorted by filters plus joins. The Columns table gains a "Query use · 30 days" column only when
there's data.

Column use comes only from `query_logs`. A source with `query_log.mode == "iceberg_table"` writes
table-level counts and no per-query rows, so the page explains the empty state rather than
drawing an empty heatmap.

## Verification

- Backend:
  - `tests/test_scanner_cost.py`
  - `tests/test_maintenance_impact.py`
  - `tests/test_table_snapshots_api.py`
  - `tests/test_maintenance_exclusions.py` (expiry, the `held` reason, the API's role and bounds checks)
  - `tests/test_skip_history.py`
  - `tests/test_column_usage_api.py` (latest window, empty state, history lift)
- Frontend:
  - `runModel.test.ts`
  - `OverviewPanel.test.tsx` (impact)
  - `ManifestHealth.test.ts`
  - `deleteDebt.test.ts`
  - `FilesTabPanel.test.tsx` (heatmap, empty state, debt chip, partition evolution)
  - `SnapshotsTab.test.tsx` (writer, deletes, partitions)
  - `ScanSuggestions.test.ts`

## After deploy

1. Run `alembic upgrade head` (`20261018_1300`) before rolling the API, worker and scheduler.
2. After the first full scan, compare Cost "reclaimable" before and after. A drop is the
   cost-accounting fix.
3. Put one table on hold for a day. Confirm the next scheduled run records it as `held`, and that
   it's picked up again after expiry.
