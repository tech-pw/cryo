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

The chart defaults to `CRYO_ENVIRONMENT=prod`. These remain exposed when an operator changes that or opts in:

- In `dev` or `test` with no OIDC issuer, `POST /api/v1/auth/token` self-issues a role with no identity check. Setting an OIDC issuer closes it.
- `CRYO_ALLOW_DEV_TOKEN_IN_PROD` reopens the dev token in every environment, including `prod`.
- Outside `staging` and `prod`, omitting `CRYO_SECRET_KEY` falls back to the published default `dev-only-change-me`, and tokens signed with it are accepted. `staging` and `prod` refuse to start with the default or a key shorter than 32 characters.
- FastAPI `/docs` and `/openapi.json` are served outside `prod`, and in `prod` when `CRYO_API_DOCS_ENABLED` is set.
- The run console WebSocket accepts the token as `?token=`, so it can reach proxy and access logs (#551).

## Data plane

The governor Role can create and delete Jobs and delete Pods in its namespace. Those grants live in the Helm templates, not in a second policy engine. Narrow or revoke them there.

Engines execute SQL or a compaction plan against the warehouse. A destination credential is authority over that warehouse.

## Out of scope

A dependency advisory with an upstream fix is a pull request, not a private advisory. A scanner hit with no Cryo exploit path is not a vulnerability. Public CVEs are not private advisories. Unvalidated scanner or LLM reports will be closed.

Do not bump Iceberg, Trino, Spark, or PyIceberg minors inside a supported tag only because a scanner flagged them.
