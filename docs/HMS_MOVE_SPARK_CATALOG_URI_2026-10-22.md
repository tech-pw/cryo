# Hive Metastore move and the Spark catalog URI — 2026-10-22

The prod Hive Metastore moved from `trino-prod-metastore.penpencil.co:9083` to
`trino-metastore-v2.penpencil.co:9083` on 2026-10-21 (Trino catalogs were repointed at
18:40 UTC). The old address stopped accepting connections from the Cryo cluster. Cryo broke in
two places.

## What broke

| Path | Symptom | Cause |
|------|---------|-------|
| Scanner (`cdp` source) | Every scan after 18:00 UTC discovered 0 tables. Each run waited 600 s, then reported `succeeded`. | The source `uri` still named the old host. `run_scan` raises on a discovery failure only for a source-scoped run (`source_id` set). Table-scoped and cron runs swallowed it. |
| Spark-on-K8s maintenance (`spark-k8s-fallback`) | Jobs failed with `java.net.ConnectException: Connection timed out` from `HiveMetaStoreClient.open`. It kept failing after the source was repointed. | The destination carried its own `spark_k8s.catalog_uri` with the old host, and the worker path let it override the source URI. |

## Catalog URI precedence

A Spark destination is shared by every table routed to it, whatever catalog that table lives in.
Its `catalog_uri` and `catalog_warehouse` are a static default. The table's source always has the
right values for its own tables, so the **source wins** whenever it sets them. The destination
value only applies when the source has none.

All three Spark dispatch paths now follow this rule:

- `SparkK8sEngine.with_source_config` (worker dispatch, `cryo/worker/worker.py`). It had let a
  non-empty destination value win until this fix.
- `cryo.governor.native_admit._fallback_engine_config` (native → Spark SQL fallback).
- `cryo/api/advanced_routes.py` (ad-hoc Spark client for a table).

S3 keys keep the opposite rule. A destination's explicit `s3_access_key`/`s3_secret_key` is a
deliberate operator override and wins over the source's.

## Operator notes

- When a metastore moves, repoint the **source** (Connections → Lakehouses). Leave
  `spark_k8s.catalog_uri` empty on shared Spark destinations.
- A Spark job submitted before the change keeps the URI it was submitted with. It fails or
  finishes on its own. Re-run it afterwards.
- A scan that reports `succeeded` with `discovered: 0` for a table that exists is a discovery
  failure. Read the scanner-worker log for `discover tables(<source>) exceeded`.

## Open follow-up

- Table-scoped and cron scans still report `succeeded` when discovery fails
  (`cryo/scanner/run.py`, the `source_id is not None` guard). They should fail the chunk and
  set `last_scan_error` on the requested tables.
