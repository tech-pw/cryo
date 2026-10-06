# Operator Guide

> Last reconciled: 2026-07-05.
> **Audience:** on-call engineers running Cryo in production. For local development setup,
> jump to §3.

---

## Part 1 — Local dev stack (host + Docker)

### Prerequisites

- Python 3.12.
- Docker and Docker Compose.
- Java 17+ for Spark-based fixture scripts (or use `make seed-hms-fixtures-docker`).
- A Python virtual environment with project dependencies installed.

Recommended setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

If the resolver backtracks into old `pyiceberg`/`pyarrow` builds, install the core runtime/test
packages directly and then install plain `pyiceberg` with Hive extras:

```bash
.venv/bin/python -m pip install pytest ruff mypy fastapi httpx sqlalchemy alembic \
  "psycopg[binary]" pydantic-settings redis trino boto3 uvicorn
.venv/bin/python -m pip install "pyiceberg[hive,s3fs,pyarrow]"
```

### Compose layout — three files

Cryo uses **three separate Compose files** so production deployments never pull in local-only
test infrastructure.

| File | Purpose | Deploy to prod? |
|------|---------|-----------------|
| `docker-compose.yml` | Cryo platform: Postgres, Redis, API, worker, governor, scheduler, scanner, web | Yes (shape matches Helm; use managed PG/Redis in real prod) |
| `docker-compose.dev-datalake.yml` | **LOCAL/CI ONLY** — MinIO, Iceberg REST, HMS, Trino, Spark | **No** |
| `docker-compose.observability.yml` | Optional Prometheus, Grafana, Loki, Promtail | No (not in Helm v1) |

All files share Compose project name `cryo` and network `cryo-dev` so services can reach each
other when started together.

### Common workflows

```bash
# Full local dev (recommended): datalake + Cryo deps + observability
make up

# Cryo only (against real external catalogs/engines)
make up-cryo

# Dev datalake only (MinIO + catalogs + Trino)
make up-datalake

# Tear down
make down
# or, to also remove volumes:
docker compose -f docker-compose.yml -f docker-compose.dev-datalake.yml -f docker-compose.observability.yml down -v
```

### Service URLs (local)

| Service | URL |
|---------|-----|
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 (minioadmin / minioadmin) |
| Iceberg REST catalog | http://localhost:8181 |
| Hive Metastore (HMS) | thrift://localhost:9083 (separate metadata store from REST) |
| Trino | http://localhost:8080 (`iceberg` = REST, `iceberg_hms` = HMS) |
| Postgres | localhost:5432 (`cryo` / `cryo` / `cryo`) |
| HMS Postgres | internal service `hms-postgres` (Hive metastore DB only) |
| Redis | localhost:6379 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |
| Cryo API | http://localhost:8000 |
| Cryo Web | http://localhost:5173 |

### Check service health

```bash
curl http://localhost:8000/healthz
make ps  # container status
```

Expected local services (container names prefixed `cryo-`):

- `cryo-minio`
- `cryo-iceberg-rest`
- `cryo-trino`
- `cryo-spark` (tools profile)
- `cryo-postgres`
- `cryo-redis`
- `cryo-prometheus`, `cryo-grafana`, `cryo-loki` (when observability stack is running)

### Apply database migrations

```bash
.venv/bin/alembic upgrade head
```

If the global `alembic` command fails with old SQLAlchemy imports, use the virtualenv command
above.

### Run the scanner

```bash
# All enabled sources
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan

# Opt-in orphan byte detection (Tier 2 storage LIST)
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan --cost-full-scan

# A specific source
.venv/bin/python -m cryo.scanner.run \
  --source-id <source_uuid> \
  --num-chunks 1 \
  --full-scan
```

By default, scans run Tier 0+1 (metadata-precise expiry/compaction/delete reclaim with no S3
LIST). Tier 2 runs when `--cost-full-scan` is passed or when `cost_full_scan_every_hours` cadence
elapses since the last full cost scan for a table. Scanner logs a `ScanSummary`:

