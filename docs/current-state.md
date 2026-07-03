# Current Implementation State

Last updated from the backend/API build sessions on 2026-07-01.

## Implemented

### Foundations

- Python package scaffold under `cryo/`.
- Pydantic settings in `cryo/config.py`.
- SQLAlchemy models and Alembic migration for the `cryo` schema.
- Local compose stack with MinIO, Iceberg REST, Trino, Spark runner, Postgres, and Redis.
- Makefile targets for install, lint, typecheck, tests, scanner, services, fixtures, and smoke checks.

### Scanner

- Source catalog abstraction and REST/Hive/Glue catalog adapters (`cryo/sources/`).
- PyIceberg table analysis for file counts, record counts, data size, manifests, snapshots, delete
  files, partition fields, partition values, small-file counts, and raw metadata.
- Health score and A-F grade calculation.
- Tier 0 S3 monthly storage cost estimate (`estimated_monthly_s3_cost_usd`).
- **Tier 1 metadata-precise cost accounting** (`account_metadata_cost`) with live/dead/metadata
  byte split and per-operation reclaim projections.
- **Tier 2 opt-in orphan scan** (`scanner/orphans.py`) with `--cost-full-scan`, cadence gating,
  age guard, and carry-forward of last-known orphan bytes.
- Pure cost projection model (`cryo/cost/model.py`) shared by scanner and future API/Worker paths.
- Persistence to `table_cost`, `table_cost_history`, and denormalized summary columns on
  `table_stats` (`projected_monthly_s3_cost_usd`, `projected_savings_usd_month`, `reclaimable_bytes`).
- Deterministic scan chunking and idempotent persistence to:
  - `cryo.tables`
  - `cryo.table_stats`
  - `cryo.table_partitions`
  - `cryo.table_stats_history`
- Local fixture coverage for unpartitioned, day, month, year, bucket, multi-column, timestamp/day,
  and nested/object-like schemas.
- Kubernetes scanner CronJob manifest at `deploy/helm/cryo/templates/scanner-cronjob.yaml` (indexed
  parallel chunks).

### Planner And Renderers

- Engine-agnostic planner and partition predicate model.
- GB-budgeted chunking with max partitions/chunk and oversize-alone behavior.
- Trino SQL renderer for expire snapshots, remove orphan files, optimize, and manifests.
- Spark SQL renderer for Iceberg stored procedures.
- Bucket predicates are intentionally treated as unsupported for SQL filters unless explicitly
  enabled by capability, preventing unsafe generated `WHERE` clauses.

### P3 Orchestration Core

- Redis-backed and in-memory queues.
- Leader and per-table locks.
- Governor admission math for caps, off-peak windows, cooldown, deadlines, adaptive budget, and
  round-robin replica assignment.
- Scheduler rule resolution with pinned assignments beating highest-priority rules.
- Worker lifecycle that builds a plan, renders statements, executes an engine, records audit
  entries, and updates job status.
- Trino client, load probe, and statement execution adapter.

### P4 API

- FastAPI app factory and health endpoint.
- Local bearer/JWT-shaped role scaffolding with `viewer`, `operator`, and `admin`.
- Shared predicate grammar for SQLAlchemy filtering and in-memory rule evaluation.
- Source, destination, preset, rule, assignment, table, run, and job API schemas.
- CRUD and management routes for sources, destinations, presets, preset rules, assignments,
  simulation, effective preset lookup, maintenance preview/run, runs, jobs, and cancellation.

## Partial Or In Progress

- Field-level secret encryption for source/destination credentials.
- CI pipeline.
- Optional dual-write Iceberg scanner history table.
- Long-running production scheduler/governor/worker service loops.
- Full DB-backed end-to-end scheduled maintenance flow.
- `/api/v1/scans` trigger endpoint.
- Production-grade signed JWT/OIDC auth. Current auth is dev-friendly local/JWT-shaped role parsing.
- Spark/EMR execution engine.
- Web UI.
- Reporting, metrics, alerts, Helm chart, and hardening.

## Planned (specced, not yet implemented)

- **Cost API + dashboards** (P4/P5): plan-accurate preview projections, `/tables/{id}/cost`,
  `/dashboards/cost`, explorer filters — scanner persistence is done; API/UI wiring remains.

## Verification Snapshot

Recent checks from the implementation sessions:

- Unit/API tests: `63 passed`.
- Ruff lint/format check: passed.
- Mypy: passed for `cryo`.
- Local scanner: scanned 13 local REST catalog tables.
- Local fixture smoke:
  - `make seed-fragmented`
  - `make smoke-file-counts`
  - `make smoke-compact`

The local compaction smoke reduced file counts across the fixture matrix, including bucket,
day/month/year, multi-column, datetime, nested/object-like, and unpartitioned tables.

## Known Environment Note

The full `pip install -e ".[dev]"` may backtrack into old `pyiceberg`/`pyarrow` builds on Python
3.12 in some environments. A working minimal test environment was restored with direct installs of
the runtime/test packages plus plain `pyiceberg`. Prefer the repository’s established `.venv` when it
already exists; if recreating it, verify the resolver picks a wheel-backed `pyiceberg`/`pyarrow`
combination before running local scanner tests.
