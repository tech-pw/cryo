# Local dev-only inventory (not for production)

This document lists **ad-hoc components that simulate external data-lake infrastructure** for
local development and CI. They stand in for real object storage, Iceberg catalogs, Trino, Spark,
and related wiring.

**Do not deploy these to production servers.** Production uses real S3, managed catalogs, and
customer Trino/Spark clusters configured as Cryo sources and destinations in the database.

---

## Maintenance rule

**When you add a new ad-hoc script, compose service, config file, or local-only code path that
exists only to support the local datalake stack, add an entry to this document in the same PR.**

Keep entries grouped by category. For each item record:

- **Path** — file, directory, or Makefile target
- **Purpose** — one line on what it simulates or enables locally
- **Prod action** — `do-not-deploy`, `remove`, or `gate` (keep code but disable outside dev/CI)

Related layout docs: [`operator-guide.md`](./operator-guide.md#part-1-local-dev-stack).

---

## What stays in the repo (tests & smoke tests)

These **are not** production infrastructure. They **should remain** in the repo per best
practices. They may *depend on* the local datalake stack when run locally or in CI, but the test
artifacts themselves are permanent.

| Path | Purpose |
|------|---------|
| `tests/` | Unit and integration tests (including HMS catalog tests) |
| `tests/integration/hms/test_hms_catalog_integration.py` | Opt-in HMS catalog integration test |
| `tests/integration/native/test_native_expire_orphan_e2e.py` | Opt-in native expire + orphan e2e (REST + MinIO) |
| `tests/integration/native/generate_rest_fixture.py` | Seed native e2e table + orphan object |
| `tests/integration/hms/smoke_hms_trino.py` | Smoke test: Trino can read HMS fixture tables |
| `scripts/smoke_file_counts.py` | Smoke test: file counts / local Spark compaction |
| `scripts/smoke_cryo_e2e.py` | Smoke test: scan → plan → execute path |
| `make test`, `make smoke-*`, `make test-hms-integration`, `make test-native-e2e`, `make test-native-e2e-local`, `make test-native-compaction-e2e`, `make test-native-compaction-e2e-local-go` | Makefile entrypoints for the above |
| `.github/workflows/ci.yml` | CI lint, typecheck, unit tests; `native-e2e` job (REST + MinIO + cryo-engine) |

**Fixture generators** (below) write data to local MinIO only; they are inventoried under
[Ad-hoc data scripts](#ad-hoc-data-scripts-not-tests) because they are not test assertions—they
create disposable lake data.

---

## Compose & containers (do not deploy)

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`docker-compose.dev-datalake.yml`](../docker-compose.dev-datalake.yml) | Full local stand-in for S3 + catalogs + engines | `do-not-deploy` |
| `minio`, `minio-init` | S3-compatible warehouse (`s3://warehouse/`) | `do-not-deploy` |
| `iceberg-rest` | Iceberg REST catalog on `:8181` | `do-not-deploy` |
| `hms-postgres`, `hive-metastore` | Hive Metastore + backing Postgres on `:9083` | `do-not-deploy` |
| `trino` | Local Trino coordinator on `:8080` | `do-not-deploy` |
| `spark` (Compose profile `tools`) | Long-lived Spark container for seeding and local Spark SQL | `do-not-deploy` |

Optional local observability (also not in Helm v1):

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`docker-compose.observability.yml`](../docker-compose.observability.yml) | Prometheus, Grafana, Loki, Promtail | `do-not-deploy` |
| [`deploy/prometheus/`](../deploy/prometheus/), [`deploy/grafana/`](../deploy/grafana/), [`deploy/loki/`](../deploy/loki/), [`deploy/promtail/`](../deploy/promtail/) | Config mounted by observability compose | `do-not-deploy` (use managed monitoring in prod) |

**Prod-shaped Cryo compose** — [`docker-compose.yml`](../docker-compose.yml) is the only compose
file intended to mirror production topology (Postgres, Redis, Cryo services). In real prod, Postgres
and Redis are typically managed services, not this compose file.

---

## Hive Metastore image build (do not deploy)

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`deploy/hive/Dockerfile`](../deploy/hive/Dockerfile) | Custom HMS image (Postgres backend + MinIO S3A) | `do-not-deploy` |
| [`deploy/hive/hive-site.xml`](../deploy/hive/hive-site.xml) | HMS config pointing at `minio:9000` | `do-not-deploy` |
| [`deploy/hive/entrypoint.sh`](../deploy/hive/entrypoint.sh) | HMS schema init + metastore start | `do-not-deploy` |

---

## Trino catalog config (local engines only)

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`trino/catalog/iceberg.properties`](../trino/catalog/iceberg.properties) | Trino `iceberg` catalog → REST + MinIO | `remove` from prod Trino clusters |
| [`trino/catalog/iceberg_hms.properties`](../trino/catalog/iceberg_hms.properties) | Trino `iceberg_hms` catalog → HMS + MinIO | `remove` from prod Trino clusters |

Production Trino catalogs are configured on the customer's Trino cluster, not via this directory.

---

## Ad-hoc data scripts (not tests)

