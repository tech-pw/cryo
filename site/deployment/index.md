---
title: Deployment
description: How Cryo deploys on Kubernetes with Helm, what it needs from you, and the committed CPU and memory request floor.
canonical: https://tech-pw.github.io/cryo/deployment/
updated: 2026-10-05
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

Helm chart
The chart lives at deploy/helm/cryo. Defaults are committed, and overrides are per environment.
Stores
PostgreSQL and Redis are required. Both are external to the chart, so you can bring managed services.
Engines
Engines stay outside the chart. Cryo reaches them through source and destination configs stored in the database.

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

Public shape, not a runbook

This page describes the shape of a deployment. It is not a production runbook.

## Where to go next

ArchitectureThe system context these workloads implement.EnginesHow the same plan renders as Trino or Spark SQL.QuickstartRun the same components locally with Docker first.ReferencePublic API surfaces and what is in the box.

Source: https://tech-pw.github.io/cryo/deployment/
Markdown: https://tech-pw.github.io/cryo/deployment/index.md
