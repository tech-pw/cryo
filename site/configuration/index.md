---
title: Configuration reference
description: The full configuration reference for Cryo -- CRYO_* environment variables and deploy/helm/cryo values.
canonical: https://tech-pw.github.io/cryo/configuration/
updated: 2026-10-26
---

# Configuration reference

Every environment variable Cryo reads and every top-level Helm value the chart accepts, with the default each one takes.

Generated, not hand-written

The tables below are generated from the settings class and the chart's own values, so they cannot drift from the code. CI fails when they do. Regenerate with `make config-reference`.

## Environment variables

Cryo reads these from the environment, with the `CRYO_` prefix. A default of *set from a secret*, *unset* or *computed* means there is no safe default to print.

The full table, with every variable and its type, is in [docs/CONFIGURATION.md](https://github.com/tech-pw/cryo/blob/main/docs/CONFIGURATION.md).

| Variable | Default | What it does |
| --- | --- | --- |
| `CRYO_ENVIRONMENT` | `dev` | One of `dev`, `test`, `staging`, `prod`. Decides whether the dev token is live and whether the default secret key is rejected. |
| `CRYO_DATABASE_URL` | secret | PostgreSQL connection string. Holds the password. |
| `CRYO_REDIS_URL` | secret | Redis connection string. Holds the password. |
| `CRYO_SECRET_KEY` | secret | Fernet key for stored connection secrets, and the default HS256 signing key. At least 32 characters; the published default is rejected in staging and prod. |
| `CRYO_JWT_SECRET` | secret | Overrides the signing key for API tokens alone, leaving the encryption key separate. |
| `CRYO_OIDC_ISSUER_URL` | unset | OIDC issuer. Setting it turns on RS256 token verification. |
| `CRYO_OIDC_CLIENT_ID` | unset | OIDC client id. |
| `CRYO_OIDC_CLIENT_SECRET` | secret | OIDC client secret, for the authorization-code exchange. |
| `CRYO_OIDC_REDIRECT_URI` | unset | The redirect URI registered with the provider. The UI callback. |
| `CRYO_ALLOW_DEV_TOKEN_IN_PROD` | `false` | Keeps the dev token live in prod. Anyone who can reach the API can self-grant any role while this is true. |
| `CRYO_DISPLAY_TIMEZONE` | `UTC` | IANA zone the install schedules and reports in. Also the zone the off-peak window is read in and the UI renders instants in. |
| `CRYO_OFFPEAK_START_IST` | `0` | Hour the off-peak window opens, in the display time zone. Equal bounds disable the gate. |
| `CRYO_OFFPEAK_END_IST` | `0` | Hour the off-peak window closes. |
| `CRYO_SCAN_ENGINE` | `pyiceberg` | `go` or `pyiceberg`. The Go scanner is faster and is what a large fleet should use. |
| `CRYO_MAINTENANCE_WRITES_ENABLED` | `true` | The master switch for anything that writes to your tables. Turning it off means Cryo plans and explains but does not execute. |
| `CRYO_MAINTENANCE_COOLDOWN_HOURS` | `20` | How long a table is left alone after maintenance, so a nightly preset does not re-run the same table. |
| `CRYO_DEFAULT_RETENTION_DAYS` | `7` | Default snapshot retention window, tightened automatically for frequently-written tables. |
| `CRYO_MAX_JOBS_PER_SCHEDULED_RUN` | `500` | Caps how many tables one scheduled run may touch, so a large fleet cannot flood the queue in one tick. |

## Helm values

Top-level values in `deploy/helm/cryo/values.yaml`. The chart's schema, `deploy/helm/cryo/values.schema.json`, validates them at install time.

| Value | Default | What it does |
| --- | --- | --- |
| `image` | object | Image repository, tag and digest for the backend. Pin the digest for a production install. |
| `imagePullSecrets` | `[]` | Pull secrets for the registry. Needed when the registry is private. |
| `serviceAccount` | object | The account Cryo pods run as. On EKS, put the IRSA role's annotation here. |
| `podSecurityContext` | object | Applied to every pod. Runs as the non-root `cryo` user by default. |
| `global` | object | Environment values applied to every service, including the environment flag and the secret reference. |
| `migration` | object | The Alembic migration job. Runs on install and upgrade. |
| `api`, `worker`, `governor`, `scheduler`, `scannerWorker` | objects | Replica counts, resource requests and limits for each service. |
| `web` | object | The UI. Its image is separate from the backend's. |
| `native` | object | The native Go compactor. It ships inside the main image and is dispatched as KEDA-scaled Jobs. |
| `spark` | object | The Spark-on-Kubernetes fallback engine. |
| `scanner` | object | The scanner CronJob and its job-dispatch settings. |
| `redis` | object | The Redis image and configuration. Redis itself is external; the chart ships a client. |
| `ingress`, `networkPolicy`, `podDisruptionBudget` | objects | Opt-in hardening. Ingress and TLS are off by default; the NetworkPolicy denies pod-to-pod traffic the chart does not need. |
| `observability` | object | Metrics and tracing settings. |
| `backup` | object | Backup-related settings for the release. |

## Where to go next

Security and authenticationWho can reach the API, and how to turn the dev token off.DeploymentInstall the chart and the resources it needs.TroubleshootingSymptoms, causes and fixes.

Source: https://tech-pw.github.io/cryo/configuration/
Markdown: https://tech-pw.github.io/cryo/configuration/index.md
