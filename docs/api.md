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
    "per_replica_cap": 5
  },
  "enabled": true
}
```

## Tables

```http
GET /api/v1/tables?filter=<json>&sort=-small_files_count&limit=100&offset=0
GET /api/v1/tables/{table_id}
GET /api/v1/tables/{table_id}/partitions
GET /api/v1/tables/{table_id}/audit
GET /api/v1/tables/{table_id}/history
GET /api/v1/tables/{table_id}/effective-preset
```

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
    "min_small_files": 10,
    "chunk_size_gb": 100,
    "file_size_mb": 256
  },
  "priority": 100,
  "enabled": true,
  "guardrails": {
    "cooldown_hours": 24
  }
}
```

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

`/maintenance/run` creates durable `runs` and `jobs` rows. It does not yet fully enqueue into a
production long-running governor/worker service path.

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

## Scans

```http
POST /api/v1/scans
```

The scan trigger records an accepted placeholder run and returns `queued=false` until the real scanner
queue path is wired.

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
`error="cancelled"`.

The WebSocket accepts the same viewer role through either an `Authorization: Bearer <token>` header or
`?token=viewer` for browser clients. It currently emits run/job snapshot messages and can fall back to
DB polling until Redis live-state fanout is available.

## Known Gaps

- `POST /api/v1/scans` is an accepted placeholder and is not yet connected to the scanner queue.
- WebSocket run console emits DB snapshots; Redis live-state fanout is still pending.
- Production OIDC/JWT signature verification is not implemented yet.
- API route tests cover the P5 route surface, but deeper DB-backed transaction tests should be expanded
  before production rollout.
