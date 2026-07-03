# Current Implementation State

Last updated: 2026-07-03 (Phase 1-8 implementation complete).

## Implemented

### Phase 1 — Core Plumbing ✅

- `cryo/infra/wiring.py` — centralized RuntimeWiring with Redis client, locks, job/scheduler stores
- `cryo/db/repositories/job_store.py` — SQL-backed worker job store
- `cryo/db/repositories/scheduler_store.py` — SQL-backed scheduler store with Redis last-run
- `cryo/db/repositories/run_finalizer.py` — Governor finalizes open runs each wave
- `cryo/engines/trino/client.py` — per-coordinator connection (HTTPS + BasicAuth)
- `cryo/engines/trino/load.py` — `system.runtime.queries` OPTIMIZE probe
- `cryo/engines/trino/engine.py` — full `MaintenanceEngine.execute` implementation
- `cryo/engines/spark/emr.py` — EMR Serverless submit/poll/logs
- `cryo/engines/spark/engine.py` — Spark `MaintenanceEngine` implementation
- `cryo/engines/spark/local.py` — `SparkLocalEngine` via `docker compose exec spark`
- `cryo/engines/factory.py` — routes to Spark/Trino/Native based on destination config
- `cryo/governor/governor.py` — wave loop with `run_wave()` and `finalize_open_runs()`
- `cryo/worker/worker.py` — claim job → plan → execute → audit via `process_next()`
- `cryo/scheduler/scheduler.py` — cron eval → create run + jobs → enqueue
- `cryo/scheduler/routing.py` — size-based routing rules (`size_gb > 100 → spark-large`)
- `cryo/cli.py` — unified CLI with `cryo governor|worker|scheduler|serve|events`
- `.github/workflows/ci.yml` — lint + typecheck + unit tests on push
- `start-local.sh` — orchestrates local daemons
- `scripts/smoke_cryo_e2e.py` — end-to-end orchestration smoke test

### Phase 2 — Cost Dashboard + Health Signals ✅

- `cryo/api/preview_cost.py` — `SavingsProjection` calculation from table stats
- `cryo/api/routes.py` — `SavingsProjection` wired into `/maintenance/preview`
- `cryo/api/schemas.py` — `SavingsProjectionRead`, `cost_full_scan` on scan trigger
- `cryo/scanner/signals.py`:
  - `metadata_to_data_ratio` — explicit health metric
  - `detect_streaming_from_history` — commits/day velocity, `is_streaming` flag
  - `detect_format_version` — V2/V3 detection
  - `is_v3_upgrade_candidate` — flags V2 with high delete_file_pct
- `cryo/scanner/persistence.py` — stores streaming/format/delete signals in `raw` column
- `cryo/db/models.py` — `raw: Mapped[JsonDict | None]` on `TableStatsHistory`
- `POST /maintenance/manifest-rewrite` — standalone manifest-only rewrite action

### Phase 3 — Operations & Safety ✅

- `cryo/planner/planner.py`:
  - Hot partition filtering via `_is_hot_partition()` with idle_hours check
  - Delete-ratio-triggered compaction via `delete_ratio_trigger` param
- `cryo/planner/guardrails.py`:
  - `validate_operation_order()` — blocks out-of-order ops
  - `VALID_OP_ORDER` — canonical sequence (expire → orphan → compact → manifests)
- `cryo/api/routes.py` — guardrail enforcement on `/maintenance/preview` and `/maintenance/run`

### Phase 4 — Event Fast-Path ✅

- `cryo/events/redis_stream.py` — Redis Streams-backed event bus (`RedisEventStream`)
- `cryo/events/watcher.py` — `TableCommitWatcher` polls hot tables, publishes `TableCommitted`
- `cryo/events/consumer.py` — `TableCommitConsumer` subscribes via consumer group
- `cryo/events/service.py` — wiring for watcher/consumer with `RuntimeWiring`
- `cryo/cli.py` — `cryo cli events` command runs event loop

