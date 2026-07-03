# Operator Runbook

This runbook covers the current local and backend operations supported by Cryo.

## Check Service Health

API health:

```bash
curl http://localhost:8000/healthz
```

Docker stack:

```bash
docker compose ps
```

Expected local services:

- `iceberg-minio`
- `iceberg-rest`
- `iceberg-trino`
- `iceberg-spark`
- `iceberg-postgres`
- `iceberg-redis`

## Apply Database Migrations

```bash
.venv/bin/alembic upgrade head
```

If the global `alembic` command fails with old SQLAlchemy imports, use the virtualenv command above.

## Seed Local Tables

Demo tables:

```bash
make seed
```

Partition-rich fragmented fixtures:

```bash
make seed-fragmented
```

Inspect file counts:

```bash
make smoke-file-counts
```

Run Spark compaction smoke:

```bash
make smoke-compact
```

## Run A Full Local Scan

```bash
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
```

The scanner logs a `ScanSummary`:

```text
ScanSummary(discovered=13, assigned=13, scanned=13, skipped=0, failed=0)
```

If `failed` is non-zero, check:

- REST catalog URL and availability.
- Source row config in `cryo.sources`.
- MinIO credentials and endpoint.
- Whether the local stack was recreated and needs tables reseeded.

## Query Fleet Health

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

## Preview Maintenance SQL

Use `POST /api/v1/maintenance/preview` with `Authorization: Bearer operator`.

This path builds a plan from supplied table stats and partitions, then renders Trino or Spark SQL.
It does not execute anything.

## Run Maintenance

`POST /api/v1/maintenance/run` creates a run and jobs. The production service loop that admits and
executes those jobs is still in progress, so for now this endpoint is best used to verify durable run
creation and API contracts.

## Destination Test

Use:

```http
POST /api/v1/destinations/{destination_id}/test
Authorization: Bearer operator
```

For Trino destinations, this attempts the live load probe used by the governor. If it fails, check:

- coordinator host/port
- user/auth config
- Trino catalog availability
- whether the local `iceberg-trino` container is healthy

## Troubleshooting

### API returns 401

Add a local bearer token:

```text
Authorization: Bearer viewer
Authorization: Bearer operator
Authorization: Bearer admin
```

### API returns 403

Use a role with enough privileges:

- reads: `viewer`
- assignments, preview/run, cancellation, destination test: `operator`
- source/destination/preset/rule writes: `admin`

### Scanner finds zero tables

Verify:

```bash
docker compose ps
docker exec -it iceberg-trino trino --execute "SHOW SCHEMAS FROM iceberg"
```

Then verify the `local-rest` source row points to `http://localhost:8181`.

### Spark fixture commands are slow

The first run downloads Iceberg, Hadoop AWS, and AWS SDK jars. Later runs are faster if the Docker
layer/cache is preserved.

### Full dev install fails on PyArrow

If `pip install -e ".[dev]"` backtracks into an old `pyiceberg`/`pyarrow` source build, install
plain `pyiceberg` plus the runtime/test packages as documented in `local-development.md`.
