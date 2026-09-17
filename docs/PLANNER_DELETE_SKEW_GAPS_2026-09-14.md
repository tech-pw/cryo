# Two confirmed planner gaps in delete-skew detection (2026-09-14)

Found live while running maintenance across the top-25 cost/checklist tables tonight, after
deploying the `_has_pathological_delete_skew`/`_has_unreachable_delete_skew` fix (commits
`1b40163e`, `ac606872`) that stopped deferring to `convert_deletes` just because a partition's
data files were also individually small. That fix is correct and already proved itself on
`gold_batches_pw` (692→0 delete files) and `gold_dbt_video_data_temp` (31,510→267 delete files).

But two more tables hit real, distinct gaps in the same skew-detection logic tonight. Neither is
fixed yet -- documenting both here as a paired follow-up rather than a third round of live
threshold tuning.

## Gap 1: uniform per-partition deletes never trip skew detection

**Table:** `mview.gold_dbt_orders_base_fact`

Both skew functions loop partitions and check `deletes >= min_deletes` (a per-partition floor)
before ever computing a ratio. This table has 33 partitions, each carrying **exactly 1** delete
file against 1 data file (confirmed via `_latest_partitions`) -- deletes are real (33 total,
100% delete_file_pct) but spread perfectly evenly, never concentrated in any single partition.

No per-partition delete count of 1 will ever clear any realistic `min_deletes` floor, no matter
how low it's set (tried `min_deletes=20`, still `False`). This isn't a mistuned constant -- it's
a shape neither skew function was designed to detect: **uniformly-distributed-but-nonzero**
deletes, as opposed to **concentrated** skew.

Confirmed impact: two `convert_deletes` runs (07:31 and 15:09) plus a forced-preset attempt with
a lowered `delete_unreachable_min_files=20` all correctly built a `convert_deletes` plan (per the
existing logic) and correctly made no progress -- data files are already well-sized (74-93MB
each), so convert_deletes has zero size-driven rewrite candidates. Row count stayed exactly
correct (35,940,893) throughout; this is a stable, low-severity residual (33 files), not a
runaway growth pattern like the other tables tonight.

**Possible fix directions** (not attempted, needs design):
- A third detection mode alongside pathological/unreachable: "every partition has >=1 delete
  file and 100% delete_file_pct table-wide" -- doesn't need a per-partition floor at all, since
  the signal is table-wide uniformity, not any single partition's concentration.
- Or: a per-table (not per-partition) delete_file_pct + partition_count heuristic that flags
  "average delete-per-partition is nonzero across every partition" as its own case.

## Gap 2: high ratio but low absolute count also never trips skew detection

**Table:** `cdp_revenue.gold_batch_subject_schedules_pw`

Opposite shape from Gap 1: deletes ARE concentrated here (up to 31 delete files against a single
1-file partition, ratio ~28-31 -- well past the 7.0 "unreachable" threshold and close to the 20.0
"pathological" one). But `_has_unreachable_delete_skew`'s `min_deletes=100` per-partition floor
gates the ratio check entirely: `28 < 100`, `31 < 100`, so the function returns `False` for
every partition despite ratios that would clearly qualify if the floor were lower.

The `min_deletes=100`/`min_ratio=7.0` (unreachable) and `min_deletes=200`/`min_ratio=20.0`
(pathological) defaults were calibrated against `mview.gold_dbt_test_results`'s real incident
(150-315 deletes per partition, ratio ~2.6-8.0) -- a table with a *high absolute count and a
moderate ratio*. `gold_batch_subject_schedules_pw` is the inverse: *low absolute count, high
ratio*. The two thresholds were tuned as a pair for one shape and don't generalize to the other.

Confirmed impact: a `deep`-mode run (15:44-15:50Z) built and ran `convert_deletes` +
`expire_snapshots` + `remove_orphan_files`; delete files went from 316 (stale scan) to a live
357 (worse) -- consistent with convert_deletes having no size-driven candidates here either
(`files_per_partition: 1` on every affected partition, already "compacted" by file count).

**Possible fix directions** (not attempted, needs design):
- Lower `min_deletes` for the unreachable case specifically (e.g. to something like 20-30) --
  but per the code's own comment, 7.0/100 were chosen with real margin against two existing
  tests (`test_planner_routes_sub_pathological_but_unreachable_delete_skew_to_minor_rewrite`,
  ratio ~7.9; `test_convert_deletes_scopes_to_delete_skew_when_data_files_already_compacted`,
  ratio 20.0) -- changing it needs to re-verify those margins hold, not just patch the number.
- Or: make the ratio the primary signal and the absolute count secondary/advisory, rather than
  a hard gate -- e.g. `deletes >= min_deletes OR (deletes >= some much lower floor AND ratio is
  extreme)`.

## Gap 3 (confirmed 2026-09-15, top-200 batch): optimize can make deletes worse when it merges small files

**Table:** `lsq_pw.gold_dbt_lsq_pw_prospect_activity_phone_lookup`

A third, related failure mode surfaced running the top-200-by-query-frequency batch: this table
had `small_files_count=2408` (genuinely fragmented, `optimize` correctly fires) and
`total_delete_files=4040` (100% delete_file_pct). After a `deep`-mode run (`optimize` +
`optimize_manifests` + `expire_snapshots` + `remove_orphan_files`, no `convert_deletes` or
`rewrite_position_deletes` -- same "Skipped convert_deletes: planned optimize already rewrites
data files" note as Gaps 1/2), delete files went from 4,040 to **6,422 (up 59%)**.

Mechanism (consistent with the Trino/Iceberg MOR facts established earlier tonight): when
`optimize` merges several small data files into one larger file, any position-delete file that
referenced *any* of the old files must now be rewritten to point at the new file -- but Iceberg
doesn't necessarily consolidate multiple old delete files into one during this process, so a
partition with N old small files and M delete files can end up with the same M delete files
(or more, if the rewrite splits references) now spread thinner. This is the same live incident
this whole planner-gap investigation started from tonight (`gold_dbt_orders_base_fact`'s Sep 12
cron run: 549->756 deletes after an `optimize`+full-lifecycle pass), now confirmed a third time
on a different table.

Same live per-partition shape as Gap 1: `_has_unreachable_delete_skew` returns `False` (`policy:
None`) even with per-partition delete counts of 116, because the ratio-to-data-files math doesn't
trip at this table's shape either -- consistent with Gap 1/2's finding that the two-threshold
design doesn't generalize past the one incident (`gold_dbt_test_results`) it was calibrated
against. Not re-dispatched with a forced preset tonight for the same reason as Gaps 1/2: needs
the calibration work, not another one-off workaround.

## Why not fixed live tonight

Both are real, narrow gaps in the skew-detection heuristics, not urgent regressions -- the
tables affected have small (33-357 file) delete counts, not the tens-of-thousands seen on
`gold_dbt_video_data_temp` before its fix. Given the two gaps pull the calibration in opposite
directions (Gap 1 wants "detect uniform low counts", Gap 2 wants "detect concentrated-but-low
counts"), a rushed threshold change risks re-breaking the existing tests' margins documented in
`_has_unreachable_delete_skew`'s own comments. Worth a dedicated pass with fresh test fixtures
for both shapes, not a same-night patch.
