# Threat model

One Cryo instance is one trust domain. This is the operator model, not an exploit guide.

## Assets

- Catalog and engine credentials stored encrypted at rest
- `CRYO_SECRET_KEY`, used for Fernet and as the HS256 fallback
- Warehouse data that maintenance rewrites, expires, or deletes
- Audit rows for those actions

## Trust boundary

Anyone who can reach the API with a recognized role can do what that role allows. There is no second tenant wall unless `enforce_tenant_isolation` is on, and that is opt-in.

Roles in code are `evidence_collector`, `viewer`, `operator`, and `admin`. `editor` is an alias of `operator`. An OIDC `groups` claim whose value is `admin` is an admin grant. Accepted claim keys are `role`, `roles`, `scope`, `scopes`, and `groups`.

## Defaults that are not safe to expose

- `CRYO_ENVIRONMENT` defaults to `dev` in the chart. `POST /api/v1/auth/token` then self-issues a role with no identity check. OIDC does not disable that route.
- `CRYO_ALLOW_DEV_TOKEN_IN_PROD` reopens the dev token when `environment=prod`.
- Omitting `CRYO_SECRET_KEY` falls back to the published default `dev-only-change-me`. Tokens signed with that default are accepted.
- FastAPI `/docs` and `/openapi.json` are on unless the app disables them. The WebSocket accepts `?token=`.

## Data plane

The governor Role can create and delete Jobs and delete Pods in its namespace. Those grants live in the Helm templates, not in a second policy engine. Narrow or revoke them there.

Engines execute SQL or a compaction plan against the warehouse. A destination credential is authority over that warehouse.

## Out of scope

A dependency advisory with an upstream fix is a pull request, not a private advisory. A scanner hit with no Cryo exploit path is not a vulnerability. Public CVEs are not private advisories. Unvalidated scanner or LLM reports will be closed.

Do not bump Iceberg, Trino, Spark, or PyIceberg minors inside a supported tag only because a scanner flagged them.
