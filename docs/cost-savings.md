# Cost & Savings Projection — Implementation Brief

Pickup brief for the next coding session. Design source of truth is `lld.md` §16 (data model,
config, algorithm), with supporting context in `plan.md` (§2 item 11, decision D12) and `hld.md`
(§3.10 estimator, §5.5 data flow). This file is the practical "how to build it" guide (backend).
For the **UI** side — the health-dashboard charts that turn these numbers into storage-gain and
cost-savings visuals — see `docs/ui-cost-savings.md`.

## What we're adding

Per table / schema / datalake:

- **Current monthly storage cost** (precise where the scan tier allows).
- **Projected cost + savings after maintenance**, attributed per operation:
  `optimize` (compaction), `expire_snapshots`, `remove_orphan_files`, `optimize_manifests`.
- Explorer filter/sort by projected savings; dashboard of biggest wins; post-run realized savings.

## The core idea (precise *and* cheap)

Precision and scan cost pull in opposite directions, so the scan is **tiered** — cost grows only when
precision demands it — and the money math is a **pure model** reused by scanner, API, and worker.

| Tier | What it computes | Cost | `precision` |
|------|------------------|------|-------------|
| 0 heuristic | current-snapshot bytes + snapshot-overhead heuristic (today's `estimate_storage_cost`) | ~free | `heuristic` |
| 1 metadata | live/dead/metadata byte split + exact expire/optimize/manifest reclaim, by walking retained snapshots' manifests | cheap metadata reads, **no data reads, no S3 LIST** | `metadata` |
| 2 storage-truth | orphan bytes via a bounded, paginated S3 LIST of the table's data prefix | S3 LIST requests (the only paid step) — opt-in, slow cadence | `storage_truth` |

Default daily scan = **Tier 0+1** (precise for expiry/compaction/manifests, no LIST). Orphans are
layered in periodically by Tier 2. Reclaim is computed the way Iceberg itself computes deletions, so
projections match reality.

## Byte accounting (disjoint buckets → no double counting)

`current_storage = live + delete + dead + metadata + orphan`

- `live_bytes` — data files in the **current** snapshot (kept).
- `delete_bytes` — delete files in the current snapshot → merged by `optimize`.
- `dead_bytes` — files referenced **only** by snapshots the retention policy would expire → `expire_snapshots`.
- `metadata_bytes` — manifests + manifest lists + `metadata.json` + stats/puffin → `optimize_manifests` (partial) + expiry.
- `orphan_bytes` — files under the prefix referenced by **no** snapshot (Tier 2 only) → `remove_orphan_files`.

## Exact expiry reclaim (metadata only, mirrors Iceberg)

```
surviving = { s : s is current
                 OR s.timestamp_ms >= now - retention_days
                 OR s in newest retain_last snapshots }
for file f across all retained snapshots' manifests:
    if { snapshots referencing f } ∩ surviving == ∅:  f reclaimed by expire_snapshots
reclaim_expire_bytes = Σ size(f)  # data + delete + manifest + manifest-list
```

`retention_days` / `retain_last` = effective-preset params (plan-accurate) or
`default_retention_days=7` / `default_retain_last=1` (datalake baseline; 7d matches the reference macro).

## Per-op savings (`cost/model.py`, pure)

`bytes_to_usd(b) = b / (1024³ if cost_use_gib else 1e9) * price_per_gb_month`

- `expire` = `bytes_to_usd(reclaim_expire_bytes)`
- `remove_orphan_files` = `bytes_to_usd(orphan_bytes)` (0/unknown until Tier 2)
- `optimize` (storage) = `bytes_to_usd(delete_bytes merged)` — data bytes are rewrite-neutral; small-file
  bytes are freed via the follow-on expire. Keep conservative — never over-promise.
- `optimize` (request, shown separately) = `Δfiles × query_gets_per_file × query_count_30d × s3_get_usd_per_1k/1000`,
  `Δfiles = small_files − ceil(small_bytes / target_file_size)`.
- `optimize_manifests` = `bytes_to_usd(reclaim_manifests_bytes)`.

`projected_savings = Σ storage savings`; `projected_cost = current_cost − projected_savings`.

## Build order (suggested)

1. **Migration** — `20260703_0002_cost_tables.py` adds `cryo.table_cost`, `cryo.table_cost_history`,
   `table_stats` summary columns, and `audit_log.actual_bytes_freed`. **Done.**
2. **Domain** — `CostPrecision` in `cryo/domain/enums.py`; `StorageBreakdown` / `SavingsProjection`
   in `cryo/domain/models.py`. **Done.**
3. **`cost/model.py`** — `bytes_to_usd()`, `project_savings()`, `ReclaimBytes`, `CostStatsInput`.
   Tests in `tests/test_cost_model.py`. **Done.**
4. **`scanner/cost.py` Tier 1** — `account_metadata_cost()`, `build_table_cost_analysis()`,
   bounded snapshot/manifest walk, heuristic fallback when bounds exceeded. **Done.**
5. **`scanner/orphans.py` Tier 2** — `scan_orphans()`, cadence via `should_run_orphan_scan()`,
   age guard, max-list bound, carry-forward. **Done.**
6. **`scanner/run.py`** — `--cost-full-scan`, persist `table_cost` + history + summary columns.
   **Done.**
7. **API** — plan-accurate projection in `/maintenance/preview`; `/tables/{id}/cost`;
   `/dashboards/cost`; predicate-grammar cost fields; `/scans` `cost_full_scan`. **Done.**
8. **Worker realized savings** — capture `actual_bytes_freed` per op → predicted-vs-realized trend.
   **Done** (optional per engine; trend line appears when audit rows carry bytes).
9. **UI + reporting** (Phase 5/7) — Cost & Savings page (`/cost`), explorer columns, detail breakdown,
   PlanPreview savings. **Done** for UI; reporting section + Prometheus metrics remain future work.

## Config knobs (`cryo/config.py`, see `lld.md` §2)

`cost_price_per_gb_month` (0.023, parity), `cost_use_gib` (GiB vs 10⁹ GB billing), `cost_scan_tier`,
`cost_scan_max_snapshots/_manifests`, `cost_full_scan_every_hours`, `cost_orphan_min_age_hours`,
`cost_scan_max_list_keys`, `default_retention_days=7`, `default_retain_last=1`,
`s3_get_usd_per_1k`, `s3_list_usd_per_1k`, `query_gets_per_file`.

## Layering & parity guardrails

- `cost/model.py` is **pure** (no SQL, no I/O), like the Planner. The manifest walk (I/O) lives in the
  Scanner; the LIST (paid) lives in `scanner/orphans.py` — never in the model.
- Keep `0.023/GB-month` + snapshot-overhead heuristic as Tier 0 (parity with the ported stats batch);
  `7d` retention default matches `retention_threshold => '7d'` in the reference dbt macro.
- Read the reference stats batch for cost parity:
  `/Users/pw/PycharmProjects/pw-data-connectors/airflow/dags/data_team/iceberg_compaction_utils/iceberg_table_stats_batch.py`.

## Tests (`lld.md` §14)

- Pure model golden tests; heuristic tier == existing `estimate_storage_cost`.
- Multi-snapshot fixtures: predicted expire reclaim == Iceberg's own deletion set.
- MinIO orphan fixtures: seed unreferenced files, assert detection + bounding + age guard.
- Smoke: predicted savings reconcile with realized `actual_bytes_freed` within tolerance.
