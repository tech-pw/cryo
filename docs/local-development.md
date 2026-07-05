# Local Development Guide

This guide describes the local development workflow for the current Cryo backend.

## Prerequisites

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

## Start The Local Stack

```bash
./start-local.sh start
# or
make up
make ps
.venv/bin/alembic upgrade head
```

Compose files are split — see [`local-docker-compose.md`](./local-docker-compose.md). `make up` and
`./start-local.sh` start the dev datalake, Cryo dependencies, and observability stack.

Services:

- MinIO: `http://localhost:9000` and console `http://localhost:9001`.
- Iceberg REST catalog: `http://localhost:8181`.
- Hive Metastore (HMS): `thrift://localhost:9083` (separate metadata store from REST).
- Trino: `http://localhost:8080` (`iceberg` = REST, `iceberg_hms` = HMS).
- Spark runner: container service `spark` (Compose profile `tools`).
- Postgres: `localhost:5432`, database/user/password `cryo`.
- HMS Postgres: internal service `hms-postgres` (Hive metastore DB only).
- Redis: `localhost:6379`.

`./start-local.sh` starts HMS services by default. REST and HMS share the MinIO bucket
`s3://warehouse/`, but table metadata is not shared between catalogs.

## HMS Fixture Tables

HMS fixture generation lives under `tests/integration/hms/` and writes data only to MinIO.

Quick smoke fixtures (small row counts):

```bash
make seed-hms-fixtures
```

Full-scale fixtures (1M–10M rows per table; long-running):

```bash
HMS_FIXTURE_SCALE=full make seed-hms-fixtures
```

Docker-only Spark runner (no host Java/PySpark required):

```bash
HMS_FIXTURE_SCALE=full make seed-hms-fixtures-docker
```

The generator creates 10 tables in namespace `hms_issues` with intentional maintenance issues:
skewed day partitions, sparse many-partition tables, fragmented small files, nested payloads,
and an oversized single-partition table.

Verify Trino can read HMS tables:

```bash
docker compose -f docker-compose.dev-datalake.yml exec trino trino --execute "SHOW TABLES FROM iceberg_hms.hms_issues"
python tests/integration/hms/smoke_hms_trino.py
```

Run opt-in HMS integration tests (requires Docker HMS + fixtures):

```bash
make test-hms-integration
```

Or explicitly:

```bash
CRYO_HMS_URI=thrift://localhost:9083 pytest tests/integration/hms/test_hms_catalog_integration.py -v
```

Cleanup HMS/MinIO state (removes generated data from Docker volumes, not git):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev-datalake.yml -f docker-compose.observability.yml down -v
```

## Seed Fixture Tables

The fragmented fixture set covers:

- unpartitioned
- day transform
- month transform
- year transform
- bucket transform
- multi-column partitioning
- timestamp/date partitioning
- nested/object-like schemas

Run:

```bash
make seed-fragmented
make smoke-file-counts
```

To compact fixtures with Spark and assert file-count drops:

```bash
make smoke-compact
```

## On-demand load testing

Use `scripts/load_test_data.py` to create or append fragmented Iceberg data on demand. Each
`--snapshots` increment adds a new Iceberg snapshot; `--rows-per-snapshot` and
`--files-per-snapshot` control volume and small-file fragmentation for Cryo maintenance testing.

Prefer the Docker Spark runner for multi-million row loads (no host Java/PySpark required):

```bash
make load-test-data-docker ARGS="--preset unpartitioned --snapshots 2 --rows-per-snapshot 10000"
```

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

Explicit table (custom namespace/partition):

```bash
make load-test-data-docker ARGS="--catalog rest --namespace stress --table events --partition days(event_date) --snapshots 3 --rows-per-snapshot 500000"
```

Re-run on the same table to append more snapshots (auto-detects `max(id)+1`):

```bash
make load-test-data-docker ARGS="--preset unpartitioned --mode append --snapshots 2 --rows-per-snapshot 10000"
```

Presets: `unpartitioned`, `day`, `month`, `multi`, `nested`. Default namespaces:
`load_test` (REST) and `hms_load_test` (HMS).

Useful env vars: `LOAD_CATALOG`, `LOAD_PRESET`, `LOAD_NAMESPACE`, `LOAD_TABLE`, `LOAD_MODE`,
`LOAD_SNAPSHOTS`, `LOAD_ROWS_PER_SNAPSHOT`, `LOAD_FILES_PER_SNAPSHOT`. Pass `--dry-run` to print
the plan without writing.

Drop and recreate (destructive — requires `--force`):

```bash
make load-test-data-docker ARGS="--preset unpartitioned --drop-existing --force --snapshots 2 --rows-per-snapshot 10000"
```

### Scan and maintain after loading

Loading data does not trigger Cryo automatically. After appending:

```bash
make seed-config
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
```

Use `--full-scan` so tables scanned earlier the same day are rescanned. Then verify stats in
the UI or via the datalake API (`needs_compaction`, `small_files_count`, snapshot count).

To exercise Cryo maintenance:

1. **Ad-hoc:** `POST /api/v1/maintenance/run` with a table filter (requires `cryo serve` or worker/governor).
2. **Scheduled:** create a preset rule matching `needs_compaction == true` and run `make serve`.

Expected signals on heavily loaded tables: `small_files_count >= 10` (local preset
`min_small_files`), `needs_compaction=true`, and snapshot count growing with each append run.
Expire-snapshots is planned when snapshot count/age crosses scanner health thresholds.

Example workflow:

```bash
make load-test-data-docker ARGS="--preset unpartitioned --snapshots 5 --rows-per-snapshot 1000000"
make seed-config
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
# Inspect compaction candidates in the UI or query table_stats (see below)
```

## Configure A Local Source

For scanner/API testing, seed the local REST and HMS sources, Trino destinations, and default presets:

```bash
make seed-config
```

Seeded sources:

- `local-rest` → Iceberg REST at `http://localhost:8181`
- `local-hms` → Hive Metastore at `thrift://localhost:9083`

Seeded Trino destinations:

- `local-trino` → catalog `iceberg` (REST-backed tables)
- `local-trino-hms` → catalog `iceberg_hms` (HMS-backed tables)

Or create only the source row manually:

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

## Run The Scanner

Scan all enabled sources:

```bash
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan
```

Opt into orphan byte detection (Tier 2 storage LIST):

```bash
.venv/bin/python -m cryo.scanner.run --num-chunks 1 --full-scan --cost-full-scan
```

Scan a specific source:

```bash
.venv/bin/python -m cryo.scanner.run \
  --source-id <source_uuid> \
  --num-chunks 1 \
  --full-scan
```

Parallel chunking uses deterministic `index % num_chunks == chunk_id` assignment:

```bash
.venv/bin/python -m cryo.scanner.run --chunk-id 0 --num-chunks 4
.venv/bin/python -m cryo.scanner.run --chunk-id 1 --num-chunks 4
.venv/bin/python -m cryo.scanner.run --chunk-id 2 --num-chunks 4
.venv/bin/python -m cryo.scanner.run --chunk-id 3 --num-chunks 4
```

## Query Local Health

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
    print(Counter(row.grade for row in rows))
    for row in rows:
        print(row)
PY
```

## Run The API

```bash
.venv/bin/uvicorn cryo.api.app:create_app --factory --reload
```

Use one of these local auth headers:

```bash
Authorization: Bearer viewer
Authorization: Bearer operator
Authorization: Bearer admin
```

## Quality Checks

```bash
.venv/bin/ruff check cryo tests
.venv/bin/ruff format --check cryo tests
.venv/bin/mypy cryo
.venv/bin/python -m pytest
```
