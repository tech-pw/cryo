---
title: Install on Kubernetes
description: Production Helm install for Cryo. Prerequisites, the Secret, a secure values file, helm upgrade --install, migrations, verification, upgrade and uninstall.
canonical: https://tech-pw.github.io/cryo/install/
updated: 2026-10-26
---

# Install on Kubernetes

Install a released Cryo with Helm, against your own PostgreSQL and Redis, with the dev token off and every image pinned by digest. Then sign in, add a source and run the first scan.

Read Security first

This page uses the settings from [Security](https://tech-pw.github.io/cryo/security/): `CRYO_ENVIRONMENT=prod`, a random `CRYO_SECRET_KEY` of at least 32 characters, and OIDC for sign-in. Nothing here turns on the dev token.

## What you need

Kubernetes
Version 1.27 or newer (the chart's `kubeVersion` is `>=1.27.0-0`), with amd64 nodes. The published images are amd64-only, so every Cryo pod gets a `kubernetes.io/arch: amd64` node selector.
Tools
Helm 3, `kubectl`, `git`, `openssl`, and `crane` to look up image digests.
PostgreSQL
An external database and a user that owns it. Cryo keeps its tables in a schema called `cryo` in that database. The first migration runs `CREATE EXTENSION IF NOT EXISTS pgcrypto`, so either give the user that privilege or create the extension first. `pgvector` is optional. Without it, the migration logs a warning and AI search falls back to a slower path. The local stack runs PostgreSQL 16.
Redis
An external Redis for the job queue. Postgres is the source of truth, so Redis doesn't need persistence. You can use the chart's single in-namespace Redis instead (see Using the bundled Redis).
An OIDC client
A confidential client at your identity provider with redirect URI `https://<your-cryo-host>/login/callback`. Its access tokens must carry a Cryo role. Security lists the exact claims.
Network
Cryo's pods must be able to reach PostgreSQL, Redis, your catalogs, your engines (Trino or Spark), the object storage that holds the table files, and the OIDC issuer. Security has the full list.

## Install

### Pick a version and get its chart

The chart isn't in a Helm repository. Take it from the git tag that matches the image tag, so the chart and the images come from the same release. This page uses `v0.1.5`. Use the newest `v*` tag, and use it everywhere this page says `v0.1.5`.

**terminal**

```
$ export CRYO_VERSION=v0.1.5
$ git clone --depth 1 --branch "$CRYO_VERSION" https://github.com/tech-pw/cryo.git "cryo-$CRYO_VERSION"
$ cd "cryo-$CRYO_VERSION"
```

### Look up the image digests

The release images are public on GHCR: `ghcr.io/tech-pw/cryo` runs the API, workers and migrations, and `ghcr.io/tech-pw/cryo-web` is the UI. With `CRYO_ENVIRONMENT=prod`, the chart won't render until every enabled image has a `sha256` digest, so a moved tag can't change what runs.

**terminal**

```
$ export CRYO_DIGEST=$(crane digest "ghcr.io/tech-pw/cryo:$CRYO_VERSION")
$ export CRYO_WEB_DIGEST=$(crane digest "ghcr.io/tech-pw/cryo-web:$CRYO_VERSION")
$ echo "$CRYO_DIGEST $CRYO_WEB_DIGEST"
```

Done when both values start with `sha256:`.

### Write the settings file

Every Cryo process reads its `CRYO_*` settings from the Secret named by `global.envFromSecret`, and that includes the migration Job. Save the settings below as `cryo.env` and replace every placeholder. Keeping them in a file keeps them out of your shell history.

**cryo.env**

```
CRYO_DATABASE_URL=postgresql+psycopg://cryo:REPLACE_ME@postgres.example.internal:5432/cryo
CRYO_REDIS_URL=redis://redis.example.internal:6379/0
CRYO_OIDC_ISSUER_URL=https://idp.example.com/realms/data
CRYO_OIDC_CLIENT_ID=cryo
CRYO_OIDC_CLIENT_SECRET=REPLACE_ME
CRYO_OIDC_REDIRECT_URI=https://cryo.example.com/login/callback
```

`CRYO_OIDC_ISSUER_URL` must match the `iss` claim in your tokens. If your provider's access tokens use an audience other than the client ID, add `CRYO_OIDC_AUDIENCE` as well.

### Create the namespace and the Secret

Add two random keys to the file: one encrypts stored secrets, the other signs Cryo's own tokens. Then load the file into the Secret and delete it.

**terminal**

```
$ echo "CRYO_SECRET_KEY=$(openssl rand -hex 32)" >> cryo.env
$ echo "CRYO_JWT_SECRET=$(openssl rand -hex 32)" >> cryo.env
$ kubectl create namespace cryo
$ kubectl -n cryo create secret generic cryo-secrets --from-env-file=cryo.env
$ rm cryo.env
```

**Keep `CRYO_SECRET_KEY`.** It also encrypts the connection secrets Cryo stores, so back it up alongside the database. If you lose it or change it, those stored secrets can't be read. See [Security](https://tech-pw.github.io/cryo/security/#secret-key).

### Write the values file

Save this as `cryo-values.yaml`. It sets `prod`, takes both images from the release, turns on the UI and points every process at the Secret. The bundled Redis is off because `CRYO_REDIS_URL` points at your own. The digests are passed on the command line in the next step.

**cryo-values.yaml**

```
global:
  envFromSecret: cryo-secrets
  env:
    CRYO_ENVIRONMENT: prod
image:
  repository: ghcr.io/tech-pw/cryo
  tag: v0.1.5
migration:
  image:
    repository: ghcr.io/tech-pw/cryo
    tag: v0.1.5
web:
  enabled: true
  image:
    repository: ghcr.io/tech-pw/cryo-web
    tag: v0.1.5
redis:
  enabled: false
```

The native and Spark-on-Kubernetes lanes are off by default. If you turn on `native.enabled` or `spark.k8s.enabled`, pin those images by digest too. `deploy/helm/cryo/values.yaml` documents every other setting and its default.

### Render, then install

Render first. A missing digest or a typo fails at this point, before anything touches the cluster.

**terminal**

```
$ helm template cryo ./deploy/helm/cryo --namespace cryo -f cryo-values.yaml --set image.digest="$CRYO_DIGEST" --set migration.image.digest="$CRYO_DIGEST" --set web.image.digest="$CRYO_WEB_DIGEST" > /dev/null
$ helm upgrade --install cryo ./deploy/helm/cryo --namespace cryo -f cryo-values.yaml --set image.digest="$CRYO_DIGEST" --set migration.image.digest="$CRYO_DIGEST" --set web.image.digest="$CRYO_WEB_DIGEST" --wait --timeout 15m
```

Done when Helm prints `STATUS: deployed`. With the release name `cryo`, the Services are `cryo-api` on port 8000 and `cryo-web` on port 80.

## How migrations run

Every `helm install` and `helm upgrade` starts with a hook Job called `cryo-migrate-<revision>`. It runs `alembic upgrade head` with the same image and Secret. The release waits for the Job, and no Deployment changes until it succeeds. Helm deletes the Job when it succeeds and keeps it when it fails, so after a failed install you can still read the log:

**terminal**

```
$ kubectl -n cryo get jobs -l app.kubernetes.io/component=migration
$ kubectl -n cryo logs job/cryo-migrate-1
```

After a successful install, check which revision the database is on:

**terminal**

```
$ kubectl -n cryo exec deploy/cryo-api -- alembic current
```

Done when it prints one revision ending in `(head)`.

## Check the install

### Pods are ready

**terminal**

```
$ kubectl -n cryo get pods
```

Done when the api, worker, governor, scheduler and web pods are all `Running` and ready. The scanner worker can stay at zero replicas until there's scan work.

### The API answers and the dev token is off

In a second terminal, run `kubectl -n cryo port-forward svc/cryo-api 8000:8000`. `/healthz` only shows that the process is up. `/healthz/deep` checks PostgreSQL and Redis as well.

**terminal**

```
$ curl -s localhost:8000/healthz
$ curl -s localhost:8000/healthz/deep
$ curl -s localhost:8000/api/v1/auth/config
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/v1/auth/token -H 'content-type: application/json' -d '{"role":"admin"}'
```

Done when `/healthz` reports `"environment":"prod"` and `/healthz/deep` returns `"status":"ok"`. The auth config should show `"mode":"oidc"` and `"dev_token_enabled":false`, and the token request should print `503`.

### Expose the UI

The chart has no Ingress. Point your own Ingress or load balancer at the `cryo-web` Service on port 80, using the host from `CRYO_OIDC_REDIRECT_URI`. The web pod serves the UI and forwards `/api/` to the API, so you only need one host. Ask Cryo streams server-sent events, so turn proxy buffering off and allow a read timeout of at least 60 seconds.

**cryo-ingress.yaml**

```
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cryo
  namespace: cryo
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
spec:
  ingressClassName: nginx
  tls:
    - hosts: [cryo.example.com]
      secretName: cryo-tls
  rules:
    - host: cryo.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: cryo-web
                port:
                  number: 80
```

These annotations are for ingress-nginx, so use your controller's equivalents if you run a different one. The web pod also forwards `/metrics` and `/healthz/deep`, and neither needs a login. If the host can be reached from outside your network, block both at the Ingress.

### Sign in

Open `https://cryo.example.com` and sign in through your identity provider. You need the `admin` role in your access token to add sources. If `/api/v1/auth/config` returns 503 with `OIDC issuer discovery failed`, the API can't reach `CRYO_OIDC_ISSUER_URL/.well-known/openid-configuration`.

Done when Home loads. It stays empty until the first scan.

### Add a source and run the first scan

Follow [Connect a lakehouse](https://tech-pw.github.io/cryo/onboarding/) to add a catalog and start a scan. The scanner CronJob also scans every source every six hours, as set by `scanner.schedule`.

Done when Tables lists your tables with grades.

## Using the bundled Redis

When `redis.enabled` is `true`, which is the chart default, the chart runs one Redis pod with no volume behind a ClusterIP Service called `cryo-redis`. The chart doesn't set `CRYO_REDIS_URL` for you, so put `CRYO_REDIS_URL=redis://cryo-redis:6379/0` in the Secret. In `prod`, the Redis image needs a digest too:

**terminal**

```
$ export REDIS_DIGEST=$(crane digest redis:7-alpine)
$ helm upgrade --install cryo ./deploy/helm/cryo --namespace cryo -f cryo-values.yaml --set image.digest="$CRYO_DIGEST" --set migration.image.digest="$CRYO_DIGEST" --set web.image.digest="$CRYO_WEB_DIGEST" --set redis.enabled=true --set redis.image.digest="$REDIS_DIGEST" --wait --timeout 15m
```

## Upgrade

Back up the database first, because migrations only move forward. Then repeat the install with the new tag: check out its chart, look up its digests, change the three `tag` values in `cryo-values.yaml`, render, and run `helm upgrade --install`. The migration hook runs before any pod changes.

## Uninstall

**terminal**

```
$ helm uninstall cryo --namespace cryo
```

This removes the Deployments, Services, CronJobs, ServiceAccount and RBAC that the release created. These are left behind:

- **The database.** The `cryo` schema stays in PostgreSQL, with its run history, audit rows and stored connections. Drop it yourself if you want them gone.

- **Redis keys** in your external Redis. The bundled Redis has no volume, so its data goes when its pod does.

- **The Secret** `cryo-secrets` and the namespace, because you created them outside the chart.

- **Runtime Jobs** started by the native or Spark lanes. They aren't part of the release and are removed through `ttlSecondsAfterFinished`. Check with `kubectl -n cryo get jobs,pods`.

- **Changes to your tables.** Maintenance that already ran is permanent. Compacted files and expired snapshots don't come back.

## Where to go next

SecurityRoles, OIDC claims, the dev token and a hardening checklist.DeploymentWhat the release runs and what it requests.Connect a lakehouseAdd the first source and run the first scan.

Source: https://tech-pw.github.io/cryo/install/
Markdown: https://tech-pw.github.io/cryo/install/index.md
