# PDAE-8347 — Spark-on-K8s driver failures

Queried production `cryo.jobs` via `cryo-api` on 2026-08-18 (failed `spark_emr`
jobs, last 14 days). Truncation code (`truncate_error_message` / PDAE-8346) was
not changed here.

## Pattern 1 — temp/test tables

Already mitigated live on 2026-08-11 with preset-rule predicates. Code follow-up:
`cryo.scheduler.rules.is_ephemeral_maintenance_table` so a **new** catch-all
preset cannot re-admit scratch tables. Pinned assignments still win. Carve-out:
`testpress`. Suffix `_test` only; `gold_test_student_mappings` stays eligible.

## Pattern 2 — `BaseMetastoreTableOperations.current`

Pre-8346 stored errors showed only the middle of the Spark stack
(`BaseMetastoreTableOperations.current`). Post-8346 `[root cause]` lines on the
same gold tables are:

`Caused by: software.amazon.awssdk.services.s3.model.NoSuchKeyException`

That is a **stale catalog metadata location** (Hive/REST pointer vs S3), not an
Iceberg commit conflict. Retry/backoff on Spark `execute()` covers:

- `BaseMetastoreTableOperations`
- `CommitFailedException` / `CommitStateUnknownException` / Iceberg
  `ValidationException`

Dropped-table signatures (`NoSuchTableException`, …) do **not** retry.

Async `submit()` + `fallback_sweep` (PDAE-8352) is unchanged: fire-and-forget
cannot cheaply retry without a second Job and governor-wave changes.

## NoSuchKey vs `remove_orphan_files`

No code guard added. Iceberg `remove_orphan_files` uses `older_than` (planner
default 7 days) and must not delete the *current* `metadata.json`. These Spark
failures happen in `loadTable` / `BaseMetastoreTableOperations.current` **before**
the procedure body runs, so this job's own orphan cleanup cannot be the deleter.
A concurrent expire/commit from another engine can leave HMS/REST pointing at a
just-replaced object; retry is the appropriate mitigation. Bare `NoSuchKey`
without `metadata.json` or "could not open manifest file" is intentionally
**not** `is_table_not_found_error` (would deactivate live gold tables).
