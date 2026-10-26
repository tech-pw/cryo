---
title: Quickstart
description: Clone Cryo and start the local stack. Migrations run. The UI does not show a graded lake until you add a source and scan.
canonical: https://tech-pw.github.io/cryo/quickstart/
updated: 2026-10-26
---

# Quickstart

Start the local control plane. The UI comes up empty. Screenshots on this site are synthetic demo data, not the result of these commands.

Before you start

You need Docker, Python 3.12 or newer, Node 22 or newer, and curl. This is a local control plane, not the production topology.

Development only

The local stack runs with `CRYO_ENVIRONMENT=dev` and the published default `CRYO_SECRET_KEY`. Its dev sign-in hands an admin token to anyone who can reach the API. Don't expose it. For a real install, follow [Install on Kubernetes](https://tech-pw.github.io/cryo/install/) and [Security](https://tech-pw.github.io/cryo/security/).

## Install and start

### Clone the repository

The clone directory is `cryo`, lowercase.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git
$ cd cryo
```

### Start the local stack

`./start-local.sh start` creates the venv, starts Postgres, Redis, and the local datalake, runs Alembic, and starts the API and UI on the host. `make up` only starts containers. It does not migrate. `/healthz` does not touch Postgres.

**terminal**

```
$ ./start-local.sh start
```

Done when the script prints a dev sign-in role. The UI is empty. No preset is marked default, so a later scan does not schedule maintenance until you assign one.

### Seed demo tables

Optional. This writes five small tables (`rest.demo.customers`, `products`, `orders`, `order_items`, `shipments`). It does not create small files, stale manifests, or expirable snapshots, and it does not register a Cryo source or scan. It needs host PySpark, which `make install` does not install.

**terminal**

```
$ make seed
```

Done when the log prints those five table names. The dashboard still does not show them until a source exists and a scan runs.

### Open the UI

Open [http://localhost:5173](http://localhost:5173). On macOS, `open http://localhost:5173` does the same thing.

Done when the login screen loads. Dev sign-in is a role (`viewer`, `operator`, or `admin`), not an arbitrary username.

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

- **Home** is empty until a scan has stored grades. The screenshots below are synthetic demo data, not this quickstart.
- Open **Tables** after you add a source and run a scan. `make seed` alone does not put rows here.
- Open **Runs** after a preset is assigned. Nothing in this quickstart enqueues maintenance.
- Open any run to see its jobs and receipts: operation, engine, status and duration.

localhost:5173/

*Home after a few nights of maintenance. Last night's run and your next steps come first. The lake's grades, the tables people query most and the tables Cryo just fixed sit below.*

localhost:5173/tables

*Tables opens on what needs you: last night's failures and skips, then every table the scanner has seen, sorted by small-file count with the grade next to the counts that produced it.*

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

How Cryo fitsWhat source, preset and destination mean once you look past the demo.MaintenanceThe fixed order the seeded tables run through.Health and costWhy the seeded tables show the grades they show.Install on KubernetesMove from the local stack to a Kubernetes install.

Source: https://tech-pw.github.io/cryo/quickstart/
Markdown: https://tech-pw.github.io/cryo/quickstart/index.md
