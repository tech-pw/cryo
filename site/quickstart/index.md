---
title: Quickstart
description: Clone Cryo, start the local stack, seed fragmented Iceberg tables, scan them, assign a preset and run maintenance. make demo does all of it in one command.
canonical: https://tech-pw.github.io/cryo/quickstart/
updated: 2026-11-03
---

# Quickstart

Start the local control plane, seed fragmented tables, and finish with graded tables and one completed maintenance run. Plan on about 15 minutes the first time, most of it image downloads.

Before you start

You need Docker, Python 3.12 or newer, Node 22 or newer, and curl. Give Docker at least 8 GB of memory: the seeding Spark container asks for a 4 GB driver on top of Trino, the Hive Metastore and MinIO. This is a local control plane, not the production topology.

The fragmented seed used here runs Spark inside a container, so you don't need Spark on the host. The older `make seed` (five unfragmented tables in `rest.demo`) is different: it runs PySpark on the host, which needs `pip install -r requirements.txt` and a Java runtime. `make install` installs neither.

Development only

The local stack runs with `CRYO_ENVIRONMENT=dev` and the published default `CRYO_SECRET_KEY`. Its dev sign-in hands an admin token to anyone who can reach the API. Don't expose it. For a real install, follow [Install on Kubernetes](https://tech-pw.github.io/cryo/install/) and [Security](https://tech-pw.github.io/cryo/security/).

## Fast path: make demo

One command takes an empty clone to graded tables and a finished maintenance run. It runs the same four steps as the walkthrough below, through the same API the UI uses.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git
$ cd cryo
$ make demo
```

`make demo` starts the stack with `./start-local.sh start` (Grafana and Prometheus are skipped unless you set `START_OBSERVABILITY=1`), recreates the fragmented tables with `make seed-fragmented`, then runs `scripts/demo.py`. That script scans the `local-rest` source, prints each table's grade, assigns the `local-standard` preset, runs `optimize` and `optimize_manifests`, waits for the run to finish, and rescans.

Done when it prints `Done in N min` and a `This run` link. Open the link: the run shows succeeded, with one job per table. Open [Tables](http://localhost:5173/tables): the eight `fixtures` tables each carry a grade.

It is safe to rerun. A scan or run that is already in flight is waited on rather than duplicated. Each rerun recreates the fixture tables, so there is fresh work every time; `make demo DEMO_SEED=0` keeps the tables you have.

## Install and start

The walkthrough does the same thing one step at a time, with a check after each.

### Clone the repository

The clone directory is `cryo`, lowercase.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git
$ cd cryo
```

### Start the local stack

`./start-local.sh start` creates the venv, starts Postgres, Redis, and the local datalake, runs Alembic, and starts the API, the UI and `cryo serve` (scanner, governor, worker, scheduler) on the host. `make up` only starts containers. It does not migrate. `/healthz` does not touch Postgres.

**terminal**

```
$ ./start-local.sh start
```

Done when the script prints `Cryo local stack is running.` and the URLs below it.

### Open the UI

Open [http://localhost:5173](http://localhost:5173). On macOS, `open http://localhost:5173` does the same thing.

Done when the login screen loads. Dev sign-in is a role (`viewer`, `operator`, or `admin`), not an arbitrary username. Pick `operator` or `admin` to follow the checks below. The dashboard is empty until step 2 below.

## From empty to a maintained table

Each `scripts/demo.py` step looks things up by name and can be rerun.

### 1. Check the demo catalog is registered

`./start-local.sh start` already registered the local REST catalog as the source `local-rest`, the local Trino as the destination `local-trino`, and the presets. None is marked default, so nothing runs until you assign one. If you started with `SEED_CONFIG=0`, run `make seed-config` now.

Done when [Settings, Connections](http://localhost:5173/settings/connections) lists `local-rest` under Lakehouses and `local-trino` under Engines.

### 2. Seed fragmented tables and scan them

`make seed-fragmented` writes eight small tables to `rest.fixtures`, one per partition layout (unpartitioned, day, month, year, bucket, multi-column, timestamp, nested), each appended in many small batches so there are small files to compact. The first run pulls the Spark image and its Iceberg packages. The scan then reads the catalog and grades every table.

**terminal**

```
$ make seed-fragmented
$ python3 scripts/demo.py scan
```

Done when the seed log prints eight `rest.fixtures.fixture_*` lines with file counts, the scan prints a grade for each table, and [Tables](http://localhost:5173/tables) lists them with the same grades.

### 3. Assign a preset

A preset says which operations to run and on which engine. This assigns `local-standard` (Trino) to every table in `fixtures`, selected by a filter rather than a list of IDs.

**terminal**

```
$ python3 scripts/demo.py assign
```

Done when it prints `8 tables now use local-standard`, and a fixture table's detail page names `local-standard` as its preset.

### 4. Run maintenance and open the receipt

This starts one run of `optimize` and `optimize_manifests` on those tables, waits for the governor to admit the jobs and the worker to finish them, then rescans so the grades reflect the result. It leaves out `expire_snapshots` and `remove_orphan_files`, which wait for an approval by default.

**terminal**

```
$ python3 scripts/demo.py run
```

Done when the run reaches `succeeded` and the script prints its link. On that page each table has a job with its operations, engine, status and duration. Compare the small-file counts the rescan prints with the ones from step 2.

If a step stalls

The script stops after its timeout and names the run it was waiting on. `./start-local.sh status` shows which processes are up, and `./start-local.sh logs` tails the API and `cryo serve`. [Troubleshooting](https://tech-pw.github.io/cryo/troubleshooting/) covers the common causes.

## What runs locally

You use the web UI, which talks only to the API. The scanner reads the demo catalog, and the worker runs maintenance on the local Trino. Nothing here needs a cloud account.

Figure 1make up runs the whole stack against a demo lake

*The local stack reads a demo catalog. Production keeps the same split with your own catalogs and engines.*

- **Read path
Text description
- You to Web UI (open)
- Web UI to API (REST)
- Scanner to Demo catalog

## What to look at

The screenshots below are synthetic demo data from a lake with history, not the result of these commands. Your local lake has eight small tables and one run.

- **Home** summarizes the lake's grades and the last run.
- **Tables** lists every table the scanner has seen, with the grade next to the counts that produced it.
- **Runs** lists scans and maintenance runs. Open one to see its jobs and receipts: operation, engine, status and duration.

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