### Phase 5 — Native Rust Engine ✅ Builds (Read-Only Operations)

- `cryo/domain/enums.py` — `EngineType.NATIVE`
- `cryo/engines/base.py` — `payload: dict[str, object]` field on `RenderedStatement`
- `cryo/engines/native/engine.py` — Python adapter implementing `MaintenanceEngine`
- `cryo/engines/native/payload.py` — JSON payload serialization
- `cryo-engine/src/main.rs` — JSON stdin/stdout protocol with full error reporting
- `cryo-engine/src/operations.rs` — Iceberg operations using iceberg-rust 0.10.x (git main)
- `cryo-engine/src/progress.rs` — JSONL progress reporting to stderr
- `cryo-engine/Cargo.toml` — Rust crate with iceberg-rust, tokio, tracing

**Status:** The Rust engine builds successfully and connects to Iceberg REST catalogs.
Operations currently perform read-only metadata analysis; full write operations
depend on iceberg-rust's table modification APIs which are still stabilizing upstream.

### Phase 6 — Compliance & Governance ✅

- `cryo/db/models.py` — `ProtectedSnapshot` model
- `migrations/versions/20260703_0003_protected_snapshots.py` — schema migration
- `cryo/compliance/governance.py`:
  - `track_pending_deletions()` — soft-delete tracking
  - `ProtectedSnapshotRegistry` — protected snapshot management
- `cryo/sources/latency.py` — `CatalogLatencyMonitor` with P50/P99
- `cryo/sources/monitored.py` — wraps catalogs with automatic latency recording
- `cryo/api/extensions.py`:
  - `GET /tables/{id}/compliance`
  - `POST /tables/{id}/protected-snapshots`
  - `GET /sources/{id}/catalog-latency`

### Phase 7 — AI Layer ✅

- `cryo/ai/anomaly.py` — EWMA/z-score anomaly detection over `table_stats_history`
- `cryo/ai/explain.py` — `explain_health_change()` with before/after narration
- `cryo/ai/copilot.py` — verified questions over Postgres metadata
- `cryo/mcp/server.py` — `CryoMcpServer` with read-only tools
- `cryo/api/extensions.py`:
  - `GET /tables/{id}/anomalies`
  - `POST /tables/{id}/explain-health`
  - `GET /ai/copilot/questions`
  - `POST /ai/copilot/ask`
  - `GET /mcp/tools`
  - `POST /mcp/tools/call`

### Phase 8 — Long-Term Features ✅

- `cryo/scheduler/adaptive_routing.py`:
  - `resolve_destination_adaptive()` — multi-engine routing
  - `rank_destinations()` — scoring by capacity, latency, cost
- `cryo/api/phase8_routes.py`:
  - `POST /api/v1/routing/preview`
  - CRUD for `/api/v1/tier-policies`
  - `GET /tables/{id}/tier-candidates`
- `cryo/tiering/service.py`:
  - `tier_candidates_for_table()` — identifies cold partitions
  - `summarize_tier_savings()` — potential storage savings
- `cryo/db/models.py` — `TierPolicyRecord` model
- `migrations/versions/20260703_0004_tier_policies.py` — schema migration
- `scripts/seed_local_config.py` — preset with adaptive routing + engine pool
- `docs/GOVERNANCE.md` — GitHub-first governance for pre-v1
- `CONTRIBUTING.md` — updated with governance reference

### Foundations (from earlier sessions)

- Python package scaffold under `cryo/`
- Pydantic settings in `cryo/config.py`
- SQLAlchemy models and Alembic migrations for the `cryo` schema
- Local compose stack with MinIO, Iceberg REST, Trino, Spark runner, Postgres, and Redis
- Makefile targets for install, lint, typecheck, tests, scanner, services, fixtures, smoke checks
- Source catalog abstraction and REST/Hive/Glue catalog adapters
- PyIceberg table analysis for all key metrics
- Health score and A-F grade calculation
- Full cost accounting with live/dead/metadata byte split
- Engine-agnostic planner and partition predicate model
- Trino and Spark SQL renderers
- FastAPI app with CRUD for sources, destinations, presets, rules, assignments
- Predicate grammar for SQLAlchemy filtering and in-memory rule evaluation

