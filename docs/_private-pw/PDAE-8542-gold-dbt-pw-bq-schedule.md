# PDAE-8542 — `unified_bq.gold_dbt_pw_bq` schedule + failure notes

**Date:** 2026-08-18  
**Cluster:** `data-platform-cluster` / namespace `cryo`  
**Status:** Live cron applied. 7-day health trend not claimed in this session.

## Live stats (scan `2026-08-18T00:11:29Z`)

| Field | Value |
| --- | --- |
| Table id | `1ae85c5b-94f9-4091-8a8f-417fa8bbd02f` |
| Grade / score | **F** / 16 |
| Files | **189,844** (58,393 small) |
| Size | **24,549 GB** |
| Snapshots | **382** (critical vs 200) |
| Partitions | 63,694 |
| `stats.raw.commits_per_day` | **0.0** (`is_streaming` false) |
| Location | `s3a://pw-data-cdp-prod-gold-layer/gold/unified_bq/gold_dbt_pw_bq` |

`commits_per_day` is 0 because `detect_streaming_from_history` only uses the last two `table_stats_history` rows and zeros the signal when a maintenance job sat between them. History API rows also omit `total_snapshots`, so the stored rate is not usable for cadence.

**Derived write velocity (this session):** `raw.first_snapshot_at` = 2026-08-14T05:19Z (lineage after expire), `last_snapshot_committed_at` = 2026-08-17T08:09Z, **382 snapshots in ~3.1 days ≈ 123 commits/day**. Planner `_expire_params` treats `commits_per_day >= 100` as **1-day retention**. File count from history: 184,963 (Aug 13) → 189,844 (Aug 18) ≈ **+980 files/day** after the Aug 13 drop.

Grade F deductions: excessive files (−20), snapshot backlog (−20), partition skew 79.5× (−20), small-file % (−8), partition count (−8), property gaps (−8).

## Cron chosen and applied

Previously: preset `claude-spark-run-unified-bq-gold-dbt-pw-bq` (`4e8c66d2-2067-403a-b2fd-014bb0312e9f`) was **enabled** with **`schedule_cron: null`** and **no rules**. The table was **pinned to `spark-large`** (`c3e8d7d4-5342-4d51-b1a9-61332aaa5dca`, cron `45 20 * * *`, cooldown 48h, deadline 240 min). A cron on the dedicated preset would not have selected this table while that pin stood.

**Applied live (admin API, 2026-08-18):**

- `PATCH /api/v1/presets/4e8c66d2-2067-403a-b2fd-014bb0312e9f` → **`schedule_cron`: `0 16 * * *`** (16:00 UTC = **21:30 IST**, once daily). Sized to planner 1-day expire cadence at ≥100 commits/day. Daily rather than twice-daily so a DEEP optimize (Jul 31 run was ~62h) is not re-queued every 12h; per-table lock would serialize anyway. Preset cooldown remains 0; no `deadline_minutes` (unlike `spark-large`’s 240 min cap, which cannot finish a real rewrite of this table).
- `POST /api/v1/assignments` — pin this table to that preset (`affected: 1`).
- `POST /api/v1/presets/{id}/rules` — predicate `schema_name=unified_bq` AND `table_name=gold_dbt_pw_bq` (rule `9c11954a-023f-4854-b177-f9daec4fa270`). Simulate: **matched_count 1**.

Next expected fire: **16:00 UTC** on the next calendar day the scheduler evaluates this cron as due.

## Failure-rate findings (`GET /api/v1/tables/{id}/runs`)

**15 jobs on record: 10 failed / 5 succeeded = 67% fail** (ticket’s ~50% was the right order of magnitude; this is the full table history, not a 7-day window).

| When | Trigger | Outcome | Stored `error` |
| --- | --- | --- | --- |
| 2026-08-17, 08-15 | `cron` (`spark-large`) | succeeded ~244s, 2 steps | — expire + orphan only |
| 2026-08-14 | `cron` | failed; expire ok, **orphan failed** | Spark `RejectedExecutionException` / terminated executor pool; **PDAE-8346** `[root cause]` + head/tail truncation (`16,684 chars total`) |
| 2026-08-13 | `cron` | failed on expire | same class; `[root cause]` present |
| 2026-08-11 18:17 | manual reclaim | expire ok, orphan OOM | short code `spark_executor_oom_could_not_allocate_bytestobytesmap_during_remove_orphan_files` |
| 2026-08-11 18:04 | manual | expire fail | `Could not allocate memory to grow BytesToBytesMap` + truncation |
| 2026-08-11 10:39 | mandate | **succeeded ~6.8h**, 3 steps | — |
| 2026-08-10 | retries | failed, 0 steps | `native_job_failed` (17 chars; no stack) |
| 2026-08-07 / 06 | api | failed, 0 steps | `native_job_missing_before_result_collection`; `native_job_failed: exec ... argument list too long` |
| 2026-07-31 | dedicated spark runs | 1 success ~62h (32 steps); 2 fails | Spark-on-K8s; one optimize-stage fail. Older rows are head-truncated INFO dumps (pre–root-cause prepend). |

**PDAE-8346:** `truncate_error_message` + `deepest_caused_by` (incl. Py4J / qualified `Exception:` headers) **is already in `cryo/engines/base.py`**. Aug 13–14 Spark failures store `[root cause]` plus `...[truncated, N chars total, tail below]...`. Do not change truncation. Short native codes (`native_job_failed`) are a different path (no JVM dump to truncate).

Dominant failure modes for this table: **Spark executor death during expire/orphan** (RejectedExecution / BytesToBytesMap OOM) and **native dispatch** (`native_job_failed`, argv-too-long). Cron succeed rows under `spark-large` did **not** run optimize (2 steps, ~4 min) — 48h cooldown + 240 min deadline cannot rewrite 24TB.

## How to verify a 7-day trend (not done here)

One session cannot substitute for a week of post-cron scans. After several daily fires:

1. Port-forward `kubectl port-forward -n cryo svc/cryo-api 18000:8000`, admin token `POST /api/v1/auth/token`.
2. `GET /api/v1/tables/1ae85c5b-94f9-4091-8a8f-417fa8bbd02f` — grade, `total_files`, `total_snapshots`, `scan_at`.
3. `GET /api/v1/tables/{id}/history?limit=14` — plot `total_files` / `grade` / `total_size_gb` by `scan_at` for the last 7 days (history has no snapshots column; use current stats + job before/after on runs).
4. `GET /api/v1/tables/{id}/runs?limit=50` — filter `started_at` to the last 7 days; fail rate = failed / n. Confirm `trigger=cron` and that steps include `optimize` when the dedicated preset runs (not only expire+orphan).
5. Do not infer a trend from a single scan or from the pre-change `spark-large` cron rows.

Success looks like: files and snapshots **not rebounding** toward 200k+ / 2000+ between daily expires, and at least one completed **optimize** step in the week without a 50%+ cron fail rate.