Scripts that **create or load Iceberg data** into the local MinIO warehouse. Safe to keep in the
repo; do not run or package them as part of a production deployment.

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`scripts/create_test_tables.py`](../scripts/create_test_tables.py) | Seed demo `demo.*` tables via REST catalog | `gate` (dev/CI only) |
| [`scripts/create_partition_fixture_tables.py`](../scripts/create_partition_fixture_tables.py) | Partition-rich fragmented fixtures in `fixtures` namespace | `gate` |
| [`scripts/load_test_data.py`](../scripts/load_test_data.py) | On-demand fragmented snapshots for maintenance load testing | `gate` |
| [`scripts/seed_local_config.py`](../scripts/seed_local_config.py) | Upsert DB rows pointing at `localhost` REST/HMS/Trino | `gate` (never run in prod bootstrap) |
| [`scripts/spark_session.py`](../scripts/spark_session.py) | PySpark session wired to local REST + MinIO defaults | `gate` |
| [`tests/integration/hms/generate_hms_fixture_tables.py`](../tests/integration/hms/generate_hms_fixture_tables.py) | Large HMS fixture tables in `hms_issues` namespace (MinIO only) | `gate` |
| [`tests/integration/hms/spark_hms_session.py`](../tests/integration/hms/spark_hms_session.py) | PySpark session wired to local HMS + MinIO (used by fixture generator) | `gate` |

Makefile targets that invoke the above against the datalake compose file:

| Target | Purpose | Prod action |
|--------|---------|-------------|
| `make seed` | Host-run `create_test_tables.py` | `gate` |
| `make seed-docker` | Spark container seed | `gate` |
| `make seed-fragmented` | Partition fixtures via Spark | `gate` |
| `make seed-config` | Run `seed_local_config.py` | `gate` |
| `make seed-hms-fixtures` | HMS fixture generator (host Spark) | `gate` |
| `make seed-hms-fixtures-docker` | HMS fixtures via Spark container | `gate` |
| `make load-test-data` | Host-run load generator | `gate` |
| `make load-test-data-docker` | Load generator via Spark container | `gate` |
| `make up-datalake` | Start dev datalake compose only | `gate` |
| `make spark-shell` | Shell into local Spark container | `gate` |

---

## Local orchestration & exploration

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`start.sh`](../start.sh) | JupyterLab workflow: datalake + Postgres/Redis + demo seed | `gate` |
| [`start-local.sh`](../start-local.sh) | Starts datalake + observability Docker stacks; Cryo app on host (see `START_DATALAKE`, `START_OBSERVABILITY`, `DOCKER_DATALAKE_*` env vars) | `gate` (datalake/obs sections only) |
| [`requirements.txt`](../requirements.txt) | JupyterLab / PySpark / PyIceberg exploration deps (not backend `pyproject` prod install) | `gate` |
| [`Pipfile`](../Pipfile) | Mirror of `requirements.txt` for `pipenv` users (not used in backend prod install) | `gate` |
| [`notebooks/iceberg_local_demo.ipynb`](../notebooks/iceberg_local_demo.ipynb) | Interactive local Iceberg demo | `gate` |

---

## Application code: local-only engine wiring

Production uses real Trino and Spark-on-EMR engines. These paths exist to route maintenance SQL to
**local Docker** instead.

| Path | Purpose | Prod action |
|------|---------|-------------|
| [`cryo/engines/spark/local.py`](../cryo/engines/spark/local.py) | `SparkLocalEngine` — `docker compose exec` into `cryo-spark` | `gate` (`engine_mode=local` or `local: true` in destination config only) |
| [`cryo/engines/factory.py`](../cryo/engines/factory.py) | Branches to `SparkLocalEngine` when `config.local` / `engine_mode == "local"` | `gate` (ensure prod destinations never set local mode) |

**Note:** [`cryo_spark_runner.py`](../cryo_spark_runner.py) is the Spark submit entrypoint for
**EMR Serverless in production**. The local engine reuses it inside the dev Spark container; do not
delete the file for prod—only disable local docker exec routing.

Seeded destination examples in `seed_local_config.py` that must not exist in prod DB:

- `local-trino` → `localhost:8080`, catalog `iceberg`
- `local-trino-hms` → `localhost:8080`, catalog `iceberg_hms`
- Sources `local-rest`, `local-hms` → `localhost:8181` / `thrift://localhost:9083`

---

## Pre-production cutover checklist

Use this when preparing a production deployment or trimming a release artifact:

- [ ] **Compose:** `docker-compose.dev-datalake.yml` and `docker-compose.observability.yml` are not on prod hosts or in prod CI deploy pipelines.
- [ ] **Images:** No MinIO, Tabulario REST, custom HMS, or local Trino images in prod K8s/Helm.
- [ ] **Trino:** `trino/catalog/*.properties` are not copied to production Trino; catalogs configured per environment.
- [ ] **DB seed:** `scripts/seed_local_config.py` is not part of prod migrations or bootstrap; sources/destinations point at real endpoints.
- [ ] **Engines:** No destination rows with `local: true` or `engine_mode: local`; `SparkLocalEngine` path unused.
- [ ] **Secrets:** No `minioadmin` or other compose-default credentials in prod config.
- [ ] **Makefile / scripts:** `up-datalake`, `seed-docker`, `load-test-data-docker`, etc. are dev/CI only (documented here).
- [ ] **This file:** Any new local-only component added since last release is listed above.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-06 | Initial inventory after splitting `docker-compose.dev-datalake.yml` from Cryo platform compose |
