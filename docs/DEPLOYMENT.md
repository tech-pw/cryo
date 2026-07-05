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
