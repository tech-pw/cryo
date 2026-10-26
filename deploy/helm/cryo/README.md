# Cryo

Self-hosted Apache Iceberg maintenance control plane. This chart installs the API, worker, governor, scheduler, and scanner CronJob. It does not install Postgres, a catalog, or Trino.

## Prerequisites

- Kubernetes 1.27+
- Helm 3
- An external Postgres database
- `amd64` nodes (`kubernetes.io/arch: amd64`)
- Pull access to `ghcr.io` (the release images are public), or your own registry if you build the images yourself

Redis is rendered when `redis.enabled` is true. The chart does not inject `CRYO_REDIS_URL`. Set that in the Secret below, or the app default `redis://localhost:6379/0` is what the process uses.

## Install

Every release tag publishes this chart to `oci://ghcr.io/tech-pw/charts/cryo`. Helm 3.8+ installs it with no clone:

```sh
helm install cryo oci://ghcr.io/tech-pw/charts/cryo --version <version> \
  --namespace cryo --create-namespace
helm show values oci://ghcr.io/tech-pw/charts/cryo --version <version>
```

The chart version is the release tag without its `v`: tag `v0.1.6` is chart `0.1.6`, and `appVersion` matches. The release workflow stamps the packaged `values.yaml` so `image`, `migration.image`, `native.image`, `web.image` and `spark.k8s.image` point at `ghcr.io/tech-pw/cryo`, `cryo-web` and `cryo-spark` for that tag, pinned by digest. The committed `values.yaml` in this repo still uses the local `cryo:latest` placeholders. See `scripts/stamp_helm_chart_release.sh`.

The published chart is signed with cosign keyless by the release workflow. Verify it before installing:

```sh
cosign verify ghcr.io/tech-pw/charts/cryo:<version> \
  --certificate-identity-regexp '^https://github.com/tech-pw/cryo/\.github/workflows/release-supply-chain\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

### From a clone

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

A clone install uses the committed placeholders. Set `image.repository`, `image.tag` or `image.digest`, and the matching `migration.image` values to images you can pull.

Do not point this command at an environment overlay. Those files are not part of the published chart.

## Required configuration

A default install is not a production install.

| Key | Default | Set this |
|---|---|---|
| `image.repository` | `ghcr.io/tech-pw/cryo` in the published chart, `cryo` in a clone | Your image repository, if you build your own |
| `image.tag` or `image.digest` | The release tag and digest in the published chart, `latest` / empty in a clone | A tag or digest you published, if you build your own |
| `global.envFromSecret` | `""` | Name of a Secret that holds `CRYO_DATABASE_URL` |
| `global.env.CRYO_ENVIRONMENT` | `prod` | `dev` only for a throwaway local install |
| `web.enabled` | `false` | `true` only after you publish a web image and set `web.image`. The web Deployment injects `CRYO_API_UPSTREAM` (API Service FQDN) and `NGINX_ENTRYPOINT_LOCAL_RESOLVERS` so nginx uses the pod nameserver, not kube-dns |

The Secret must also hold `CRYO_SECRET_KEY`: a random value of at least 32 characters. In `staging` and `prod`, Cryo refuses to start without one.

In `prod`, `POST /api/v1/auth/token` (self-issued roles, no identity check) is off. Configure OIDC before the Service is exposed. In `dev` the route is open until an OIDC issuer is set.

Google sign-in is the other way off the dev token. Under Settings > Access & policy, paste a Google OAuth Client ID, test it, enable it, and add at least one admin by Google email. The sign-in page then shows "Sign in with Google" in every auth mode. By default only people with a Cryo user can sign in. Once sign-in is restricted to a domain, you can switch on "Give new Google accounts viewer access": anyone on that domain then gets a viewer user on their first sign-in, and an admin raises their role under People. Role changes and disables apply to signed-in sessions within a minute, and the Access page lists recent access changes. A user added without a password signs in with Google only, and its username must be the Google email. To bootstrap the first admin without the dev token, set `CRYO_GOOGLE_ADMIN_EMAILS` to a comma-separated list. Those emails become admins on their first Google sign-in if no Cryo user exists for them. The list never changes an existing user's role. Turn `CRYO_ALLOW_DEV_TOKEN_IN_PROD` off only after an admin has signed in with Google.

Catalogs and engines are not Helm values. Create them with `POST /api/v1/sources` and `POST /api/v1/destinations` after migrate. Supported source types are `rest`, `hive`, `glue`, `polaris`, and `nessie`. Unity Catalog and JDBC are rejected.

## Production hardening

These are off by default unless noted. Turn them on in your values file.

| Key | Default | What it does |
|---|---|---|
| `scheduler.probe`, `worker.probe`, `scannerWorker.probe` | on | Readiness and liveness on each daemon's `/healthz`. It fails after 5 consecutive failed loop ticks, or when one tick runs longer than `tickStallSeconds` (scheduler and scanner-worker 3600, worker 0 = off because one worker tick can wait on an engine for hours) |
| `scannerWorker.autoscaling.targetMemoryUtilizationPercentage` | `null` | Memory target for the scanner-worker HPA. Off because idle CPython heaps stay above the request and pin the HPA at `maxReplicas`. CPU, or `keda.enabled`, drives scaling |
| `podDisruptionBudget.enabled` | `false` | `maxUnavailable: 1` PDBs for api, web, worker, scanner-worker and governor |
| `networkPolicy.enabled` | `false` | Redis accepts only this release's pods. `networkPolicy.api.from` limits who reaches the API. A non-empty `networkPolicy.egress` limits all Cryo egress to DNS, the release and those rules, so add your database, catalog, object storage and engines |
| `ingress.enabled` | `false` | Ingress to the web UI (or the API when `web.enabled` is false), with `ingress.tls`. `api.ingressAnnotations` (SSE settings) are merged in |

Terminate TLS at the Ingress or the load balancer. Sign-in passwords, Google ID tokens and session JWTs cross that hop.

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: cryo.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: cryo-tls
      hosts:
        - cryo.example.com
```

### Sizing

The committed requests are an idle floor, not steady state. On a long-running install with thousands of tables, the scheduler has been seen at about 4.7 GiB and an API replica at about 2.5 GiB. Watch `container_memory_working_set_bytes` for a few days and set `scheduler.resources.requests.memory` and `api.resources.requests.memory` near what you see, so the scheduler is not evicted first under node pressure.

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
