---
title: Deployment
description: How Cryo deploys on Kubernetes with Helm, what it needs from you, and the committed CPU and memory request floor.
canonical: https://tech-pw.github.io/cryo/deployment/
updated: 2026-10-26
---

# Deployment

Cryo does not replace Trino or Apache Spark. It plans maintenance and admits the work to the engines you already run. Deploy it as a Helm chart and keep your engines where they are.

## Cryo on Kubernetes

Figure 1Helm installs Cryo; PostgreSQL, Redis and the engines stay yours

*The Helm release holds Cryo's services. PostgreSQL and Redis are yours, and so are the engines.*

- **Write or execute
Text description
- Cryo Helm release to Required stores (state)
- Cryo Helm release to Engines you already run (execute)

The scheduler and the governor are leader-elected, so several replicas may start but only one is active. Scaling workers does not by itself raise `OPTIMIZE` concurrency, because the governor bounds real concurrency against engine load.

## Layout in three lines

**Helm chart**

The chart lives at `deploy/helm/cryo` and each release tag publishes it to `oci://ghcr.io/tech-pw/charts/cryo`, signed with cosign keyless. Defaults are committed, and overrides are per environment.

**Stores**

PostgreSQL and Redis are required. PostgreSQL is always external, so you can use a managed service. Redis can be external too, or you can use the chart's single in-namespace Redis, which has no volume.

**Engines**

Engines stay outside the chart. Cryo reaches them through source and destination configs stored in the database.

## Install the chart

No clone needed. The chart version `X.Y.Z` is the release tag without its `v`, and the published defaults pin that release's `ghcr.io/tech-pw/cryo`, `cryo-web` and `cryo-spark` images by digest.

**terminal**

```
$ helm install cryo oci://ghcr.io/tech-pw/charts/cryo --version X.Y.Z --namespace cryo --create-namespace
```

The chart README lists the values a production install must set, including the database connection, the production environment flag, and OIDC.

## Request floor

These are the resources the committed Helm defaults request when Cryo is idle, summed across replicas. `scannerWorker` is the ad-hoc scan consumer, not the scanner CronJob.

| Service | CPU (cores) | Memory (GiB) |
| --- | --- | --- |
| `api` | 0.50 | 1.50 |
| `worker` | 1.00 | 2.00 |
| `governor` | 0.25 | 0.50 |
| `scheduler` | 0.10 | 0.50 |
| `scannerWorker` | 1.50 | 1.50 |
| Total | 3.35 | 6.00 |

Reproduce the table from the chart defaults:

**terminal**

```
$ make deployment-footprint
```

Chart defaults are not a production install

This page describes the shape of a deployment. The chart's defaults and the local stack are for development only. A default install has no OIDC and uses the published default `CRYO_SECRET_KEY`. Follow [Install on Kubernetes](https://tech-pw.github.io/cryo/install/) for a production install, and check [Security](https://tech-pw.github.io/cryo/security/) before anyone else can reach it.

## Where to go next

Install on KubernetesA pinned, production Helm install, step by step.SecuritySign-in, roles, secrets and a hardening checklist.Upgrade and rollbackMove between releases, and what a rollback undoes.Backup and restoreWhat to back up, and a restore you can practise.ArchitectureThe system context these workloads implement.EnginesHow the same plan renders as Trino or Spark SQL.QuickstartRun the same components locally with Docker first.ReferencePublic API surfaces and what is in the box.

Source: https://tech-pw.github.io/cryo/deployment/
Markdown: https://tech-pw.github.io/cryo/deployment/index.md
