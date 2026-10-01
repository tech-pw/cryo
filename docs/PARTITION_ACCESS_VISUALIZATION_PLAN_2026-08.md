# Partition access visualization — plan

**Inspired by:** [Yelp Engineering Blog — Partition Access Visualizations](https://engineeringblog.yelp.com/2026/05/partition-access-visualizations.html)
(2026-05). Their build: S3 server access logs → daily batch SQL parses each object key into
`(table, partition, iam_role, day)` → scatter plot of partition-key vs. access-timestamp. Visual
patterns (y=x diagonal = daily batch job, vertical line = backfill, scatter = ad hoc queries)
drove S3 storage-class/retention decisions and got them a 33% storage cost cut.

**Why this is a smaller lift for Cryo than it was for Yelp:** Yelp built this from raw S3 logs
with no existing partition model. Cryo already has most of the pieces, just not wired together:

| Piece | Already exists as |
|---|---|
| S3 server access-log ingestion, keyed by table | [`cryo/scanner/s3_request_cost_ingest.py`](../cryo/scanner/s3_request_cost_ingest.py) — real boto3 log parsing, `_match_table_prefix` against `Table.location`, already in prod |
| Canonical per-partition value model | `TablePartition` ([`cryo/db/models.py:398`](../cryo/db/models.py#L398)) — `partition_value`, `partition_values` (JSONB), `partition_path` (Text), scanned on a cadence |
| "Attribute an access event to a specific partition" | [`cryo/planner/partition_query_affinity.py`](../cryo/planner/partition_query_affinity.py) — already does this for Trino query-log SQL text against `TablePartition.partition_values` (AutoComp/SIGMOD 2025 benefit scoring) |
| Scatter-plot-capable frontend | `web/` already ships recharts (see `Anomalies.tsx`, `TableSimilarity.tsx`'s cluster chart) |

The one real gap: `_match_table_prefix` resolves an S3 key down to a `table_id` and discards the
rest of the path — the partition-value suffix is never persisted. Everything else is either
already built or a straightforward extension of an existing pattern.

## Scope

1. **Extend S3 log ingestion to retain partition path, not just table_id.**
   - New function `_match_table_partition(key, table_prefix_match, table_id) -> str | None` in
     `s3_request_cost_ingest.py`, called right after `_match_table_prefix` resolves the table.
   - Since Hive-style layouts put the partition directory directly after the table's data
     prefix (`s3://bucket/.../data/dt=2026-08-19/...`), this is a suffix-strip, not a new
     parser: `key.removeprefix(matched_table_prefix)`, then take the leading path segment(s)
     up to the file name.
   - Cross-check against `TablePartition.partition_path` for that `table_id` (already scanned,
     already indexed via `ix_table_partitions_table_scan`) rather than trusting the raw string
     blindly — a key that doesn't match any known `partition_path` gets recorded with
     `partition_path=None` (unpartitioned table, or a partition scanned-away since the log was
     written) rather than guessed at.

2. **New rollup table: `PartitionAccessDaily`.**
   - Grain: `(table_id, day, partition_path, requester_role)` — mirrors `S3RequestCostDaily`'s
     shape but one dimension finer, and deliberately a **separate** table rather than adding
     `partition_path` to `S3RequestCostDaily` itself: that table's whole read path
     (`recompute_table_cost_rollup`, `TableCost`) is table-day granularity by design, and
     bolting partition cardinality onto it would blow up every existing cost query's row count
     for no benefit to cost reporting. Same reasoning `cost_attribution.py`'s docstring already
     used to justify `QueryCostAttributionDaily` as its own table.
   - Columns: `table_id`, `day`, `partition_path`, `requester_role` (IAM role/principal from
     the log line — Cryo's log parser already has this field available, just unused today),
     `request_count`, `first_seen_at`, `last_seen_at`.
   - Retention: prune on the same cadence as `S3RequestCostDaily` (`prune_s3_request_cost_daily`,
     35-day default) — this is an operational-visibility table, not a billing record, no reason
     to keep it longer than that one.

3. **Migration**: `PartitionAccessDaily` model + Alembic revision, new indexes
   `(table_id, day)` and `(table_id, partition_path)` for the two query shapes the chart needs
   (table's access-over-time, and a specific partition's access history).

4. **Backend read endpoint**: `GET /api/v1/tables/{id}/partition-access` — returns the raw
   `(partition_path, day, request_count)` triples for the chart, plus a computed
   `days_since_last_access` per partition (directly reusable by `expire_snapshots`/retention
   logic later, see below). Bound the response (e.g. last 90 days, top N partitions by
   recency) rather than shipping the whole table's history to the browser.

5. **Frontend: new Insights page, `PartitionAccess.tsx`.**
   - Scatter chart, partition path (sorted, e.g. by date if the partition column is a date
     transform) on Y, access day on X — same shape as Yelp's chart. recharts `ScatterChart`,
     consistent with the existing chart usage in `Anomalies.tsx`/`TableSimilarity.tsx`.
   - Table picker (reuse the existing table-search/autocomplete component already used
     elsewhere in `web/`) since this is inherently a per-table view, unlike the fleet-wide
     Anomalies/Dark Tables pages.
   - Read-only. No new mutation surface, so no new safety/RBAC surface beyond what
     table-detail pages already require.

6. **Tests**: mirror `test_s3_request_cost_ingest.py`'s existing coverage shape for the new
   partition-matching function (known-partition match, unknown-path fallback, unpartitioned
   table). Add `test_partition_access_api.py` for the endpoint. Add
   `PartitionAccess.test.tsx` for the chart page (existing `*.test.tsx` pattern in `web/`).

## Explicitly out of scope for this pass

- **Wiring partition-recency into `expire_snapshots` retention automatically.** The data this
  produces (`days_since_last_access` per partition) is exactly what a smarter retention rule
  would want, but that's a planner-behavior change with its own safety review — do it as a
  deliberate follow-up once the visualization has been used manually for at least one real
  storage-class/retention decision, not bundled into the same change that introduces the data.
- **S3 Storage Class / Intelligent-Tiering automation.** Yelp's 33% number came from *acting*
  on the visualization (recommending storage classes and expanding deletion-based retention; the
  post does not say objects were re-tiered by hand -- corrected 2026-10-01), not from the
  visualization itself. Researched in `ARCHIVE_TIERING_RESEARCH_2026-10-01.md`. Cryo
  doesn't currently manage S3 storage class at all — that's a materially bigger, separate
  feature (needs S3 PutObject-tagging/lifecycle-policy write access, a new safety boundary).
  This plan only builds the visibility Yelp used to make that call by hand.
- **IAM-role-level access dashboards** (Yelp mentions this as a facet of their chart). Cryo's
  log parser can capture `requester_role` (see scope item 2) but a role-centric view ("what
  does this pipeline/team actually touch") is a different page with different audience and
  RBAC questions — worth a follow-up plan doc of its own if wanted, not bundled here.

## Suggested order of work

1. Migration + model (`PartitionAccessDaily`) — inert until step 2 populates it.
2. Ingestion extension (`_match_table_partition`, wired into `ingest_bucket`).
3. Read API.
4. Frontend page.
5. Let it run a few days before drawing conclusions from real data, same discipline as the
   query-cost user-attribution plan (`QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md`) — a
   scatter chart against one day of data won't show the diagonal/vertical patterns that make
   this useful at all.