```text
ScanSummary(discovered=13, assigned=13, scanned=13, skipped=0, failed=0)
```

### Seed fixture tables

```bash
make seed                      # demo tables
make seed-fragmented           # partition-rich fragmented fixtures
make seed-config               # sources, destinations, default presets
make smoke-file-counts         # inspect file counts
make smoke-compact             # Spark compaction smoke
```

The fragmented fixture set covers: unpartitioned, day/month/year/bucket/multi-column
partitioning, timestamp/date partitioning, and nested/object-like schemas.

### HMS fixture tables

HMS fixture generation lives under `tests/integration/hms/` and writes data only to MinIO.

```bash
make seed-hms-fixtures                          # small smoke fixtures
HMS_FIXTURE_SCALE=full make seed-hms-fixtures  # 1M–10M rows per table (long-running)
HMS_FIXTURE_SCALE=full make seed-hms-fixtures-docker  # no host Java/PySpark required
```

The generator creates 10 tables in namespace `hms_issues` with intentional maintenance issues:
skewed day partitions, sparse many-partition tables, fragmented small files, nested payloads,
and an oversized single-partition table.

Verify Trino can read HMS tables:

```bash
docker compose -f docker-compose.dev-datalake.yml exec trino trino --execute "SHOW TABLES FROM iceberg_hms.hms_issues"
python tests/integration/hms/smoke_hms_trino.py
```

Run opt-in HMS integration tests:

```bash
make test-hms-integration
# or explicitly:
CRYO_HMS_URI=thrift://localhost:9083 pytest tests/integration/hms/test_hms_catalog_integration.py -v
```

### Load testing

```bash
make load-test-data-docker ARGS="--preset unpartitioned --snapshots 2 --rows-per-snapshot 10000"
```

Each `--snapshots` increment adds a new Iceberg snapshot; `--rows-per-snapshot` and
`--files-per-snapshot` control volume and small-file fragmentation for Cryo maintenance testing.
REST example — 5 snapshots × 1M rows (5M total):

```bash
LOAD_SNAPSHOTS=5 LOAD_ROWS_PER_SNAPSHOT=1000000 \
  make load-test-data-docker ARGS="--preset unpartitioned"
```

HMS example — append to a day-partitioned table:

```bash
LOAD_CATALOG=hms LOAD_SNAPSHOTS=10 LOAD_ROWS_PER_SNAPSHOT=2000000 \
  make load-test-data-docker ARGS="--preset day --table day_stress"
```

Presets: `unpartitioned`, `day`, `month`, `multi`, `nested`. Default namespaces: `load_test`
(REST) and `hms_load_test` (HMS). Useful env vars: `LOAD_CATALOG`, `LOAD_PRESET`, `LOAD_NAMESPACE`,
`LOAD_TABLE`, `LOAD_MODE`, `LOAD_SNAPSHOTS`, `LOAD_ROWS_PER_SNAPSHOT`,
`LOAD_FILES_PER_SNAPSHOT`. Pass `--dry-run` to print the plan without writing.

Drop and recreate (destructive — requires `--force`):

```bash
make load-test-data-docker ARGS="--preset unpartitioned --drop-existing --force --snapshots 2 --rows-per-snapshot 10000"
```

Loading data does not trigger Cryo automatically. After appending:

```bash
make seed-config
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
```

Use `--full-scan` so tables scanned earlier the same day are rescanned. Then verify stats in
the UI or via the datalake API (`needs_compaction`, `small_files_count`, snapshot count).

### Query local health

```bash
.venv/bin/python - <<'PY'
from collections import Counter
from sqlalchemy import select

from cryo.db.models import Table, TableStats
from cryo.db.session import SessionLocal

with SessionLocal() as session:
    rows = session.execute(
        select(
            Table.schema_name,
            Table.table_name,
            TableStats.grade,
            TableStats.overall_score,
            TableStats.needs_compaction,
            TableStats.compaction_priority,
            TableStats.total_files,
            TableStats.small_files_count,
            TableStats.total_partitions,
            TableStats.partition_col,
        )
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .order_by(Table.schema_name, Table.table_name)
    ).all()
    print("grade_distribution", dict(Counter(row.grade for row in rows)))
    for row in rows:
        print(row)
PY
```

