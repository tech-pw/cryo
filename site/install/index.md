---
title: Install on Kubernetes
description: A production install guide for Cryo on Kubernetes -- prerequisites, secrets, the Helm values that matter, and what to check afterwards.
canonical: https://tech-pw.github.io/cryo/install/
updated: 2026-10-26
---

# Install on Kubernetes

Cryo ships as a Helm chart. This page takes you from nothing to a running install with a catalog connected and a maintenance run on a real table.

What the chart does and does not install

The chart installs Cryo's services: the API, the worker, the governor, the scheduler, the scanner and the web UI. It does **not** install PostgreSQL, a catalog or your engines. You bring those, and Cryo reaches them over the network.

## Prerequisites

| You need | Why |
| --- | --- |

| Kubernetes 1.27 or newer | The chart's APIs. |

| Helm 3.8 or newer | OCI chart installs. |

| An external PostgreSQL | Cryo's durable state. Cryo does not manage the database. |

| Redis | The queue, the leader leases and the live counters. |

| A reachable catalog | REST, Hive Metastore, Glue, Polaris or Nessie. |

| A reachable engine | Trino, or Spark on Kubernetes. |

| Object storage | Where the Iceberg data lives. |

## Install

Every release tag publishes the chart to `oci://ghcr.io/tech-pw/charts/cryo`. The chart version is the release tag without its `v` — tag `v0.1.6` is chart `0.1.6` — and the published defaults pin that release's images by digest.

**terminal**

```
$ helm install cryo oci://ghcr.io/tech-pw/charts/cryo --version 0.1.6 \
--namespace cryo --create-namespace \
--set global.envFromSecret=cryo-secrets \
--set image.digest=sha256:... \
--set migration.image.digest=sha256:...
```

The chart is signed with cosign keyless. Verify it before you install it:

**terminal**

```
$ cosign verify ghcr.io/tech-pw/charts/cryo:0.1.6 \
--certificate-identity-regexp '^https://github.com/tech-pw/cryo/\.github/workflows/release-supply-chain\.yml@refs/tags/v' \
--certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Create the secret

Cryo reads its credentials from a Kubernetes Secret, named by `global.envFromSecret`. The secret needs at least a database URL and a secret key; the rest depends on what you connect.

**terminal**

```
$ kubectl -n cryo create secret generic cryo-secrets \
--from-literal=CRYO_DATABASE_URL='postgresql+psycopg://cryo:...@postgres.example/cryo' \
--from-literal=CRYO_REDIS_URL='redis://redis.example:6379/0' \
--from-literal=CRYO_SECRET_KEY="$(openssl rand -base64 48)"
```

CRYO_SECRET_KEY cannot be changed casually

It encrypts every stored connection secret. Change it without re-encrypting and every stored credential becomes unreadable. Keep it in a secret manager, separate from the database it protects.

## The values that matter

A default install is not a production install. These are the ones to set.

| Value | Default | Set it to |
| --- | --- | --- |

| `global.envFromSecret` | `""` | The name of your Secret. |

| `global.env.CRYO_ENVIRONMENT` | `prod` | Leave it. Set `dev` only for a throwaway local install. |

| `image.digest`, `migration.image.digest` | set in the published chart | Your digest, if you build your own images. |

| `web.enabled` | `false` | `true` once you have a web image and set `web.image`. |

| `ingress.enabled` | `false` | `true`, with TLS. Sign-in passwords and session tokens cross that hop. |

| `networkPolicy.enabled` | `false` | `true`, and add your database, catalog, storage and engines to the egress rules. |

| `podDisruptionBudget.enabled` | `false` | `true` so a node drain does not take the API down with it. |

| `serviceAccount.annotations` | `{}` | On EKS, the IRSA role's `eks.amazonaws.com/role-arn`. |

| `scheduler.resources.requests.memory` | small | Near what you actually see — the scheduler has been observed at about 4.7 GiB on a large fleet. |

The full list is in [Configuration](https://tech-pw.github.io/cryo/configuration/).

## After the install

The chart runs `alembic upgrade head` as a pre-install and pre-upgrade hook, and blocks the release until it finishes. Check that it did:

**terminal**

```
$ kubectl -n cryo get pods
$ kubectl -n cryo logs -l app.kubernetes.io/name=cryo -l app.kubernetes.io/component=api --tail=20
```

Then create a source. Catalogs and engines are not Helm values — they are rows in Cryo's own database, created through the API or the UI:

**terminal**

```
$ curl -X POST http://<api>/api/v1/sources \
-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
-d '{"name":"prod-lake","type":"rest","config":{"uri":"http://catalog:8181"},"secret_ref":"..."}'
```

Supported source types are `rest`, `hive`, `glue`, `polaris` and `nessie`. Unity Catalog and JDBC are rejected.

### Run the first scan

The scan reads catalog metadata and grades every table it finds. It does not open data files, but it does list the table's objects, so scope the source's credentials to the namespaces Cryo should see.

When the scan finishes, open the Tables page. Every table has a grade from A to F, and the grade says what to do about it.

### Run the first maintenance

Create a preset — a schedule bound to a destination — and assign it to a table, or let the default preset catch it. Then start a run.

Start with one table, not a fleet. Watch the run's receipt: it shows the plan, the operations, the SQL the engine received, and what happened. If the plan surprises you, fix the preset before you widen it.

Turn writes on deliberately

Nothing writes to your tables until you allow it. A preset with maintenance writes disabled still plans and still shows you exactly what it would do — which is the safest way to review a plan.

## Sizing

The chart's committed requests are an idle floor, not a steady state. On a long-running install with thousands of tables, the scheduler has been seen at about 4.7 GiB and an API replica at about 2.5 GiB. Watch `container_memory_working_set_bytes` for a few days and set the requests near what you see, so the scheduler is not evicted first under node pressure.

Worker replicas are not how you raise `OPTIMIZE` concurrency. The governor bounds real concurrency against live engine load; more workers only drain the queue faster.

## Uninstall

**terminal**

```
$ helm uninstall cryo --namespace cryo
```

Uninstalling does not drop the external database or touch object storage. Your tables, jobs and audit history stay exactly where they were.

## Where to go next

DeploymentThe shape of a deployment and the request floor.Security and authenticationWhat to turn on before you expose the install.Upgrade and rollbackMoving between releases, and what cannot be rolled back.

Source: https://tech-pw.github.io/cryo/install/
Markdown: https://tech-pw.github.io/cryo/install/index.md
