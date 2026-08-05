# API Guide

The FastAPI application is created by `cryo.api.app:create_app` and registers routes under
`/api/v1`.

## Authentication

Most API routes require a local bearer role token.

| Role | Intended Access |
|------|-----------------|
| `viewer` | Read-only routes such as tables, presets, sources, runs, and jobs. |
| `operator` | Viewer access plus assignment, simulation, maintenance preview/run, cancellation, and destination tests. |
| `admin` | Operator access plus source, destination, preset, and rule management. |

Local examples:

```bash
curl -H "Authorization: Bearer viewer" http://localhost:8000/api/v1/tables
curl -H "Authorization: Bearer operator" http://localhost:8000/api/v1/presets/<id>/simulate
curl -H "Authorization: Bearer admin" http://localhost:8000/api/v1/sources
```

The auth layer also accepts unsigned JWT-shaped local development tokens by decoding the payload for
`role`, `roles`, `scope`, or `scopes`. This is a development scaffold, not production OIDC.

## Health

```http
GET /healthz
```

Returns:

```json
{"status": "ok", "environment": "dev"}
```

## Capability contract

```http
GET /api/v1/capabilities
```

Viewer access is sufficient. The response is the versioned, exhaustive catalog
and engine-operation contract used by runtime native routing, generated
documentation, and the Connections capability ledger. Each operation cell
includes support status, maturity, execution mode, runtime conditions,
limitations, compatible fallbacks, owner, and exact code/test evidence.

The generated human-readable form is
[`CAPABILITIES.md`](./CAPABILITIES.md). A configured Destination must not be
interpreted as support for every operation.

## Sources

```http
GET    /api/v1/sources
GET    /api/v1/sources/{source_id}
POST   /api/v1/sources
PATCH  /api/v1/sources/{source_id}
DELETE /api/v1/sources/{source_id}
```

`DELETE` currently disables the source by setting `enabled=false`.

`maintenance_schema_names` is an optional allowlist of Iceberg namespaces (schemas) Cryo may scan
and schedule for maintenance. An empty list means every namespace in the catalog is in scope. The
Connections UI populates this from the `POST /sources/test` namespace list.

Example source payload:

```json
{
  "name": "local-rest",
  "type": "rest",
  "config": {
    "catalog_name": "rest",
    "type": "rest",
    "uri": "http://localhost:8181",
    "warehouse": "s3://warehouse/"
  },
  "maintenance_schema_names": ["analytics", "cdp"],
  "enabled": true
}
```

## Destinations

```http
GET    /api/v1/destinations
GET    /api/v1/destinations/{destination_id}
POST   /api/v1/destinations
PATCH  /api/v1/destinations/{destination_id}
DELETE /api/v1/destinations/{destination_id}
POST   /api/v1/destinations/{destination_id}/test
```

`DELETE` currently disables the destination by setting `enabled=false`.

For Trino destinations, `/test` attempts a live capacity probe through `system.runtime.queries`.
Other engine types currently return configuration presence/enablement.

Example Trino destination payload:

```json
{
  "name": "local-trino",
  "engine": "trino",
  "config": {
    "host": "localhost",
    "port": 8080,
    "user": "cryo",
    "catalog": "iceberg",
    "catalog_bindings": [
      {
        "source_id": "00000000-0000-0000-0000-000000000001",
        "trino_catalog": "iceberg"
      }
    ],
    "per_replica_cap": 5
  },
  "enabled": true
}
```

`config.catalog` is the Trino **session default** (connection probes). `catalog_bindings` lists
Cryo lakehouses (sources) registered on this Trino. Optional `trino_catalog` overrides the SQL
catalog name when it differs from the source's `catalog_name`. Empty bindings mean no tables can
use Trino presets that target this destination. `catalog_bindings` is Trino-only.

## Tables

```http
GET /api/v1/tables?filter=<json>&sort=-small_files_count&limit=100&offset=0
GET /api/v1/tables/{table_id}
GET /api/v1/tables/{table_id}/partitions
GET /api/v1/tables/{table_id}/audit
GET /api/v1/tables/{table_id}/history
GET /api/v1/tables/{table_id}/effective-preset
GET /api/v1/tables/{table_id}/compatible-presets
```