### Configure a local source manually

If `make seed-config` doesn't fit your environment, create the source row directly:

```bash
.venv/bin/python - <<'PY'
from sqlalchemy import select
from cryo.db.models import Source
from cryo.db.session import SessionLocal

config = {
    "catalog_name": "rest",
    "type": "rest",
    "uri": "http://localhost:8181",
    "warehouse": "s3://warehouse/",
    "s3.endpoint": "http://localhost:9000",
    "s3.access-key-id": "minioadmin",
    "s3.secret-access-key": "minioadmin",
    "s3.path-style-access": "true",
}

with SessionLocal() as session:
    source = session.scalar(select(Source).where(Source.name == "local-rest"))
    if source is None:
        source = Source(name="local-rest", type="rest", config=config, enabled=True)
        session.add(source)
    else:
        source.type = "rest"
        source.config = config
        source.enabled = True
    session.commit()
    print(source.id)
PY
```

### Run the API

```bash
.venv/bin/uvicorn cryo.api.app:create_app --factory --reload
```

Use one of these local auth headers:

```bash
Authorization: Bearer viewer
Authorization: Bearer operator
Authorization: Bearer admin
```

### Preview / run maintenance

- **Preview** (no execution): `POST /api/v1/maintenance/preview` with `Authorization: Bearer operator`.
  Builds a plan from supplied table stats, then renders Trino or Spark SQL.
- **Run**: `POST /api/v1/maintenance/run` with `Authorization: Bearer operator` creates a run and
  jobs. The production service loop that admits and executes those jobs is still in progress,
  so for now this endpoint is best used to verify durable run creation and API contracts.

### Destination test

```http
POST /api/v1/destinations/{destination_id}/test
Authorization: Bearer operator
```

For Trino destinations, this attempts the live load probe used by the governor. If it fails,
check: coordinator host/port, user/auth config, Trino catalog availability, and whether the local
`iceberg-trino` container is healthy.

For Trino compaction jobs, configure the Iceberg writer target in the Trino catalog properties:
`iceberg.target-max-file-size=256MB`. The local Trino deployment does not expose
`<catalog>.target_max_file_size` as a session property, so Cryo does not emit that `SET SESSION`
statement. `optimize(file_size_threshold => '256MB')` still controls which files are eligible for
rewrite; the catalog property controls the target size of newly written files.

### Quality commands

```bash
.venv/bin/ruff check cryo tests
.venv/bin/ruff format --check cryo tests
.venv/bin/mypy cryo
.venv/bin/python -m pytest
```

Run focused tests first while developing, then the full suite before handing off.

### Common local-dev troubleshooting

- **API returns 401** — Add `Authorization: Bearer viewer/operator/admin`.
- **API returns 403** — Use a role with enough privilege:
  - reads: `viewer`
  - assignments, preview/run, cancellation, destination test: `operator`
  - source/destination/preset/rule writes: `admin`
- **Scanner finds zero tables** — Verify `make ps`, then
  `docker exec -it cryo-trino trino --execute "SHOW SCHEMAS FROM iceberg"`. Verify the `local-rest`
  source row points to `http://localhost:8181`.
- **Spark fixture commands are slow** — First run downloads Iceberg, Hadoop AWS, and AWS SDK jars.
- **Full dev install fails on PyArrow** — Install plain `pyiceberg` plus runtime/test packages as
  shown above.

---

## Part 2 — Production on-call

### Quick health checks

```bash
# API
curl -sf http://<api-host>/healthz

# Prometheus metrics
curl -sf http://<api-host>/metrics | grep -E '^cryo_(governor|worker|scanner|queue)'

# Grafana dashboard: Cryo Overview (uid: cryo-overview)
# Panels: admission decisions, queue depth, worker duration, scanner throughput, cost estimates
```

