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

The chart defaults to `CRYO_ENVIRONMENT=prod`, so a bare render fails until every enabled
image has a digest. A throwaway local install sets `dev` explicitly:

```sh
helm template cryo deploy/helm/cryo --set global.env.CRYO_ENVIRONMENT=dev
helm upgrade --install cryo deploy/helm/cryo \
  --namespace cryo --create-namespace \
  --set global.env.CRYO_ENVIRONMENT=dev
```

A real install pins digests and points at a Secret instead:

```sh
helm upgrade --install cryo deploy/helm/cryo \
  --namespace cryo --create-namespace \
  --set image.digest=sha256:... --set migration.image.digest=sha256:... \
  --set redis.image.digest=sha256:... \
  --set global.envFromSecret=cryo-secrets
```

Do not point this command at an environment overlay. Those files are not part of the published chart.

## Required configuration

A default install is not a production install.

| Key | Default | Set this |
|---|---|---|
| `image.repository` | `cryo` | Your image repository |
| `image.tag` or `image.digest` | `latest` / empty | A tag or digest you published |
| `global.envFromSecret` | `""` | Name of a Secret that holds `CRYO_DATABASE_URL` |
| `global.env.CRYO_ENVIRONMENT` | `prod` | `dev` only for a throwaway local install |
| `web.enabled` | `false` | `true` only after you publish a web image and set `web.image`. The web Deployment injects `CRYO_API_UPSTREAM` (API Service FQDN) and `NGINX_ENTRYPOINT_LOCAL_RESOLVERS` so nginx uses the pod nameserver, not kube-dns |

The Secret must also hold `CRYO_SECRET_KEY`: a random value of at least 32 characters. In `staging` and `prod`, Cryo refuses to start without one.

In `prod`, `POST /api/v1/auth/token` (self-issued roles, no identity check) is off. Configure OIDC before the Service is exposed. In `dev` the route is open until an OIDC issuer is set.

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
