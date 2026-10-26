---
title: Security
description: Cryo authentication and production hardening. OIDC setup and role claims, the viewer, operator and admin roles, the dev token, CRYO_SECRET_KEY, stored credentials, egress, and a hardening checklist.
canonical: https://tech-pw.github.io/cryo/security/
updated: 2026-10-26
---

# Security

How people sign in to Cryo, what each role can do, which secrets Cryo keeps and how they're protected, and what to check before an install is reachable.

Local defaults are for development only

The local stack and an unconfigured install run with `CRYO_ENVIRONMENT=dev` and the published default `CRYO_SECRET_KEY`. In that state anyone who can reach the API can mint an admin token. Use the [hardening checklist](#checklist) before an install is reachable by anyone else.

## How sign-in works

Every API route needs a bearer token, except the sign-in routes and the health checks. Cryo accepts two kinds of token:

**Tokens from your identity provider (OIDC)**

The UI sends you to your provider, and the API swaps the authorization code for the provider's access token (PKCE is supported). Cryo then checks each request's token against the provider's JWKS. This is the production sign-in.

**Tokens Cryo signs itself (HS256)**

These are issued by password sign-in for local users, by Google sign-in, and by the dev token route. They're signed with `CRYO_JWT_SECRET`, or with `CRYO_SECRET_KEY` when that's unset, and they last 24 hours.

On the API Service, `/healthz`, `/readyz` and `/healthz/deep` need no token, and `/metrics` needs one only when `CRYO_METRICS_BEARER_TOKEN` is set. The web UI does not proxy `/healthz/deep` or `/metrics`; admins see them through `/api/v1/ops/`. Keep the API Service off the public internet; see [Install](https://tech-pw.github.io/cryo/install/#verify).

## OIDC setup

Register Cryo as a confidential client with the authorization-code flow, then set these in the Secret that `global.envFromSecret` names:

| Setting | What it is |
| --- | --- |
| `CRYO_OIDC_ISSUER_URL` | The issuer. Cryo reads `/.well-known/openid-configuration` under it, and tokens must carry exactly this `iss`, without a trailing slash. |
| `CRYO_OIDC_CLIENT_ID` | The client ID. OIDC is on only when the issuer and the client ID are both set. |
| `CRYO_OIDC_CLIENT_SECRET` | Used for the code exchange and refresh. Without it, sign-in can't complete. |
| `CRYO_OIDC_REDIRECT_URI` | `https://<your-cryo-host>/login/callback`. Register the same URI at the provider. The default is `http://localhost:5173/login/callback`. |
| `CRYO_OIDC_AUDIENCE` | Optional. The `aud` that access tokens must carry. Defaults to the client ID. |
| `CRYO_OIDC_JWKS_URI` | Optional. Overrides the JWKS URL from discovery. |

The provider's **access token** must be a JWT signed with RS256, RS384, RS512, ES256, ES384 or ES512, and it must carry `exp` and `sub`. Some providers issue opaque access tokens or use another audience by default. Configure yours to issue a JWT access token for Cryo's audience.

### Role mapping

Cryo reads roles from the `role`, `roles`, `scope`, `scopes` and `groups` claims. Each claim can be a string separated by spaces or commas, or a list. Only the exact role names count, in any letter case: `viewer`, `operator`, `admin` and `evidence_collector`. A group called `cryo-admins` doesn't map to anything, so have your provider emit the role name itself. A valid token with no recognized role is rejected with 401.

## Roles

| Role | Can do |
| --- | --- |
| `viewer` | Read catalogs, tables, runs and health. Can't start maintenance. |
| `operator` | Everything a viewer can, plus run maintenance. Can't change users, sign-in or connections. |
| `admin` | Everything an operator can, plus sources, destinations, presets and their rules, per-table parameter overrides, local users, sign-in settings and integration settings. |
| `evidence_collector` | A service role for recording deployed layout-conformance evidence. It doesn't include viewer rights, and admin doesn't include it. |

The first admin has to come from your identity provider, because only an admin can create local users. After that, an admin can add password users and turn on Google sign-in under Settings. Password sign-in is on by default, but it does nothing until a local user exists. Turn it off in Settings if you sign in only through OIDC.

## The dev token

`POST /api/v1/auth/token` issues a `viewer`, `operator` or `admin` token to anyone who asks, with no identity check. It exists for the local stack and for tests. When it's off, it returns 503.

| Setting | Dev token |
| --- | --- |
| `CRYO_ENVIRONMENT=prod` | Off |
| `dev`, `test` or `staging` | On, even when OIDC is configured |
| `CRYO_ALLOW_DEV_TOKEN_IN_PROD=true` | On in every environment |

Stricter defaults are on the way

A pending change ([#213](https://github.com/tech-pw/cryo/pull/213)) turns the dev token on only in `dev` or `test` with no OIDC issuer set, makes `prod` the chart default, and stops every process in `staging` or `prod` from starting when `CRYO_SECRET_KEY` or `CRYO_JWT_SECRET` is the default or shorter than 32 characters. The checklist below is already safe under both the current and the new rules.

To confirm it's off, `GET /api/v1/auth/config` should report `"dev_token_enabled":false`, and a `POST` to the token route should return 503.

## CRYO_SECRET_KEY and token lifetime

`CRYO_SECRET_KEY` does two jobs:

- It signs Cryo's own HS256 tokens, unless `CRYO_JWT_SECRET` is set.
- It's the encryption key for the connection secrets Cryo stores. Cryo derives a Fernet key from its SHA-256 hash.

The code default is `dev-only-change-me`. Because that value is published, a deployment that keeps it accepts tokens anyone can forge, and anyone with a copy of the database can decrypt its stored secrets. Generate a random value of at least 32 characters, for example with `openssl rand -hex 32`.

Set `CRYO_JWT_SECRET` to a separate random value, so you can rotate token signing without touching stored secrets.

**Rotating `CRYO_JWT_SECRET`**

Every HS256 token stops working at once, and people sign in again. Cryo can't revoke a single HS256 token, so rotation is the way to end every session. Tokens from your identity provider aren't affected. Their lifetime and refresh follow your provider's settings.

**Rotating `CRYO_SECRET_KEY`**

There's no re-encryption tool. After a change, every stored secret fails to decrypt until an admin enters it again. Rotate only when you have to, and keep the old key until every secret has been re-entered. If `CRYO_JWT_SECRET` is unset, this rotation also ends every HS256 session.

## What Cryo stores

These are encrypted in PostgreSQL with the key above, stored with an `enc:` prefix:

- the secret part of each source and destination (`secret_ref`), such as an S3 secret access key
- AWS credentials for cost insights and S3 access logs
- API keys for the AI providers
- Slack and other webhook URLs

Everything else in a source or destination `config` is stored as plain JSON. Keep credentials out of `config`. Better still, give the pods an IAM role through `serviceAccount.annotations` (IRSA on EKS), so no static AWS key is stored at all.

Local users' passwords are stored as salted PBKDF2-SHA256 hashes. The database URL, the Redis URL, the OIDC client secret and the keys themselves never go into the database. They live in the Kubernetes Secret.

## Network and egress

Cryo has no inbound path except the API, and the UI's web pod in front of it. Its pods make outbound connections to:

- PostgreSQL and Redis
- your catalogs: Iceberg REST, Hive Metastore (Thrift), AWS Glue, Polaris or Nessie
- your engines: Trino over HTTP, and Spark. Spark on Kubernetes and the native lanes create Jobs through the Kubernetes API.
- the object storage that holds the table files, plus AWS APIs such as STS, Glue and cost data when you use them
- the OIDC issuer for discovery, JWKS and token exchange, and `oauth2.googleapis.com` if Google sign-in is on
- Slack or webhook URLs, if you configure notifications
- an AI provider, if you configure one. `api.openai.com` and `*.openai.azure.com` are allowed. Any other OpenAI-compatible origin must be listed exactly in `CRYO_LLM_ALLOWED_ORIGINS`.

A default-deny egress NetworkPolicy that opens only these destinations is a good fit. The chart doesn't ship one.

## Production hardening checklist

- `CRYO_ENVIRONMENT=prod` is set in `global.env`.
- `CRYO_ALLOW_DEV_TOKEN_IN_PROD` is unset or `false`.
- `CRYO_SECRET_KEY` is a random value of at least 32 characters, it's stored in a Secret, and it's backed up with the database.
- `CRYO_JWT_SECRET` is set to a different random value.
- OIDC is configured, the access token carries a Cryo role, and you've signed in as an admin through it.
- `/api/v1/auth/config` reports `"dev_token_enabled":false`, and `POST /api/v1/auth/token` returns 503.
- Password sign-in is off in Settings unless you need local users.
- Every image is pinned by digest. In `prod`, the chart enforces this.
- The UI is served over TLS, the API Service is not exposed outside your network, and `CRYO_METRICS_BEARER_TOKEN` is set if anything outside the cluster can reach `/metrics`.
- Credentials are in each connection's secret field or come from an IAM role, never from its `config`.
- Egress is limited to the destinations above.

## Reporting a vulnerability

Don't open a public issue with details. Follow [SECURITY.md](https://github.com/tech-pw/cryo/blob/main/SECURITY.md).

## Where to go next

Install on KubernetesA production install with these settings.DeploymentWhat the release runs and what it requests.ConfigurationEvery environment variable and Helm value.

Source: https://tech-pw.github.io/cryo/security/
Markdown: https://tech-pw.github.io/cryo/security/index.md