Log exploration (Loki via Grafana Explore):

```logql
{service=~"cryo-.*"} |= "ERROR"
```

### Governor stuck / not admitting jobs

**Symptoms:**
- Maintenance jobs remain `queued` for extended periods
- `cryo_governor_queue_depth` rising in Grafana
- `cryo_governor_admission_decisions_total{outcome="deferred"}` increasing
- No new `cryo_worker_job_executions_total`

**Diagnosis:**
1. Check Governor logs: `{service="cryo-governor"} |= "wave"`
2. Engine load — Governor defers when Trino/Spark `system.runtime.queries` exceeds caps.
   Query Trino: `SELECT state, count(*) FROM system.runtime.queries GROUP BY 1`
3. Redis leader lock — multiple Governor pods; only the leader admits:
   `redis-cli GET cryo:leader:governor`
4. Off-peak window — admissions may defer heavy (standard/deep) jobs outside a configured IST
   window. If the engine has no off-peak set, or start and end are both `0` (or equal), there is
   no gate and maintenance may run anytime.
5. Per-replica caps — `default_per_replica_optimize_cap` may be saturated.

**Remediation:**

| Cause | Action |
|-------|--------|
| Engine overloaded | Wait for off-peak or reduce external query load; temporarily lower worker replicas |
| Stale leader lock | Restart Governor pod; verify Redis connectivity |
| Misconfigured destination | Check destination `enabled=true` and probe credentials |
| Orphaned in-flight jobs | Governor reconciles on wave; inspect `audit_logs` for stuck `running` jobs |
| Emergency bypass | Use ad-hoc maintenance with `force=true` (audited) for a single table only |

**Escalation data to collect:**
- Governor wave log excerpt (last 3 waves)
- `cryo_governor_admission_decisions_total` by outcome (24h)
- Destination engine query count snapshot
- Redis queue depth: `cryo_queue_pending_jobs`

### Scanner failures

**Symptoms:**
- Tables show stale `scan_at` timestamps
- `cryo_scanner_errors_total` increasing
- `cryo_scanner_tables_scanned_total` flatlined
- UI scanner status shows failed runs

**Diagnosis:**
1. Scanner run logs: `{service="cryo-scanner"} |= "ScanSummary" or |= "ERROR"`
2. Catalog connectivity — PyIceberg REST / Glue / Hive reachability from scanner pod
3. Source credentials — `secret_ref` resolution failures appear as auth errors
4. Chunk parallelism — OOM on large catalogs; check `scan_parallel_tables` / memory limits
5. Manual trigger:
   ```bash
   curl -X POST http://<api>/api/v1/scans \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"source_id":"<uuid>","full_scan":true}'
   ```

**Remediation:**

| Cause | Action |
|-------|--------|
| Catalog timeout | Retry scan; increase scanner memory; reduce `num_chunks` temporarily |
| Auth failure | Rotate credentials in secret store; verify IRSA/SA role |
| Single table error | Scanner isolates per-table failures; inspect `audit_logs` / table detail |
| Scheduler disabled | PATCH `/api/v1/scanner/config` → `enabled: true` |
| Full cost scan overload | Disable `cost_full_scan` until off-peak |

### Unmatched tables (no effective preset)

**Symptoms:**
- `CryoUnmatchedNeedsCompactionTablesHigh` alert fires
- `cryo_scheduler_unmatched_tables{needs_compaction="true"}` reads high in Grafana

**What this means:** these tables resolve to no effective preset via
`cryo.scheduler.rules.effective_preset()` — no pinned assignment, no matching rule, and
either no fleet-wide catchall preset is enabled or its rule/priority doesn't reach them. They
get zero recurring maintenance, silently, until someone notices. A disabled preset once
left a large table unmatched until someone looked by hand.

**Diagnosis:**
1. `GET /api/v1/presets` — confirm the intended fleet-wide catchall (`default-native-deep` or
   equivalent) is `enabled: true` and has a `schedule_cron`.