`compatible-presets` returns presets whose destination can maintain this table's catalog.
Non-Trino destinations are always included. Trino destinations are included only when the
table's `source_id` appears in that destination's `config.catalog_bindings`.

Supported sort fields:

- `schema_name`
- `table_name`
- `total_size_gb`
- `small_files_count`
- `total_files`
- `grade`
- `overall_score`
- `compaction_priority`

Filter predicates use the shared grammar:

```json
{
  "all": [
    {"field": "schema_name", "op": "glob", "value": "fixtures*"},
    {"field": "small_files_count", "op": ">", "value": 100},
    {"not": {"field": "grade", "op": "=", "value": "A"}}
  ]
}
```

URL-encode the JSON when passing it as the `filter` query parameter.

`assigned_preset_id` filters by **explicit table assignment** (`cryo.table_assignments`),
not the effective preset (pinned assignment else matching rule). Examples:

```json
{"field": "assigned_preset_id", "op": "=", "value": null}
```

```json
{"field": "assigned_preset_id", "op": "in", "value": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]}
```

Combine with `any` to match unassigned tables or specific presets. Rule-matched but
unassigned tables appear as `null` (no assignment row).

The drilldown endpoints return bounded UI-ready envelopes:

- `/partitions`: latest partition rows, optionally scoped by `scan_at`, with `limit`/`offset`.
- `/audit`: recent maintenance actions with status, engine, detail, and duration.
- `/history`: latest table health points for trend charts.

## Dashboards

```http
GET /api/v1/dashboards/datalake
GET /api/v1/dashboards/schema-cost
```

`/dashboards/datalake` returns datalake totals, grade distribution, top offender tables, and scan drift.
`/dashboards/schema-cost` returns per-schema size, estimated S3 cost, small-file count, and query count.

## Presets

```http
GET    /api/v1/presets
GET    /api/v1/presets/{preset_id}
POST   /api/v1/presets
PATCH  /api/v1/presets/{preset_id}
DELETE /api/v1/presets/{preset_id}
POST   /api/v1/presets/{preset_id}/simulate
```

`DELETE` currently disables the preset by setting `enabled=false`.

Example preset:

```json
{
  "name": "trino-standard",
  "destination_id": "00000000-0000-0000-0000-000000000000",
  "mode": "standard",
  "operations": ["expire_snapshots", "optimize"],
  "params": {
    "min_small_files": 128,
    "max_partitions_per_chunk": 100,
    "delete_ratio_trigger": 0,
    "retention_days": 7,
    "retain_last": 1,
    "file_size_mb": 256,
    "compute_statistics": false
  },
  "priority": 100,
  "enabled": true,
  "guardrails": {
    "cooldown_hours": 24,
    "sla_freshness_hours": 24,
    "sla_maintenance_days": 7,
    "sla_p95_latency_ms": 5000,
    "adaptive_routing": false,
    "engine_preferences": {
      "spark_min_gb": 100,
      "native_min_gb": 50,
      "latency_p99_ms": 500
    }
  }
}
```

Supported `params` keys validated by the API include planner tuning (`min_small_files`,
`max_partitions_per_chunk`, `max_chunks`, `delete_ratio_trigger`,
`partition_write_idle_hours`, retention fields, `file_size_mb`) and statistics options
(`compute_statistics`, `stats_columns`, `bloom_filter_columns`, `stats_compute_ndv`).
`chunk_size_gb` is not a preset knob (native derives it from pod memory at admit time;
if present it is still validated as a positive number for back-compat). Extra keys are
accepted for forward compatibility.

Supported `guardrails` keys include admission and SLA settings (`cooldown_hours`,
`deadline_minutes`, `sla_*`), adaptive routing (`adaptive_routing`, `engine_pool`,
`engine_preferences`), and `routing_rules` predicates. Extra keys are accepted for forward
compatibility.

The web UI exposes these fields as described form controls instead of raw JSON editors.

Simulation returns matched table count, estimated total size, small-file count, and sample tables.

## Preset Rules

```http
GET    /api/v1/presets/{preset_id}/rules
POST   /api/v1/presets/{preset_id}/rules
DELETE /api/v1/presets/{preset_id}/rules/{rule_id}
```

