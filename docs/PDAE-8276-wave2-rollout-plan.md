# PDAE-8276 — Wave 2 plan (actively-queried tables)

**Status:** Planning only. **Do not create a Wave 2 preset or trigger maintenance** until PDAE-8274 (Wave 1) is understood: 2026-08-18 snapshot was 11,707 tables touched, ~32% succeeded, ~73% of current failures are undiagnosed `native_job_failed`.

Ticket target size (~1,983) is a **30-day query-activity snapshot from filing**. Re-count with the query below before any canary; `query_count_30d` moves.

## Go / no-go (must all be true)

1. Wave 1 failure rate is classified (retryable vs systematic), not a wall of `native_job_failed`.
2. No Wave 2 preset exists yet (confirmed 2026-08-17: 20 live presets, none target this cohort). Keep it that way until go.
3. Governor fallback-starve fix (PDAE-8352) is **deployed** (`f967caa` / pin `f922d0c`) so a large native cohort cannot starve Trino/Spark queue admission during Wave 2.
4. Explicit human go-ahead. This plan is not a go-ahead.

## Cohort selection (Cryo Postgres)

Actively queried = latest `cryo.table_stats.query_count_30d > 0`. Wave 1 was the complement (`<= 0`). Apply the same ephemeral skip as `is_ephemeral_maintenance_table` (PDAE-8347) so scratch tables never enter a live-traffic wave.

**Count + canary ranking** (lowest query volume first):

```sql
-- Wave 2 candidate set. Re-run before each batch; do not freeze a 1,983 list.
SELECT
  t.id,
  t.schema_name,
  t.table_name,
  s.query_count_30d,
  s.grade,
  s.total_size_gb,
  s.total_files,
  s.total_snapshots,
  s.needs_compaction,
  t.last_maintained_at
FROM cryo.tables t
JOIN cryo.table_stats s ON s.table_id = t.id
WHERE t.is_active
  AND COALESCE(s.query_count_30d, 0) > 0
  AND lower(t.schema_name) NOT IN ('temp', 'tmp', 'scratch')
  AND lower(t.table_name) NOT LIKE '%__dbt_tmp%'
  AND lower(t.table_name) NOT LIKE 'temp\_%' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE 'tmp\_%' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE 'scratch\_%' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE '%\_temp' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE '%\_tmp' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE '%\_scratch' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE '%\_test' ESCAPE '\'
  AND lower(t.table_name) NOT LIKE '%\_dtmp' ESCAPE '\'
  -- testpress is a real product schema (PDAE-8347 carve-out); keep it.
ORDER BY s.query_count_30d ASC, s.total_size_gb ASC;
```

**Buckets (execute later, never all at once):**

| Batch | Filter on the result set | Size cap | Why |
| --- | --- | --- | --- |
| 0 canary | `query_count_30d` between 1 and ~p10 of the cohort | **10–20 tables** | Lowest blast radius; proves job terminal + no Trino regression |
| 1 | remaining `query_count_30d` < 50 | ≤ 100 / night | Still "warm" not dashboard-hot |
| 2 | 50–500 | ≤ 50 / night | Consumer-visible |
| 3 | > 500 | **named tables only**, one at a time | Includes `gold_dbt_pw_bq` (already on dedicated preset PDAE-8542 — **exclude if pin is still that preset**) |

Exclude tables already pinned to a dedicated hot preset (`claude-spark-run-unified-bq-gold-dbt-pw-bq`, etc.) unless the operator intends a second engine.

API equivalent when exec is available: filter tables with `query_count_30d>0` (same field the dark-table path already uses). Prefer SQL above so Wave 1's zero-query preset cannot be copied by accident.

## Off-peak scheduling

Heavy modes (`deep` / optimize) are gated by destination `offpeak_start` / `offpeak_end` **IST hours** (`cryo.governor.admission.is_offpeak`). Equal start/end **disables** the gate.

**Plan for Wave 2 destinations (do not PATCH live now):**

- Set a real window, e.g. **22:00–06:00 IST** (hour 22 → 6), overlapping typical CDP quiet time.
- Preset `schedule_cron` in UTC that only *enqueues* inside that window (example: `0 18 * * *` = 18:00 UTC = 23:30 IST).
- Mode: start canary as **standard**, not deep. Deep only after canary jobs succeed and Grafana Trino latency on those table names is flat.
- Cooldown ≥ 48h on the Wave 2 preset so a failed rewrite cannot immediately re-queue a hot table.
- Deadline: **do not** copy `spark-large`'s 240 min cap onto 10TB+ tables (PDAE-8542 lesson).

PDAE-8352: fallback `optimize_manifests` must not block the regular queue; that is already in the deployed governor. Still keep Wave 2 **batch size ≤ destination `slot_cap`** so one night cannot recreate a 49-dispatch wave.

## Batched rollout (execute later)

1. Dry-run: `POST /api/v1/presets/{id}/simulate` with the Wave 2 rule (`query_count_30d > 0` plus ephemeral exclusions). Confirm overlap with Wave 1 preset is **zero**.
2. Create preset **disabled**. Pin **only** the 10–20 canary table ids. Enable cron.
3. Next off-peak: confirm each canary job reaches a **terminal** state (success or classified failure). Abort Wave 2 if unclassified `native_job_failed` reappears.
4. Watch Trino: p95 / error rate for those `schema.table` names for 24h (Grafana + query-log, PDAE-8277 path).
5. Expand one bucket per night. Never big-bang ~2k tables.

## Monitoring checklist

- Job: `GET /api/v1/tables/{id}/runs` — terminal, step list includes expire/orphan/optimize as intended.
- Fleet: Governor "Wave complete" still appearing every tick (PDAE-8352 regression).
- Query: no latency/failure regression on touched tables (ticket AC).
- Worker: `spark-k8s payload` RSS logs (PDAE-8348) if Spark fallback is used on large hot tables.

## Explicitly not in this session

- No live preset, no assignment, no `POST /maintenance/run` for this cohort.
- No change to PDAE-8274 investigation.
- `mview.dbt_gold_user_cohort_base_facts_free` is Wave 2-shaped (queried + written) but should be a **named** batch-3 table, not the canary.