2. `GET /api/v1/tables?filter=needs_compaction=true` cross-referenced against
   `POST /api/v1/presets/{id}/simulate` for each enabled preset — find which unmatched tables
   should be covered and by which preset.
3. Check for a recently disabled preset (`PATCH .../presets/{id}` audit trail) or a rule
   priority regression that stopped matching a previously-covered cohort.

**Remediation:**

| Cause | Action |
|-------|--------|
| Fleet-wide catchall disabled | Re-enable it, or confirm the narrower preset set is the deliberate replacement |
| Rule priority/predicate regression | Fix the rule via `POST /api/v1/presets/{id}/rules`; re-run simulate to confirm `matched_count` |
| Genuinely out-of-scope tables (temp/scratch/test) | Confirm via `cryo.scheduler.rules.is_ephemeral_maintenance_table` — these should stay unmatched, not force-matched |

### API memory / OOM (cryo-api)

**Symptoms:**
- `cryo-api` pods OOMKilled despite light request volume
- RSS climbs to multi-GiB within minutes of UI use; Helm limit historically raised to 6Gi as a bandage
- Readiness/liveness timeouts while a dashboard request is in flight

**Root cause (fixed in code):** fleet-wide ORM loads that pulled every active `TableStats` /
`TableCost` row — including large `raw` JSONB blobs — into Python:

- `GET /api/v1/dashboards/datalake` and `GET /api/v1/dashboards/cost` (now SQL aggregates)
- Earlier: `GET /api/v1/smart-maintenance/overview` (already SQL-aggregated)
- Long-lived `WS /api/v1/ws/runs/{id}` without `session.expunge_all()` (identity-map leak; fixed)

With ~13k tables these paths must never `select(Table, TableStats, TableCost)` for the whole
catalog. Default API memory limit is 1Gi after the SQL rewrite.

**Diagnosis:**
1. `kubectl top pod -n cryo -l app.kubernetes.io/component=api`
2. Access logs around the spike — look for `/dashboards/datalake` or long-lived run websockets
3. `kubectl describe pod` → `Last State: OOMKilled`

### Worker memory / OOM (cryo-worker)

**Symptoms:** many `cryo-worker` pods OOMKilled in the same window; steady-state RSS
~150–220Mi against a 4–6Gi limit.

**Do not treat a Helm memory bump as the fix.** `values-prod.yaml` was raised 4Gi→6Gi
in 1253fe3 as a margin only. Spark-on-K8s already runs in a Job; the worker blow-up is
holding a huge partition-aware plan / `table_data.partitions` while submitting SQL.

**Code path:** outlier tables (≥500GB, ≥10k files, ≥2k partitions, or a
plan JSON ≥256KiB) drop partition filters and run a full-table Spark CALL. Logs:
`spark-k8s payload worker:pre_execute` / `execute_plan:before` with `plan_json_bytes`,
partition/predicate counts, and RSS.

### Approval workflows

Cryo can require human approval before destructive or high-impact maintenance.

**Symptoms:**
- Jobs stuck in `pending_approval`
- `cryo_scheduler_approval_requests_total{status="pending"}` rising
- Slack/email approval links not actioned

**Workflow:**
1. List pending approvals: `GET /api/v1/advanced/approvals?status=pending`
2. Review `impact_preview` payload (estimated bytes, operations, tables affected)
3. Approve or reject:
   - `POST /api/v1/advanced/approvals/{id}/approve`
   - `POST /api/v1/advanced/approvals/{id}/reject`
4. Approved jobs enqueue automatically; rejected jobs audit with reason

**SLA guidance:**
- **Standard optimize** — approve within 4 business hours
- **expire_snapshots / remove_orphan_files** — require explicit operator sign-off; verify retention policy
- **Expired approvals** — re-trigger maintenance; expired requests increment `status="expired"`

### Metrics reference