Rule payload:

```json
{
  "predicate": {
    "all": [
      {"field": "total_size_gb", "op": "<=", "value": 100},
      {"field": "small_files_count", "op": ">", "value": 10}
    ]
  },
  "enabled": true
}
```

## Assignments

```http
POST   /api/v1/assignments
DELETE /api/v1/assignments
```

`POST` returns **400** when the preset's destination is Trino and any selected table's source is
not listed in that destination's `catalog_bindings`.

Assignment by ids:

```json
{
  "selection": {
    "mode": "ids",
    "ids": ["00000000-0000-0000-0000-000000000000"]
  },
  "preset_id": "11111111-1111-1111-1111-111111111111",
  "assigned_by": "local-user"
}
```

Assignment by filter:

```json
{
  "selection": {
    "mode": "filter",
    "filter": {"field": "grade", "op": "in", "value": ["C", "D", "F"]}
  },
  "preset_id": "11111111-1111-1111-1111-111111111111"
}
```

Unassignment uses the same selection envelope without `preset_id`:

```json
{
  "selection": {
    "mode": "filter",
    "filter": {"field": "schema_name", "op": "=", "value": "fixtures"}
  }
}
```

## Maintenance

```http
POST /api/v1/maintenance/preview
POST /api/v1/maintenance/run
```

`/maintenance/preview` is stateless and renders Trino or Spark SQL from supplied table stats and
partitions. It does not execute statements.

`/maintenance/run` creates durable `runs` and `jobs` rows and enqueues immediately unless
`scheduled_at` defers dispatch to the Scheduler.

Run by ids:

```json
{
  "table_ids": ["00000000-0000-0000-0000-000000000000"],
  "destination_id": "11111111-1111-1111-1111-111111111111",
  "mode": "standard",
  "trigger": "api"
}
```

Run by filter selection:

```json
{
  "selection": {
    "mode": "filter",
    "filter": {"field": "grade", "op": "in", "value": ["C", "D", "F"]}
  },
  "destination_id": "11111111-1111-1111-1111-111111111111",
  "mode": "standard",
  "trigger": "web"
}
```

### Maintenance safety and control

```http
GET  /api/v1/maintenance/control
POST /api/v1/maintenance/control/pause
POST /api/v1/maintenance/control/resume

GET  /api/v1/maintenance/safety/summary
GET  /api/v1/maintenance/safety/intents
GET  /api/v1/maintenance/safety/effects
POST /api/v1/maintenance/safety/effects/{receipt_id}/resolve
```

Control state is generation-stamped and scoped to `global`, `tenant`, or
`destination`. Viewers can inspect visible controls; operators can control their
tenant/destinations; global pause/resume requires admin.

Safety reads are tenant-scoped. `effects` accepts `state`, `ambiguous_only`, and a
bounded `limit`. Resolving an ambiguous effect requires an operator outcome
(`succeeded` or `failed`), reason, and optional immutable engine evidence
reference. Resolution is audited and returns an eligible job to normal Governor
admission rather than dispatching it directly.

## Scans

```http
POST /api/v1/scans
```

The scan trigger creates a scan run and enqueues bounded scanner chunks when Redis
is available. Queue failures are returned explicitly instead of being reported as
successful dispatch.

## Runs And Jobs

```http
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/jobs
POST /api/v1/runs/{run_id}/cancel
GET  /api/v1/jobs/{job_id}
WS   /api/v1/ws/runs/{run_id}
```

Cancellation marks the run `cancelled` and queued/admitted/running jobs as skipped with
`error="cancelled"`. It also releases Redis queue / in-flight slots and per-table locks for
those jobs. Re-cancelling an already-cancelled run is idempotent and re-attempts the same
Redis cleanup (useful if a prior cancel left a lock behind).

The WebSocket requires viewer access through either an authorization header or
the browser `?token=` parameter. It emits run/job snapshots and Redis
live-state/log events with DB polling fallback.

## Known Gaps

- Durable maintenance intents are feature-gated off by default until production
  rollout and recovery evidence passes.
- Production OIDC/JWT signature verification is not implemented yet.
- API route tests cover the P5 route surface, but deeper DB-backed transaction tests should be expanded
  before production rollout.
