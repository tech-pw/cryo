# Cryo

Self-hosted Apache Iceberg maintenance control plane. This chart installs the API, worker, governor, scheduler, and scanner CronJob. It does not install Postgres, a catalog, or Trino.

## Prerequisites

- Kubernetes 1.27+
- Helm 3
- An external Postgres database
- `amd64` nodes (`kubernetes.io/arch: amd64`)
- A container registry you can pull from. `cryo:latest` and `cryo-web:latest` are placeholders, not public images

Redis is rendered when `redis.enabled` is true. The chart does not inject `CRYO_REDIS_URL`. Set that in the Secret below, or the app default `redis://localhost:6379/0` is what the process uses.

## Install

`helm template` does not need a cluster. `helm upgrade --install` does.

```sh
helm template cryo deploy/helm/cryo
helm upgrade --install cryo deploy/helm/cryo \
  --namespace cryo --create-namespace
```

Do not point this command at an environment overlay. Those files are not part of the published chart.

## Required configuration

A default install is not a production install.

| Key | Default | Set this |
|---|---|---|
| `image.repository` | `cryo` | Your image repository |
| `image.tag` or `image.digest` | `latest` / empty | A tag or digest you published |
| `global.envFromSecret` | `""` | Name of a Secret that holds `CRYO_DATABASE_URL` |
| `global.env.CRYO_ENVIRONMENT` | `dev` | `prod` before the API is reachable |
| `web.enabled` | `false` | `true` only after you publish a web image and set `web.image`. The web Deployment injects `CRYO_API_UPSTREAM` (API Service FQDN) and `NGINX_ENTRYPOINT_LOCAL_RESOLVERS` so nginx uses the pod nameserver, not kube-dns |

The Secret must also hold `CRYO_SECRET_KEY`. The code default is `dev-only-change-me`. Leaving it unset lets HS256 tokens signed with that published default pass.

`CRYO_ENVIRONMENT: dev` leaves `POST /api/v1/auth/token` open. That route self-issues `viewer`, `operator`, or `admin` with no identity check. A production install sets `CRYO_ENVIRONMENT=prod`, leaves `CRYO_ALLOW_DEV_TOKEN_IN_PROD` unset, and configures OIDC before the Service is exposed. OIDC does not turn the dev token off.

Google sign-in is the other way off the dev token. Under Settings > Access & policy, paste a Google OAuth Client ID, test it, enable it, and add at least one admin by Google email. The sign-in page then shows "Sign in with Google" in every auth mode. By default only people with a Cryo user can sign in. Once sign-in is restricted to a domain, you can switch on "Give new Google accounts viewer access": anyone on that domain then gets a viewer user on their first sign-in, and an admin raises their role under People. Role changes and disables apply to signed-in sessions within a minute, and the Access page lists recent access changes. A user added without a password signs in with Google only, and its username must be the Google email. To bootstrap the first admin without the dev token, set `CRYO_GOOGLE_ADMIN_EMAILS` to a comma-separated list. Those emails become admins on their first Google sign-in if no Cryo user exists for them. The list never changes an existing user's role. Turn `CRYO_ALLOW_DEV_TOKEN_IN_PROD` off only after an admin has signed in with Google.

Catalogs and engines are not Helm values. Create them with `POST /api/v1/sources` and `POST /api/v1/destinations` after migrate. Supported source types are `rest`, `hive`, `glue`, `polaris`, and `nessie`. Unity Catalog and JDBC are rejected.

## Upgrade

`helm upgrade` runs `alembic upgrade head` as a `pre-install,pre-upgrade` hook and blocks the release until that Job finishes. It does not delete external Postgres or the warehouse.

## Uninstall

```sh
helm uninstall cryo --namespace cryo
```

Uninstall does not drop the external database or object storage.

## Render without a cluster

```sh
helm template cryo deploy/helm/cryo
```

That checks the templates. It does not prove the Kubernetes APIs on a cluster accept the objects.