| Metric | Type | Meaning |
|--------|------|---------|
| `cryo_governor_admission_decisions_total` | Counter | admitted / deferred / blocked / orphaned |
| `cryo_governor_queue_depth` | Gauge | Jobs waiting for admission |
| `cryo_queue_pending_jobs` | Gauge | Redis queue depth by destination |
| `cryo_worker_job_duration_seconds` | Histogram | End-to-end job execution time |
| `cryo_worker_job_executions_total` | Counter | Jobs by status (ok/error/timeout) |
| `cryo_scanner_tables_scanned_total` | Counter | Tables scanned per source |
| `cryo_scanner_errors_total` | Counter | Scanner failures by error type |
| `cryo_cost_monthly_estimate_usd` | Gauge | Estimated monthly cost by source/type |
| `cryo_cost_savings_projected_usd` | Gauge | Projected savings from maintenance |
| `cryo_anomaly_alerts_total` | Counter | Dispatched anomaly alerts |

**Useful PromQL:**

```promql
# Admission defer rate
sum(rate(cryo_governor_admission_decisions_total{outcome="deferred"}[15m]))

# Worker p95 latency
histogram_quantile(0.95, sum by (le) (rate(cryo_worker_job_duration_seconds_bucket[15m])))

# Scanner error rate
sum(rate(cryo_scanner_errors_total[1h]))
```

### Anomaly alerts

When `detect_all_anomalies(..., dispatch_alerts=True)` runs (scheduled job or API with dispatch
enabled):

- **Slack** — all severities ≥ configured minimum via `CRYO_SLACK_WEBHOOK_URL`
- **PagerDuty** — high-severity only via `CRYO_PAGERDUTY_ROUTING_KEY`

Verify dispatch:

```bash
curl "http://<api>/api/v1/advanced/anomalies?lookback_days=30&threshold_sigma=3.0"
```

Check `cryo_anomaly_alerts_total` after dispatch.

### Shared S3 warehouse-path anomalies

Distinct from the ML detector above. The **Anomalies** page (`/anomalies`) lists catalog
tables whose warehouse locations are the same path or nested under another table — the
failure mode of two tables sharing one warehouse path.

- Harvest uses Hive Metastore Thrift `sd.location` and Glue `StorageDescriptor.Location`
  (not Iceberg `load_table`), so unreadable aliases still appear.
- Slack fires at **10:00 IST** only when overlapping groups exist (`ReportScheduler`).
- Manual: `make shared-s3-anomalies` / `POST /api/v1/anomalies/shared-s3/refresh`.

See [`docs/shared-s3-anomalies.md`](./shared-s3-anomalies.md).

### Rate limiting (API)

Expensive endpoints are token-bucket limited per client IP:

- `POST /api/v1/maintenance/preview`
- `POST /api/v1/scans`
- `GET /api/v1/advanced/anomalies`
- `POST /api/v1/routing/preview`
- `POST /api/v1/tables/{id}/clustering-plan`
- `POST /api/v1/anomalies/shared-s3/refresh`

HTTP 429 responses include `Retry-After: 2`. Tune via `CRYO_API_RATE_LIMIT_RPS` and
`CRYO_API_RATE_LIMIT_BURST`.

### Multi-tenant isolation

After migration `20260705_0004_multi_tenant`:

1. Create tenant rows in `cryo.tenants`
2. Backfill `tenant_id` on `sources`, `destinations`, `tables`, `presets`
3. Ensure JWT carries `tenant_id`; API sessions use `create_tenant_aware_session`
4. Cross-tenant access returns 404 via `verify_tenant_access`

---

## Part 3 — Production deployment

Production deploys **Cryo application pods only** via `deploy/helm/cryo/`. Do not deploy
`docker-compose.dev-datalake.yml` or `docker-compose.observability.yml` to production servers.
External object storage, catalogs, and query engines are configured as Cryo sources and
destinations in the database.

Helm install uses `deploy/helm/cryo/`. Set `iceberg.target-max-file-size` on the catalog,
not in Cryo. Multi-tenancy is per source and destination in the database.

Full inventory of local-only components (update when adding new ad-hoc dev tooling):
[`local-dev-only-inventory.md`](./local-dev-only-inventory.md).