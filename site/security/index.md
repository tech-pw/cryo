---
title: Security and authentication
description: Authentication, roles, the dev token, secret encryption, network expectations and how to report a vulnerability in Cryo.
canonical: https://tech-pw.github.io/cryo/security/
updated: 2026-10-26
---

# Security and authentication

Cryo runs destructive maintenance against production data and stores catalog, cloud and database credentials. This page covers who can reach it, how a request is authenticated, and what to turn on before you expose it.

## Roles

Cryo has three roles. A request carries a token; the token maps to a role; the role decides what the request may do.

| Role | Can do | Cannot do |
| --- | --- | --- |

| **Viewer** | Read tables, runs, jobs, health and cost. See the fleet dashboard. | Change anything. |

| **Operator** | Everything a viewer can, plus assign presets, start maintenance runs, and approve queued work. | Change credentials, users, or global settings. |

| **Admin** | Everything an operator can, plus manage connections, destinations, users and roles. | — |

Least privilege by default

Run the day-to-day operator with an operator token. Keep the admin token for connection changes and onboarding.

## How a request is authenticated

The API accepts three kinds of credential. Which ones are live depends on how the install is configured.

### OIDC (production)

Set `CRYO_OIDC_ISSUER_URL`, `CRYO_OIDC_CLIENT_ID` and `CRYO_OIDC_CLIENT_SECRET`. The UI redirects to your identity provider, and the API verifies the resulting token against the issuer's JWKS. Role mapping comes from the user's record in Cryo's own database — the provider authenticates, Cryo authorizes.

### Local sign-in

Password sign-in is enabled per install from **Settings → Access**, not from an environment variable. Cryo then manages its own users, with passwords hashed and stored in its own `cryo.users` table. This is fine for a single-tenant install and a step down from OIDC.

### Google sign-in

Google sign-in is also configured from **Settings → Access**. When it is enabled and no Cryo user exists yet, a Google identity listed in `CRYO_GOOGLE_ADMIN_EMAILS` becomes the first admin on first sign-in. That is how a fresh install bootstraps its first admin without a dev token.

## The dev token

The dev token is not authentication

`POST /api/v1/auth/token` returns a token for a role you name in the request body. There is no password, no identity check and no record of who asked. Anyone who can reach that endpoint can become an admin.

Cryo enables the dev token in `dev` and `test` so the UI and the end-to-end tests work without an identity provider. It is **off** in `staging` and `prod` unless you set `CRYO_ALLOW_DEV_TOKEN_IN_PROD=true`.

If you set that flag, you have chosen to run an install where anyone who can reach the API can grant themselves any role. Turn it off as soon as OIDC, local sign-in or Google sign-in works, and check it after every upgrade — the flag is opt-in, so it stays on until something turns it off.

**terminal**

```
$ kubectl -n cryo get deploy cryo-api -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CRYO_ALLOW_DEV_TOKEN_IN_PROD")].value}'
```

## Secrets at rest

Connection secrets — S3 credentials, database URLs, catalog passwords — are stored in PostgreSQL. They are encrypted with Fernet before they are written, using `CRYO_SECRET_KEY`.

CRYO_SECRET_KEY is the master key

Every stored secret is unreadable without it. The default value is `dev-only-change-me` and Cryo refuses to start in `staging` or `prod` with that value or with anything shorter than 32 characters. Set your own before the first real connection, and keep it in a secret store: anyone with the key and database access can decrypt every stored credential.

The same key signs the API's own HS256 tokens, so a leaked key means both leaked secrets and forgeable sessions. `CRYO_JWT_SECRET` overrides it for token signing alone, which separates the two risks if you need them apart.

Changing the key makes every stored secret unreadable. To roll it, re-encrypt with the old key still available, then remove the old one.

## Network and egress

Cryo is an outbound client. It reaches the catalogs it reads and the engines it runs work on, and it does not accept inbound connections from them.

| From | To | Why |
| --- | --- | --- |

| Cryo pods | PostgreSQL | State, audit and job records. |

| Cryo pods | Redis | Queue, leases and live counters. |

| Cryo pods | Catalogs (HMS, Glue, REST, Polaris, Nessie) | Reading table lists and metadata. |

| Cryo pods | Trino, Spark, EMR | Running maintenance. |

| Cryo pods | Object storage (S3) | Reading and writing Iceberg data files. |

| Browser | Cryo API and web | The only inbound path. |

Kubernetes NetworkPolicies in the chart restrict pod-to-pod traffic. The default policy allows the ingress the API and web need and denies the rest, but it does not restrict egress — an engine or a catalog on a private network has to be reachable from the pod network.

Running on EKS

Give the pods an IRSA role rather than node instance credentials. Set `serviceAccount.annotations["eks.amazonaws.com/role-arn"]` in the chart's values, and configure the S3 connection with no static keys. Without it, scans fail with `AccessDenied` well after onboarding reported success.

## Checklist before exposing an install

### Turn the dev token off

Set `CRYO_ALLOW_DEV_TOKEN_IN_PROD=false`, or leave it unset in `prod`. Confirm with the command above.

### Set a real secret key

Generate at least 32 characters, keep it in a secret store, and never commit it. Cryo refuses to start in staging and prod with the published default, but a copied one is not rejected.

### Configure OIDC, local sign-in or Google sign-in

Otherwise the only way in is the dev token.

### Require a token on the API

Endpoints that expose operational state require a viewer token, and the API's OpenAPI documentation is private in `prod`.

### Keep the ingress behind TLS

The chart's Ingress and TLS are opt-in. Terminate TLS at the ingress and register the redirect URI your provider expects in `CRYO_OIDC_REDIRECT_URI`.

### Check what the scanner reads

The scanner reads catalog metadata. It does not open data files, but it does read the object listing. Narrow the catalog permissions to the namespaces Cryo should see.

## Reporting a vulnerability

Do not open a public issue. Open a [GitHub Security Advisory](https://github.com/tech-pw/cryo/security/advisories/new) instead — it is visible only to the maintainers until a fix is published. See [SECURITY.md](https://github.com/tech-pw/cryo/blob/main/SECURITY.md) for what to include and the response targets.

## Where to go next

DeploymentInstall the chart and the resources it needs.Configuration referenceEvery environment variable and Helm value.TroubleshootingSymptoms, causes and fixes.

Source: https://tech-pw.github.io/cryo/security/
Markdown: https://tech-pw.github.io/cryo/security/index.md
