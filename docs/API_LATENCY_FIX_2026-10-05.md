# UI slowness: API payload and N+1 fixes (2026-10-05)

Every page on cryo-prod took up to ~1 minute to load. The database was not the bottleneck
(the Tables list query runs in 0.01s); the API was.

## What was measured (prod, via port-forward)

| Endpoint | Before | Cause |
|---|---|---|
| `GET /tables?limit=50` (Tables page) | 7.7s, **39.8 MB** | every row carried `stats.raw`; 97% of it was `raw.partitions_to_rewrite` (up to 5,000 partitions per table, 6.3 MB for one table) |
| `GET /runs?limit=200` (app shell) | 20.1s | eager-loaded every job of 200 runs (`jobs` = 635k rows) plus each job's table, and ran `finalize_open_runs` per request |
| `GET /advanced/fast-path-findings` | 12.1s | `compute_property_recommendations` ran the layout analyzer, which loads each table from the live Hive catalog |
| `GET /s3-tiering-recommendations` | 5.1s | ~6 queries per large table, each loading the full `TableStats` row including `raw` |

The API ran one uvicorn process per pod with a 1-CPU limit, so one heavy response blocked
every other request on that pod — which is why all pages felt slow, not just Tables.

## Fixes

- `list_tables` defers `TableStats.raw` / `TableCost.raw` and serializes with
  `table_to_read(..., include_raw=False)` (also used by `_top_cost_opportunities`). The detail
  endpoint still returns `raw`; it is the only place the UI reads it.
- `GZipMiddleware` on the API (Starlette 1.3 skips `text/event-stream`, so SSE is unaffected).
- `/runs`, `/runs/{id}`: target labels come from `presets.run_target_summaries` (one window
  query) instead of loading jobs. `finalize_open_runs` is no longer called on API reads — the
  governor already runs it every tick.
- Fast-path sweep calls `compute_property_recommendations(..., include_bloom_filters=False)`;
  the audit listing loads table names in one query.
- Tiering sweep: one query for tables + sizes, two grouped queries for last access, region
  cached per source.
- `api.workers` (default 2) in the Helm chart; prod CPU limit 1 → 2 (requests unchanged).
- `CorrelationMiddleware` logs `slow request ...` at WARNING for requests ≥ 2s.
- Scanner (Python and Go paths) writes only the worst 50 partitions to
  `raw.partitions_to_rewrite` (`RAW_PARTITION_PREVIEW_LIMIT`). The full list already lives in
  `table_partitions`; nothing read the full copy back. Existing rows shrink on their next scan.

Not done on purpose: making `TableStats.raw` ORM-deferred globally. Many call sites read
`stats.raw` after their session closes and would raise `DetachedInstanceError`.

## Pending: reclaim `table_stats_history` TOAST bloat (off-peak only)

`table_stats_history` is 49 GB on disk, of which 48 GB is TOAST, but live `raw` is ~1.5 GB
(the TOAST table has seen 25.4M chunk inserts and 24.7M deletes). Autovacuum makes the space
reusable but never returns it. `pg_repack` 1.5.2 is available on the RDS instance (PG 17.7)
but not installed.

Run during the IST off-peak window, after the scanner cap above is deployed:

```sql
CREATE EXTENSION IF NOT EXISTS pg_repack;  -- needs rds_superuser
```

```bash
# From a pod/host with the pg_repack 1.5.2 client and network access to RDS.
pg_repack --host "$PGHOST" --username "$PGUSER" --dbname "$PGDATABASE" \
  --table cryo.table_stats_history --no-superuser-check --wait-timeout 60
```

pg_repack takes an exclusive lock only briefly at start and end; it needs free disk roughly
equal to the live table size (~2.5 GB with indexes). Verify afterwards with
`pg_total_relation_size('cryo.table_stats_history')`.
