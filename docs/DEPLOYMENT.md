# Deployment and tenancy

## Helm

The chart at `deploy/helm/cryo/` ships Deployments for **api**, **worker**, **governor**,
**scheduler**, a **scanner** CronJob, and an API Service. Configure Postgres/Redis/JWT secrets via
`global.envFromSecret` (or individual `CRYO_*` env vars).

```bash
helm install cryo deploy/helm/cryo/ \
  --set image.repository=your-registry/cryo \
  --set global.envFromSecret=cryo-secrets
```

For local development, `docker-compose.yml` remains the fastest path.

## Authentication

API routes require a **signed HS256 JWT** (`CRYO_JWT_SECRET` or `CRYO_SECRET_KEY`). In
non-production environments, `POST /api/v1/auth/token` issues dev tokens for the UI and tests.
**Do not expose that endpoint in production.**

## Multi-tenancy

Cryo stores `tenant_id` on sources, destinations, tables, and presets. **By default
(`CRYO_ENFORCE_TENANT_ISOLATION=false`) Cryo is single-tenant-per-deployment** — queries are not
filtered unless you:

1. Set `CRYO_ENFORCE_TENANT_ISOLATION=true`, and
2. Issue JWTs with a `tenant_id` claim.

Until both are enabled, treat each Cryo install as one logical tenant.

## Secrets at rest

Source, destination, and AWS integration credentials are **Fernet-encrypted** with a key derived
from `CRYO_SECRET_KEY`. Rotate `CRYO_SECRET_KEY` only with a re-encryption migration plan.

## Native engine

`EngineType.NATIVE` is **experimental and disabled** unless `CRYO_NATIVE_ENGINE_ENABLED=true`.
The OPTIMIZE path is not production-ready; use Trino or Spark for compaction.

## Backend-only features (no UI yet)

The following APIs exist and are tested at the route layer but **do not have frontend pages**
as of the premortem hardening pass (2026-07):

- **Tiering / adaptive routing** — `cryo/api/phase8_routes.py`
- **Approvals, lineage, FinOps forecast, advanced anomalies** — `cryo/api/advanced_routes.py`

Track UI surfacing in `docs/EXECUTION_PLAN.md` before treating these as shipped product features.

## Telemetry ingestion

Query performance, SLA P95, lineage, and benefit scores depend on rows in `cryo.query_logs`.
Ingestion is **not enabled by default**. To schedule Trino system-table ingestion from the
scheduler leader:

```bash
export CRYO_TELEMETRY_INGEST_ON_STARTUP=true
export CRYO_TELEMETRY_TRINO_URL=https://trino.example.com
```

For production, prefer a Trino event listener or query-log sidecar that POSTs to Cryo's ingest
API rather than polling `system.runtime.queries` from the scheduler.

## Slack webhooks

Slack incoming webhook URLs stored via `/api/v1/slack-configs` are **encrypted at rest** with the
same Fernet key as other secrets. Rotate webhooks in Slack if plaintext copies existed before
encryption was enabled.

## Health probes (worker / governor / scheduler)

Background processes expose `GET /healthz` on `CRYO_HEALTH_PORT` (default `8081`). When running
`cryo serve`, governor/worker/scheduler/scanner bind consecutive ports starting at `health_port`.

## Backup and disaster recovery

Cryo state lives in **Postgres** (`cryo` schema) and **Redis** (queues, locks, leader election).
Object storage (Iceberg data) is external.

| Component | Backup approach |
|-----------|-----------------|
| Postgres | Nightly `pg_dump` / managed RDS snapshots; test restore quarterly |
| Redis | Ephemeral queues — rebuild from Postgres run state; use Redis Sentinel/Cluster for HA |
| Secrets | `CRYO_SECRET_KEY` in K8s secret manager; document rotation runbook |

## Redis high availability

Single-node Redis is a split-brain and SPOF risk for leader election. For production:

- Run Redis Sentinel (3+ nodes) or a managed Redis cluster
- Point all Cryo services at the same logical endpoint
- Set conservative lease TTL (`CRYO_LEADER_LEASE_SECONDS`) and monitor governor/scheduler logs
  for duplicate-leader warnings

## GitOps webhooks

Configure a local clone path and POST Git push payloads to `POST /api/v1/advanced/gitops/webhook`
(operator role required):

```bash
export CRYO_GITOPS_REPO_PATH=/var/cryo/gitops-policies
export CRYO_GITOPS_POLICY_DIR=policies
export CRYO_GITOPS_WEBHOOK_SECRET=your-shared-secret
```

Send header `X-Cryo-GitOps-Secret: your-shared-secret` on webhook POSTs.

Polling remains available via `watch_git_repository()` for environments without inbound webhooks.

## OpenTelemetry

Enable distributed tracing:

```bash
export CRYO_OTEL_ENABLED=true
export CRYO_OTEL_ENDPOINT=http://otel-collector:4318/v1/traces
export CRYO_OTEL_SERVICE_NAME=cryo-api
```

## Custom script sandbox (gVisor)

For untrusted custom maintenance scripts, install gVisor and set:

```bash
export CRYO_SCRIPT_RUNTIME=runsc
```

Falls back to standard Docker with `--network none`, read-only rootfs, and `nobody` user.

