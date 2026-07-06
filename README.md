# Cryo — Iceberg Maintenance & Health Platform

> **Status: design / scaffolding.** This repo currently contains the design docs and a local OSS
> Iceberg dev stack. Implementation is tracked in [`todo.md`](./todo.md). *Cryo* is a working
> codename — rename freely.

**Cryo** is a self-hosted platform that consolidates **Apache Iceberg table maintenance** and
**lakehouse health** into one product: policy/**preset-driven** compaction, snapshot expiry, orphan-
file cleanup and manifest rewrites, with first-class **table health**, **datalake health**,
and **ad-hoc maintenance** — driven from a UI and a Python API, deployable to Kubernetes.

It is inspired by [Floe](https://github.com/nssalian/floe) and
[Apache Amoro](https://github.com/apache/amoro), but built around our needs: it preserves the
**load-aware, partition-aware filtering** that keeps our Trino clusters healthy today, adds a
**Spark-on-EMR** engine for large tables, and removes the Airflow + dbt sprawl by moving scheduling
and orchestration into the platform itself.

---

## Why

Our Iceberg maintenance is scattered across Airflow DAGs, dbt macros, and a reporting script. Cryo
consolidates all of it into a single deployable system. See [`plan.md`](./plan.md) §1 for the full
breakdown of what is being replaced and [`hld.md`](./hld.md) §9 for the old→new mapping.

## What it does

- **Sources (read side):** connect Iceberg catalogs (REST / Hive Metastore / Glue). Discover tables
  and read metadata via PyIceberg.
- **Destinations (write side):** submit maintenance to **Trino** (SQL) or **Spark on EMR** (Spark
  SQL). Sources and destinations are configured **independently**.
- **Scanner:** chunked, resumable datalake scan → per-table & per-partition stats, **A–F health grade**,
  `needs_compaction` priority, and **S3 cost** estimate.
- **Presets:** named maintenance profiles binding *operations + params + target engine + schedule +
  selection rules*. e.g. `spark-large` = "tables > 100 GB → Spark/EMR, deep maintenance weekly".
- **Assignment UX:** a table explorer with rich filters (schema, size, small-file count/%, grade,
  partitioning, drift…) → **filter → Select all matches → assign a preset** (across pages), plus
  rule-based auto-matching with manual override.
- **Planner + Governor:** engine-agnostic, **partition-aware** plan generation rendered to Trino or
  Spark SQL, executed under **global, load-aware admission control** (concurrency caps, cooldown,
  off-peak windows, adaptive budget, deadlines, failure isolation).
- **Dashboards & reports:** datalake health, per-table health + audit timeline, live run console,
  drift/cost alerts, scheduled email/Slack summaries.

## Key principles

- **No Airflow** — scheduling/orchestration live in the platform.
- **Sources ≠ Destinations** — separate configs, storage, and code paths.
- **Trino SQL and Spark SQL are generated separately**; the Planner emits no SQL strings.
- **Preserve** today's load-aware, partition-aware behavior (see `agents.md` §5 parity checklist).

---

## Documentation

| Doc | What's in it |
|-----|--------------|
| [`plan.md`](./plan.md) | Vision, scope, architecture decisions, phased roadmap, assumptions. |
| [`hld.md`](./hld.md) | High-level design: components, data flow, deployment topology, diagrams. |
| [`lld.md`](./lld.md) | Low-level design: repo layout, Postgres schema, interfaces, planner algorithm, rendered SQL per engine, API surface. |
| [`agents.md`](./agents.md) | Guide & guardrails for AI agents / contributors working in the repo. |
| [`todo.md`](./todo.md) | Phased, checkbox task breakdown (P0–P7). |
| [`docs/`](./docs/) | Practical backend/API docs, local runbook, implementation status, and session build log. |
| [`docs/local-docker-compose.md`](./docs/local-docker-compose.md) | Three-file Compose layout (Cryo / dev datalake / observability). |
| [`docs/LOCAL_DEV_ONLY_INVENTORY.md`](./docs/LOCAL_DEV_ONLY_INVENTORY.md) | Ad-hoc local datalake components **not for prod** (update when adding new dev-only tooling). |

## Architecture at a glance

```
Sources (read)            Cryo platform                         Destinations (write)
─────────────             ───────────────────────────          ────────────────────
Iceberg REST  ─┐          Scanner → Postgres (stats/health)
Hive / Glue   ─┼─ read ─► Scheduler → Governor → Workers ─ exec ─► Trino (SQL)
               │          Planner (partition-aware, engine-agnostic)   Spark on EMR (Spark SQL)
               │          API (FastAPI) ── Web UI (React)
               └────────► Redis (queue/locks/live)  ·  S3/MinIO (data)
```

Full diagrams in [`hld.md`](./hld.md).

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy + Alembic (PostgreSQL), PyIceberg, trino-python-
  client, boto3 (EMR Serverless), Redis.
- **Frontend:** React + TypeScript (Vite), Ant Design, Recharts, TanStack Query.
- **Deploy:** Docker images + Helm chart; Kubernetes (api, web, scheduler, governor, workers; scanner
  as a CronJob). Postgres + Redis as dependencies.

---

## Local development stack

The repo ships a local OSS Iceberg environment used for development and contract/e2e tests:

- **MinIO** — S3-compatible object storage warehouse
- **Iceberg REST catalog** — OSS catalog service
- **Trino** — SQL query engine (maintenance destination)
- **Spark runner** — local Spark container for fixture seeding and Spark SQL smoke checks
- **PostgreSQL** — local metadata store for upcoming backend services
- **Redis** — local queue/lock/live-state store for upcoming scheduler/governor services
- **Python `.venv`** — PyIceberg, PySpark, and JupyterLab for exploration

### Prerequisites
- Python 3.12 (PySpark/Iceberg wheels are most reliable here)
- Docker and Docker Compose
- Java 17+ (required by PySpark) — check with `java -version`

### Quick start (current stack)

```bash
./start-local.sh start
```

This starts the dev datalake (MinIO, catalogs, Trino), Cryo Postgres/Redis, observability
(Prometheus, Grafana, Loki), and host-run API + web + `cryo serve`. See
[`docs/local-docker-compose.md`](./docs/local-docker-compose.md) for compose file layout.

For Jupyter-based exploration:

```bash
./start.sh
```

This starts the dev datalake + Cryo deps + Spark, seeds demo tables, and launches JupyterLab.

Useful env vars: `PYTHON_BIN` (default `python3.12`), `JUPYTER_PORT` (`8888`),
`JUPYTER_IP` (`127.0.0.1`), `SEED_TABLES` (`1`), `SEED_FIXTURES` (`0`).

### Manual setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
make up-datalake                  # MinIO, Iceberg REST, HMS, Trino
docker compose -f docker-compose.yml up -d postgres redis
make ps                           # wait until healthy
python scripts/create_test_tables.py
```

Service endpoints:

| Service | URL |
|---------|-----|
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 (minioadmin / minioadmin) |
| Iceberg REST catalog | http://localhost:8181 |
| Trino | http://localhost:8080 |
| Spark UI | http://localhost:4040 (while Spark jobs are running) |
| Postgres | localhost:5432 (`cryo` / `cryo` / `cryo`) |
| Redis | localhost:6379 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |

Query with Trino:

```bash
docker exec -it cryo-trino trino --execute "SHOW SCHEMAS FROM iceberg"
docker exec -it cryo-trino trino --execute "SELECT * FROM iceberg.demo.customers"
```

### Fragmented fixture tables

Partition-rich test fixtures are available in the `fixtures` namespace. They cover unpartitioned,
day, month, year, bucket, multi-column, timestamp/date, and nested object-like schemas, and are
written in small batches to intentionally create many tiny Iceberg data files.

```bash
make seed-fragmented
make smoke-file-counts
make smoke-compact
```

Equivalent local-Python commands:

```bash
python scripts/create_partition_fixture_tables.py
python scripts/smoke_file_counts.py
python scripts/smoke_file_counts.py --compact --assert-drop
```

Useful fixture env vars: `FIXTURE_NAMESPACE` (`fixtures`), `FIXTURE_BATCHES` (`12`),
`FIXTURE_FILES_PER_BATCH` (`8`), `FIXTURE_ROWS_PER_BATCH` (`64`), and
`FIXTURE_DROP_EXISTING` (`1`).

### Tear down

```bash
make down                 # add -v to also remove volumes (see docs/local-docker-compose.md)
```

## Current project layout

```
.
├── plan.md / hld.md / lld.md / agents.md / todo.md   # design docs
├── docker-compose.yml              # Cryo platform (prod-shaped)
├── docker-compose.dev-datalake.yml # LOCAL/CI ONLY: MinIO, catalogs, Trino, Spark
├── docker-compose.observability.yml # optional Prometheus, Grafana, Loki
├── Makefile                    # local stack, fixture, and smoke-test shortcuts
├── requirements.txt            # dev/exploration deps (backend deps move to pyproject in P0)
├── start.sh                    # one-command local stack + Jupyter
├── scripts/
│   ├── spark_session.py        # shared Spark + Iceberg config
│   ├── create_test_tables.py   # seeds demo tables
│   ├── create_partition_fixture_tables.py  # fragmented partition-rich fixtures
│   └── smoke_file_counts.py    # file-count inspection / compaction smoke
├── trino/catalog/iceberg.properties
└── notebooks/iceberg_local_demo.ipynb
```

The `cryo/` backend package, `web/` frontend, `migrations/`, and `deploy/helm/` are added starting in
Phase 0 — see [`todo.md`](./todo.md).

## Troubleshooting

- **Spark jar download slow on first run** — PySpark fetches Iceberg/Hadoop AWS jars via Maven once.
- **Connection refused to REST catalog** — ensure `make ps` shows `cryo-iceberg-rest` on 8181.
- **Trino cannot see tables** — re-run `python scripts/create_test_tables.py` after the stack is up.
- **Java not found** — install JDK 17+ and set `JAVA_HOME`.

## License

TBD.
