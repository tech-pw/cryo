# Cryo Deployment Guide

This guide covers local deployment with Docker Compose and production deployment pointers for Kubernetes/Helm.

## Prerequisites

- Docker Engine 24+ with Compose v2
- 8 GB RAM minimum for the full stack (Trino + Spark paths need more)
- Ports available: `5432`, `6379`, `8000`, `8080`, `9090`, `3000`, `3100`, `5173`

## Docker Compose (local / single-node)

### 1. Start infrastructure and services

```bash
cd /path/to/Cryo
docker compose up -d
```

Core services:

| Service | Purpose | URL |
|---------|---------|-----|
| `cryo-api` | FastAPI backend | http://localhost:8000 |
| `cryo-web` | React UI | http://localhost:5173 |
| `cryo-scanner` | Catalog scanner CronJob-style worker | — |
| `cryo-scheduler` | Maintenance scheduler (leader-elected) | — |
| `cryo-governor` | Load-aware admission controller | — |
| `cryo-worker` | Maintenance job executor | — |
| `postgres` | Cryo metadata DB | localhost:5432 |
| `redis` | Queue + leader locks | localhost:6379 |
| `trino` | Default maintenance engine | http://localhost:8080 |
| `prometheus` | Metrics scrape | http://localhost:9090 |
| `grafana` | Dashboards + alerting UI | http://localhost:3000 |
| `loki` | Log aggregation | http://localhost:3100 |
| `promtail` | Docker log shipper | — |

### 2. Apply migrations

```bash
docker compose exec cryo-api alembic upgrade head
```

Or from a local venv:

```bash
make install
make migrate
```

### 3. Seed demo data (optional)

```bash
make seed
```

### 4. Verify health

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/metrics | head
open http://localhost:3000   # Grafana (admin / admin)
```

Grafana auto-provisions:

- **Prometheus** datasource (`deploy/grafana/datasources/prometheus.yml`)
- **Loki** datasource (same file)
- **Cryo Overview** dashboard (`deploy/grafana/dashboards/cryo-overview.json`)

### 5. Environment variables

Set via `docker-compose.yml` or `.env` (prefix `CRYO_`):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string |
| `REDIS_URL` | Redis connection string |
| `CRYO_JWT_SECRET` | JWT signing secret (change in prod) |
| `CRYO_SLACK_WEBHOOK_URL` | Slack incoming webhook for reports/alerts |
| `CRYO_PAGERDUTY_ROUTING_KEY` | PagerDuty Events API v2 routing key |
| `CRYO_ENVIRONMENT` | `dev`, `staging`, or `prod` |

### 6. Embedded UI demo

Open the sample host page after starting the web service:

```bash
open http://localhost:5173/embed-demo.html
```

Pass a custom UI base URL: `?cryoUrl=https://cryo.example.com`

---

## Kubernetes / Helm (production)

Helm chart skeleton lives at `deploy/helm/cryo/`. Production rollout typically splits Cryo into separate Deployments:

| Component | K8s resource | Notes |
|-----------|--------------|-------|
| API | Deployment + Service + Ingress | HPA on CPU; `/metrics` ServiceMonitor |
| Scanner | CronJob | One-shot per schedule; chunk parallelism via env |
| Scheduler | Deployment (replicas ≥ 1) | Redis leader lease — only one active |
| Governor | Deployment (replicas ≥ 1) | Redis leader lease — only one active |
| Worker | Deployment (replicas N) | Concurrency bounded by Governor, not replica count |
| Web | Deployment + Ingress | Static assets or CDN front |

### Recommended production checklist

1. **Secrets** — store DB/Redis/JWT/Slack/PagerDuty keys in a secret manager; mount via ExternalSecrets or SealedSecrets.
2. **Postgres** — managed RDS/Cloud SQL; run `alembic upgrade head` as a Helm pre-install/pre-upgrade Job.
3. **Redis** — managed ElastiCache/Memorystore for queue + leader election durability.
4. **Observability** — scrape `/metrics` with Prometheus Operator; ship logs to Loki/CloudWatch; import `deploy/grafana/dashboards/cryo-overview.json`.
5. **Ingress** — TLS termination; set `CRYO_CORS_ALLOW_ORIGINS` to your UI origin.
6. **Multi-tenant** — run migration `20260705_0004_multi_tenant`; backfill `tenant_id` before enforcing JWT tenant claims.
7. **Rate limits** — tune `CRYO_API_RATE_LIMIT_RPS` / `CRYO_API_RATE_LIMIT_BURST` on the API Deployment.

### Helm install (skeleton)

```bash
helm upgrade --install cryo deploy/helm/cryo \
  -f deploy/helm/cryo/values.yaml \
  --set image.tag=<git-sha> \
  --namespace cryo --create-namespace
```

Customize `values.yaml` for image registry, resource limits, envFrom secrets, and CronJob schedules before production use.

---

## Upgrades

1. Pull new image / rebuild: `docker compose build`
2. Run migrations: `alembic upgrade head`
3. Rolling restart stateless services: `docker compose up -d --force-recreate cryo-api cryo-worker cryo-governor cryo-scheduler`
4. Verify Grafana dashboard panels and `/metrics` counters increment

---

## Related docs

- [`docs/OPERATOR_RUNBOOK.md`](./OPERATOR_RUNBOOK.md) — incident response
- [`docs/operator-runbook.md`](./operator-runbook.md) — day-to-day local ops
- [`AGENTS.md`](../AGENTS.md) — architecture guardrails
