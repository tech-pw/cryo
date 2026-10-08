# Versioning

Cryo is pre-1.0. Package, chart, and web versions are `0.1.0`. The changelog's only released heading is `0.0.0`. Those do not match. There is no root `VERSION` file and no release workflow that freezes `[Unreleased]`.

Until 1.0:

- A minor may change HTTP, Helm values, schema, and engine SQL. The changelog must say so. There is no still-working deprecation window.
- Support is the latest `main` plus the newest tag, as [SECURITY.md](../SECURITY.md) states. The 90-day predecessor window in [SUPPORT_POLICY.md](SUPPORT_POLICY.md) is not in force.
- `/api/v1` has no consumer pin. OpenAPI is the running app's `/openapi.json`, not a published spec.
- Do not remove a behavior in a patch without a changelog note. That is the whole deprecation contract today.
