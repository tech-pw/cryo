---
title: Quickstart
description: Install Cryo locally with Docker. Clone, start the stack, seed demo Iceberg tables and open the UI in about five minutes.
canonical: https://tech-pw.github.io/cryo/quickstart/
updated: 2026-10-06
---

# Quickstart

Run a local lake with deliberate maintenance problems, then watch Cryo scan it, grade it and plan the work.

Before you start

You need Docker, Python 3.12 or newer, and Node 22 or newer. The local stack is a demo lake. It is not the production topology.

## Install and start

### Clone the repository

The default branch is stable. You are in the repo when the next prompt is inside `Cryo/`.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git
$ cd Cryo
```

### Start the local stack

This starts the API, the web UI, PostgreSQL, Redis and a demo Trino. The stack is up when the API and PostgreSQL report healthy.

**terminal**

```
$ make up
```

Done when `docker compose ps` shows the API and PostgreSQL as `healthy`.

### Seed demo tables

This creates five Iceberg tables in the local REST catalog: small files, stale manifests and expirable snapshots, so there is something to find.

**terminal**

```
$ make seed
```

Done when the log prints `Created rest.demo.customers`, `products`, `orders`, `order_items` and `shipments`.

- [http://localhost:5173](http://localhost:5173): Open the UI

## What runs locally

You use the web UI, which talks only to the API. The scanner reads the demo catalog. Nothing here needs a cloud account.

Figure 1make up runs the whole stack against a demo lake

*The local stack reads a demo catalog. Production keeps the same split with your own catalogs and engines.*

- **Read path
Text description
- You to Web UI (open)

- Web UI to API (REST)

- Scanner to Demo catalog

## What to try first

- **Home** shows the whole lake: how many tables sit at each grade, and how much storage maintenance could reclaim.

- Open **Tables**. The seeded tables already show a health grade and their small-file counts.

- Open **Runs**. The scheduler queues jobs for tables that need work, and the governor admits them as the demo Trino has headroom.

- Open any run to see its jobs and receipts: operation, engine, status and duration.

localhost:5173/

*Home after the first scan. The donut counts tables per grade. Storage impact shows what last night's run reclaimed.*

localhost:5173/tables

*Tables lists every table the scanner has seen. This view is sorted by small-file count, and each row shows the grade next to the counts that produced it.*

localhost:5173/runs/7f3c9a21

*One run, end to end. Each bar is one table's job, and every job leaves an audit row.*

Pre-1.0

The launch-tier surface is the scanner, planner, governor, Trino and Spark engines, presets, audit, and the run console. [Architecture](https://tech-pw.github.io/cryo/architecture/) shows the real component split.

## For agents

There is no chat on this site. Give an agent this prompt, then the page it asked about:

**prompt**

```
Read https://tech-pw.github.io/cryo/llms.txt, then answer only from those pages. If a fact is not on one of them, say so.
```

## Where to go next

How Cryo fitsWhat source, preset and destination mean once you look past the demo.MaintenanceThe fixed order the seeded tables run through.Health and costWhy the seeded tables show the grades they show.DeploymentMove from make up to a Kubernetes install.

Source: https://tech-pw.github.io/cryo/quickstart/
Markdown: https://tech-pw.github.io/cryo/quickstart/index.md
