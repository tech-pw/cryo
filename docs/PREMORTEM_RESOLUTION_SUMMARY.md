# Premortem Issue Resolution — Status (2026-07-05)

**Do not treat this file as “100% complete.”** Use [`6_MONTH_PREMORTEM_ANALYSIS.md`](./6_MONTH_PREMORTEM_ANALYSIS.md) and the codebase as the source of truth.

## Summary

The July 2026 hardening pass closed many P0 items (JWT/OIDC, secret encryption for AWS/AI keys, native engine disabled, bandit Postgres persistence, PII fail-closed, lock renewal, Helm templates, UI confirmations, tenant scope on dashboard routes).

A follow-up remediation pass addressed additional items from the 6-month premortem register, including:

- Request/job correlation IDs (`cryo/observability/correlation.py`, API middleware)
- Governor wave prefetch batching (`cryo/governor/prefetch.py`)
- Slack webhook encryption at rest
- Worker/governor/scheduler HTTP `/healthz` (`CRYO_HEALTH_PORT`)
- SLA thresholds from preset guardrails (`sla_*` keys)
- Size-aware health scoring thresholds
- Preview `stats_as_of` + stale-stats warning
- Scheduled run actor (`system:scheduler`)
- OIDC refresh token flow (API + UI)
- Advanced routes tenant scoping via `SessionDep`
- GitOps webhook endpoint (requires `CRYO_GITOPS_REPO_PATH`)
- Optional telemetry ingest tick (`CRYO_TELEMETRY_INGEST_ON_STARTUP`, `CRYO_TELEMETRY_TRINO_URL`)
- Scheduled ML cost-model retrain / drift tick
- Frontend: audit actor column, lineage table picker, CI web job

## Still open or partial

| Area | Status |
|------|--------|
| Full OpenTelemetry export | Correlation IDs only; no OTLP exporter yet |
| pgvector ANN for embeddings | JSONB cache + batched scan; no vector index |
| ABAC enforcement on execute paths | Policies stored; compliance API documents gap |
| Flink cluster submit | Config generation only; gated by `CRYO_FLINK_ENGINE_ENABLED` |
| Native Rust engine | Disabled by default |
| Migration squash | 17+ Alembic revisions; squash pending |
| Compose E2E in CI | Playwright smoke exists; not in CI yet |
| Cost attribution | Hidden from nav; API stub |
| Redis HA / Postgres DR | Documented in `DEPLOYMENT.md`; operator-owned |
| gVisor script sandbox | Docker runner only |

## Verification

```bash
make lint && make typecheck && make test
cd web && npm run typecheck && npm test && npm run build
```
