---
name: cryo-install
description: Render and explain a Cryo Helm install. Use when the user asks how to install, upgrade, or check the chart. Does not install.
disable-model-invocation: true
---

# Cryo install

Explain a Cryo install. Do not run `helm install`, `helm upgrade`, or `kubectl apply`.

Read [deploy/helm/cryo/README.md](../../deploy/helm/cryo/README.md) first. Comments in [deploy/helm/cryo/values.yaml](../../deploy/helm/cryo/values.yaml) beat any flag restated here. If this skill and that file disagree, the file wins.

## What the chart is

Kubernetes 1.27+, Helm 3, an external Postgres, and amd64 nodes. The chart installs the API, worker, governor, scheduler, and scanner CronJob. It does not install Postgres, a catalog, or Trino. Redis is rendered when `redis.enabled` is true, but the chart does not inject `CRYO_REDIS_URL`. Unset, the process uses `redis://localhost:6379/0`.

`cryo:latest` and `cryo-web:latest` are placeholders, not public images. `image.pullPolicy` defaults to `Always` because a mutable `latest` tag plus `IfNotPresent` keeps a node on the cached image after a rebuild.

## Prod render refuses to start

`cryo.validateProductionImages` in [deploy/helm/cryo/templates/_helpers.tpl](../../deploy/helm/cryo/templates/_helpers.tpl) runs only when `global.env.CRYO_ENVIRONMENT` is `prod`. A `dev` render refuses nothing.

| Digest | Required when |
|---|---|
| `image.digest` | always, in prod |
| `migration.image.digest` | always, in prod |
| `native.image.digest` | `native.enabled` |
| `spark.k8s.image.digest` | `spark.k8s.enabled` |
| `web.image.digest` | `web.enabled` |
| `redis.image.digest` | `redis.enabled` |

Do not hand-edit one digest and leave the others that this gate requires. Do not point `helm upgrade` at an environment overlay. Those files are not part of the published chart.

## Check, don't install

The user runs these. You read the output.

```sh
helm lint deploy/helm/cryo
helm template cryo deploy/helm/cryo --debug
```

`helm template` checks the templates. It does not prove a cluster will accept the objects. `helm upgrade` runs `alembic upgrade head` as a pre-install/pre-upgrade hook and blocks the release until that Job finishes. A Job log that says `Running upgrade` and exits 0 is not proof the schema changed. See `/cryo-run-triage`.

## After migrate

Catalogs and engines are not Helm values. Create them with `POST /api/v1/sources` and `POST /api/v1/destinations`. Supported source types are `rest`, `hive`, `glue`, `polaris`, and `nessie`. Unity Catalog and JDBC are rejected.

## Auth

`_dev_token_enabled` is `environment != "prod" or allow_dev_token_in_prod` ([cryo/api/auth_routes.py](../../cryo/api/auth_routes.py)). `dev`, `staging`, and `test` all leave `POST /api/v1/auth/token` open. That route self-issues `viewer`, `operator`, or `admin` with no identity check. OIDC does not close it. A production install sets `CRYO_ENVIRONMENT=prod`, leaves `CRYO_ALLOW_DEV_TOKEN_IN_PROD` unset, and configures OIDC before the Service is exposed.

The Secret named by `global.envFromSecret` must hold `CRYO_DATABASE_URL` and `CRYO_SECRET_KEY`. The code default is `dev-only-change-me`. Do not reuse it.

## Two node selectors

`cryo.nodeSelector` in `_helpers.tpl` pins Cryo's own pods to `kubernetes.io/arch: amd64`. Spark driver and executor pods do not use that helper. Their selector comes from destination config or `CRYO_SPARK_K8S_NODE_SELECTOR`. `SparkK8sConfig.from_config` merges `kubernetes.io/arch=amd64` back with `setdefault` unless the caller set that key. An explicit `arm64` is kept. A capacity-type-only selector no longer drops the arch pin. See `/cryo-run-triage`.

## Local is not an install

`make up` stacks three compose files. `docker-compose.dev-datalake.yml` (MinIO, catalogs, Trino, Spark) and `docker-compose.observability.yml` are local/CI only. Do not deploy them, and do not copy their localhost URLs or console passwords into Helm values. Inventory: [docs/LOCAL_DEV_ONLY_INVENTORY.md](../LOCAL_DEV_ONLY_INVENTORY.md). [docs/operator-guide.md](../operator-guide.md) was last reconciled 2026-07-05. Later incident docs override it.

## Refuse

- Do not run `helm install`, `helm upgrade`, or `kubectl apply`.
- Do not mix the dev datalake compose file into a production install.
- Do not leave a non-prod `CRYO_ENVIRONMENT` on an API that is reachable.
- Do not create a catalog or an engine as a Helm value.
- Do not invent a values key this file does not name. Read `values.yaml`.
