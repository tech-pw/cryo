# Local Development Guide

This guide describes the local development workflow for the current Cryo backend.

## Prerequisites

- Python 3.12.
- Docker and Docker Compose.
- Java 17+ for Spark-based fixture scripts.
- A Python virtual environment with project dependencies installed.

Recommended setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

If the resolver backtracks into old `pyiceberg`/`pyarrow` builds, install the core runtime/test
packages directly and then install plain `pyiceberg`:

```bash
.venv/bin/python -m pip install pytest ruff mypy fastapi httpx sqlalchemy alembic \
  "psycopg[binary]" pydantic-settings redis trino boto3 uvicorn
.venv/bin/python -m pip install pyiceberg
```

## Start The Local Stack

```bash
make up
docker compose ps
.venv/bin/alembic upgrade head
```

Services:

- MinIO: `http://localhost:9000` and console `http://localhost:9001`.
- Iceberg REST catalog: `http://localhost:8181`.
- Trino: `http://localhost:8080`.
- Spark runner: container service `spark`.
- Postgres: `localhost:5432`, database/user/password `cryo`.
- Redis: `localhost:6379`.

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

## Configure A Local Source

For scanner/API testing, seed the local REST source, Trino destination, and default preset:

```bash
make seed-config
```

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