### Web UI ✅

React/TypeScript frontend per `UI_UX_DESIGN.md`:

- 7 pages: Health, Cost, Tables, Table Detail, Presets, Runs, Connections
- Dark theme with toggle (persisted to localStorage, respects system preference)
- Always-dark sidebar (premium enterprise pattern like Linear/Vercel)
- IBM Plex Sans/Mono typography
- Design tokens in `web/src/lib/theme.ts` (light + dark palettes)
- Theme context in `web/src/lib/themeContext.tsx`
- Grade/Priority/Status badge system
- Cost waterfall chart, savings by operation donut
- Bulk selection + filter-based assignment (`BulkAssignBar`)
- Plan preview with dry-run SQL rendering
- Live run console with streaming logs
- Sparklines for small-file pressure
- Responsive layout (mobile-friendly with sidebar collapse)
- Reduced motion support (`prefers-reduced-motion`)
- Focus ring accessibility
- Log line fade+slide animation

### Scanner Concurrency Optimizations ✅

Implemented per `RECOMMENDATIONS.md` Part 1.2:

- **Parallel manifest fetching:** `cryo/scanner/analyze.py::iter_data_files()` uses a thread pool
  to fetch manifest entries concurrently (default 8 workers, configurable via
  `CRYO_SCAN_PARALLEL_MANIFESTS`).
- **Parallel table scanning:** `cryo/scanner/run.py::run_scan()` can scan multiple tables
  concurrently within a chunk (configurable via `CRYO_SCAN_PARALLEL_TABLES` or
  `--parallel-tables` CLI flag).
- **Configuration:** `cryo/config.py` Settings now include `scan_parallel_tables` and
  `scan_parallel_manifests`.

### Native Rust Engine (`cryo-engine`) ⚠️ Scaffold

Located in `cryo-engine/`:

- **`Cargo.toml`** — Dependencies: iceberg 0.4, iceberg-catalog-rest, datafusion 45,
  parquet/arrow 55, object_store, tokio, tracing
- **`src/main.rs`** — Entry point with JSON stdin/stdout protocol
- **`src/operations.rs`** — Four operation implementations using iceberg-rust:
  - `rewrite_data_files` — Compaction/binpacking
  - `expire_snapshots` — Snapshot expiration
  - `remove_orphan_files` — Orphan file cleanup
  - `rewrite_manifests` — Manifest consolidation
- **`src/progress.rs`** — JSONL progress events to stderr
- **`README.md`** — Build instructions and usage

**Status:** Code structure complete, but iceberg-rust's table modification APIs are still
evolving. The implementation demonstrates the integration pattern and will be fully
functional once iceberg-rust stabilizes write operations.

## Partial / In Progress

- Field-level secret encryption for source/destination credentials
- Production-grade signed JWT/OIDC auth (current auth is dev-friendly)
- Full iceberg-rust write operations (waiting for upstream API stabilization)

## Verification Snapshot

Recent checks from implementation sessions:

- Backend unit/API tests: **111 passed**
- Frontend tests: **9 passed** (Vitest)
- Frontend build: passed (TypeScript + Vite)
- Ruff lint/format: passed
- Mypy: passed for `cryo`
- Local scanner: scans all local REST catalog tables
- E2E smoke: `scripts/smoke_cryo_e2e.py` validates event publishing and job store
- Docker E2E: `make smoke-compact` validates full stack (Trino + Spark + Iceberg)

## Known Environment Note

Full `pip install -e ".[dev]"` may backtrack into old `pyiceberg`/`pyarrow` builds on Python 3.12.
Prefer the repository's established `.venv` when it exists. If recreating, verify the resolver picks
a wheel-backed `pyiceberg`/`pyarrow` combination before running tests.
