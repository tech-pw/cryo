# Local Docker Compose Layout

Cryo uses **three separate Compose files** so production deployments never pull in local-only
test infrastructure.

| File | Purpose | Deploy to prod? |
|------|---------|-----------------|
| [`docker-compose.yml`](../docker-compose.yml) | Cryo platform: Postgres, Redis, API, worker, governor, scheduler, scanner, web | Yes (shape matches Helm; use managed PG/Redis in real prod) |
| [`docker-compose.dev-datalake.yml`](../docker-compose.dev-datalake.yml) | **LOCAL/CI ONLY** — MinIO, Iceberg REST, HMS, Trino, Spark | **No** |
| [`docker-compose.observability.yml`](../docker-compose.observability.yml) | Optional Prometheus, Grafana, Loki, Promtail | No (not in Helm v1) |

All files share Compose project name `cryo` and network `cryo-dev` so services can reach each other
when started together.

## Common workflows

### Full local dev (recommended)

Starts datalake + Cryo deps + observability in Docker, then runs API/web/orchestration on the host:

```bash
./start-local.sh start
```

### Full local dev with native engine

Use Docker mode when testing `EngineType.NATIVE`. The worker then runs inside Linux and can execute
the bundled Linux `cryo-engine` binary instead of trying to spawn it from macOS:

```bash
START_CRYO_DOCKER=1 ./start-local.sh start
```

The image builds the Go engine (`engine-go`) for the container's platform and links it as
`cryo-engine`. For a binary outside the image, build it with `make` from `engine-go` for the target
platform and set the native destination `config.binary_path` to it.

Equivalent Docker-only full stack:

```bash
make up
```

### Cryo only (against real external catalogs/engines)

```bash
make up-cryo
# or
docker compose -f docker-compose.yml up -d
```

Configure sources and destinations in the Cryo DB to point at your real Iceberg catalogs and Trino/Spark
clusters.

### Dev datalake only (MinIO + catalogs + Trino)

```bash
make up-datalake
```

Useful for HMS integration tests, fixture seeding, and Spark smoke tests without starting Cryo
containers.

### Tear down

```bash
make down
# or, to also remove volumes:
docker compose -f docker-compose.yml -f docker-compose.dev-datalake.yml -f docker-compose.observability.yml down -v
```

## `start-local.sh` environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `START_DATALAKE` | `1` | Start dev datalake compose file |
| `START_OBSERVABILITY` | `1` | Start observability compose file |
| `START_CRYO_DOCKER` | `0` | When `0`, only `postgres` + `redis` from Cryo file; app runs on host. When `1`, API/web/orchestration run in Linux containers. |
| `DOCKER_CRYO_SERVICES` | `postgres redis` | Services from `docker-compose.yml`; in `START_CRYO_DOCKER=1` mode defaults to `postgres redis cryo-api cryo-scanner cryo-scheduler cryo-governor cryo-worker cryo-web` (`cryo-scanner` runs `cryo scanner`, the Redis-backed ad-hoc scan queue consumer) |
| `DOCKER_DATALAKE_SERVICES` | `minio minio-init iceberg-rest hms-postgres hive-metastore trino` | Services from dev-datalake file |
| `DOCKER_OBS_SERVICES` | `prometheus grafana loki promtail` | Services from observability file |

## Production

Production deploys **Cryo application pods only** via [`deploy/helm/cryo/`](../deploy/helm/cryo/).
Do not deploy `docker-compose.dev-datalake.yml` or `docker-compose.observability.yml` to production
servers. External object storage, catalogs, and query engines are configured as Cryo sources and
destinations in the database.

**Full inventory of local-only components (update when adding new ad-hoc dev tooling):**
[`LOCAL_DEV_ONLY_INVENTORY.md`](./LOCAL_DEV_ONLY_INVENTORY.md).
