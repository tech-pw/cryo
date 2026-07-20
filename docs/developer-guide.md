# Developer Guide

This guide documents how the current backend is structured and how to extend it safely.

## Layering Rules

- `cryo/domain` contains enums and pure DTOs. It should not import DB, API, queue, or engine clients.
- `cryo/planner` is pure planning logic. It emits `MaintenancePlan` objects and structured
  `PartitionPredicate`s, never SQL strings.
- `cryo/engines/trino` and `cryo/engines/spark` own SQL rendering for their dialects.
- `cryo/scanner` reads Iceberg metadata and writes Postgres stats. It should not execute maintenance.
- `cryo/api` validates requests and calls repository/service helpers.
- `cryo/db/repositories` contains DB-facing helpers and ORM-to-domain adapters.
- `cryo/scheduler`, `cryo/governor`, and `cryo/worker` coordinate queued maintenance.
- `cryo/queue` owns Redis/in-memory queue and lock implementations.

## Important Modules

| Area | Paths |
|------|-------|
| API routes/schemas | `cryo/api/routes.py`, `cryo/api/schemas.py` |
| API auth | `cryo/api/security.py`, `cryo/api/deps.py` |
| Predicate grammar | `cryo/api/predicates.py`, re-used by `cryo/scheduler/rules.py` |
| Preset repositories | `cryo/db/repositories/presets.py` |
| Datalake/dashboard repositories | `cryo/db/repositories/datalake.py` |
| Scanner | `cryo/scanner/analyze.py`, `cryo/scanner/run.py`, `cryo/scanner/persistence.py` |
| Source catalogs | `cryo/sources/base.py`, `cryo/sources/rest.py`, `cryo/sources/factory.py` |
| Planner | `cryo/planner/planner.py`, `cryo/planner/partitions.py`, `cryo/planner/chunking.py` |
| Trino engine | `cryo/engines/trino/client.py`, `load.py`, `engine.py`, `sql.py` |
| Spark renderer | `cryo/engines/spark/sql.py` |
| Orchestration | `cryo/scheduler`, `cryo/governor`, `cryo/worker`, `cryo/queue` |

## Predicate Grammar

The shared predicate grammar supports:

- compound nodes: `all`, `any`, `not`
- leaf fields from table/stats columns
- operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `like`, `glob`

Add fields in `FIELD_MAP` in `cryo/api/predicates.py` and add parity tests in
`tests/test_predicates.py`. The same predicate must behave consistently for SQLAlchemy filtering and
in-memory rule evaluation.

`source_id` is the supported catalog filter for table lists and bulk selection. The Tables UI exposes
this as a multi-select **Catalog** filter backed by `cryo.sources`.

## Table Identifiers

Cryo uses three related identifiers for Iceberg tables:

| Layer | Identifier | Notes |
|-------|------------|-------|
| API primary key | `tables.id` (UUID) | Stable key for routes, jobs, assignments |
| Natural key | `(source_id, schema_name, table_name)` | Enforced in Postgres; one row per catalog table |
| Display / SQL reference | `catalog.schema.table` | Derived from source config via `cryo/db/model_utils.py` |

Helpers:

- `catalog_name_for_source(source)` — `config.catalog_name` / `config.catalog`, else `source.name`
- `qualified_table_name(table)` — full `catalog.schema.table` for UI, logs, and planner `table_ref`

`GET /tables` returns `catalog_name`, `source_name`, and `qualified_table_name` on each `TableRead`.
The Tables page shows `schema.table` in the name column, a separate **Catalog** column, and uses the
qualified name in tooltips, CSV export, and cross-catalog pickers.

## API Auth

Local development uses role tokens:

- `Authorization: Bearer viewer`
- `Authorization: Bearer operator`
- `Authorization: Bearer admin`

Route guard dependencies live in `cryo/api/deps.py`:

- `require_viewer`
- `require_operator`
- `require_admin`

Tests can override `get_current_principal` with `app.dependency_overrides`.

## Scanner Development

Scanner analysis should remain metadata-driven:

- Use PyIceberg table metadata and manifest/file entries.
- Do not scan live data rows.
- Preserve partition fields and raw partition values.
- Keep bucket/object-like transforms safe for planning by preserving metadata and avoiding unsafe SQL
  rendering.

When changing scanner output, verify planner compatibility because `table_stats.raw` and
`table_partitions.partition_fields` feed partition-aware planning.

## Planner And Renderer Development

Planner changes require unit tests around:

- partition transform detection
- predicate building
- min-small-files gating
- small-table bypass
- large partition chunking
- bucket/unsupported transform safety

Renderer changes require golden tests for Trino and Spark. Keep SQL dialects separate.

**Identity / day date partitions:** Iceberg stores identity-partitioned `date` values (and
day-transform values) as days-since-1970-01-01 (e.g. `20643`). Scanner stats keep that raw
int/digit string. All three engines must accept epoch-day ints **and** digit strings:

| Engine | Path | Handling |
|--------|------|----------|
| Trino | `engines/trino/sql.py` `_parse_date` / `_timestamp_string` | epoch-day → `DATE`/`TIMESTAMP` literal |
| Spark | `engines/spark/sql.py` (same helpers) | same |
| iceberg-go | `engine-go/internal/predicate` `dayStart` + identity date/ts | epoch-day → `iceberg.Date`/`Timestamp` |

Calling `date.fromisoformat('20643')` (or emitting `TIMESTAMP '20643'`) is the failure mode.

**iceberg-go + delete files:** `cryo/engines/native/routing.py` hard-blocks native `OPTIMIZE`
when `cryo.table_stats.total_delete_files > 0` (iceberg-go cannot rewrite tables with delete
files). It does **not** fall back to Trino/Spark. The worker records OPTIMIZE as failed with a
clear error. `expire_snapshots` and `remove_orphan_files` still run on the native engine.

## Orchestration Development

The current scheduler/governor/worker implementation includes in-memory test stores and Redis-backed
queue primitives. This makes unit testing easier while the production service loops mature.

When implementing service loops:

- Keep governor admission independent of worker replica count.
- Use per-table locks before admitting work.
- Release locks and decrement in-flight counters on every terminal path.
- Record audit entries for every statement.
- Keep failures isolated per table/job.

## Test Map

| Test File | Coverage |
|-----------|----------|
| `tests/test_config.py` | Settings defaults and env behavior. |
| `tests/test_model_imports.py` | SQLAlchemy model registration and JSONB fields. |
| `tests/test_scanner.py` | Scanner health/cost/chunking/analyze/persistence. |
| `tests/test_predicates.py` | Shared predicate compile/evaluate parity. |
| `tests/test_api.py` | Health and maintenance preview behavior. |
| `tests/test_presets_repository.py` | Preset repository/adapters/sort/selection helpers. |
| `tests/test_presets_api_db.py` | DB-backed preset, assignment, and run API flows. |
| `tests/test_datalake_api.py` | Datalake dashboard, cost, drilldown, and scan-trigger routes. |
| `tests/test_api_security.py`, `tests/test_security.py` | Local auth and role enforcement. |
| `tests/test_partitions_and_planner.py` | Planner and partition predicates. |
| `tests/test_chunking.py` | Greedy chunk packing. |
| `tests/test_trino_sql_golden.py` | Trino SQL renderer. |
| `tests/test_spark_sql_golden.py` | Spark SQL renderer. |
| `tests/test_trino_engine.py` | Trino client/load/execution wrapper. |
| `tests/test_admission.py` | Governor admission math. |
| `tests/test_queue_and_governor.py` | Queue and wave admission. |
| `tests/test_worker.py` | Worker plan/execute/audit lifecycle. |

## Quality Commands

```bash
.venv/bin/ruff check cryo tests
.venv/bin/ruff format --check cryo tests
.venv/bin/mypy cryo
.venv/bin/python -m pytest
```

Run focused tests first while developing, then the full suite before handing off.

## Updating TODOs

Update `todo.md` only for work that is actually implemented and verified. Use:

- `[x]` for implemented and tested.
- `[~]` for partial implementation or library core without production service wiring.
- `[ ]` for not started.
