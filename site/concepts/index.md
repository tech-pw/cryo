---
title: How Cryo fits
description: The three parts of Cryo (source, preset, destination), the metadata-only scan path, and how a maintenance wave moves from scheduler to engine.
canonical: https://tech-pw.github.io/cryo/concepts/
updated: 2026-10-16
---

# How Cryo fits

A source answers where an Apache Iceberg table lives. A destination answers where maintenance runs. Cryo holds the plan, the admission decision, the locks and the evidence between them.

## Three parts, never collapsed

Cryo keeps reading and writing apart, down to separate database tables, configs and code. A catalog connection never carries engine credentials, and an engine never learns how to read a catalog.

**Source (read side)**

Answers where a table lives and how to read its metadata. Supported catalogs are Iceberg REST, Hive, Glue, Apache Polaris, and Project Nessie. Unity Catalog and JDBC are not supported. Polaris and Nessie use the REST adapter. Branch and tag operations are not exposed.

**Preset (binding)**

Binds what to do, on which destination, when, and to which tables. A preset never holds raw SQL.

**Destination (write side)**

Answers where to submit maintenance work. Trino and Spark execute. Native Go executes when enabled. Flink generates a config and does not submit a job.

Figure 1Read from a source, bind with a preset, write through a destination

*Sources feed the scanner. Presets bind tables to destinations. Cryo schedules and admits the work.*

- **Read path
- **Write or execute
Text description
- Step 1: Iceberg catalog to Scanner (read)
- Step 2: Scanner to Tables
- Step 3: Tables to Preset
- Step 4: Preset to Trino or Spark (write)

The split shows up in the UI too. Under **Settings → Connections**, catalogs live on the **Lakehouses** tab and engines on the **Engines** tab, each with its own credentials. Each lakehouse is one row that shows its catalog type, whether it is enabled, its endpoint and how it authenticates to S3. Credentials embedded in an endpoint URI are never shown.

cryo.local/settings/connections

*Sources are added as lakehouses. A lakehouse only reads; engines are a separate tab and never share credentials with a catalog.*

## Scan path: metadata only, no live data files

The scanner is the single source of truth for what shape every table is in. It reads catalog and manifest metadata and stores the result in PostgreSQL. It never reads a data file and never starts an `OPTIMIZE`.

Figure 2A scan reads catalog metadata, never your data files

*A scan reads a source and writes table shape to PostgreSQL, not to an engine. Object storage stays untouched.*

- **Read path
- **Write or execute
Text description
- Step 1: Iceberg REST to Scanner (metadata)
- Step 2: Scanner to PostgreSQL (stats)
- Hive or Glue to Scanner
- Object storage is not read by Scanner

That is why the UI and API can answer "which tables need care?" without touching the lake. Request paths read stored scan results. They never run a live `$files` query.

## Maintenance wave

A wave is one pass of scheduled work across many tables.

Figure 3A wave: queue, admit, plan, execute

*The scheduler finds due presets, the governor decides whether to admit, and a worker executes through the destination engine.*

- **Write or execute
- **Control or probe
Text description
- Step 1: Scheduler to Governor (queue)
- Step 2: Governor to Worker (admit)
- Step 3: Worker to Planner (plan)
- Step 4: Worker to Trino or Spark on EMR (execute)
- Governor to Trino or Spark on EMR (probe load)

Many tables need work, fewer are eligible once cooldowns, locks and off-peak windows are applied, and fewer still are admitted while the engine is loaded. A table that is not admitted waits for the next window. It is not treated as a failure.

## Grades come from stored metadata

Each scan ends with an A to F health grade per table, computed from the stored shape. [Health and cost](https://tech-pw.github.io/cryo/health/) lists the letter bands and the 8 and 20 point deductions. Trigger thresholds live in the scanner, not on that page.

## Where to go next

ArchitectureWhere these parts sit as running components, and how they connect.MaintenanceWhat the destination side runs, in a fixed order.Health and costHow the scanner turns metadata into a grade.QuickstartSee a source, a preset and a destination connected.

Source: https://tech-pw.github.io/cryo/concepts/
Markdown: https://tech-pw.github.io/cryo/concepts/index.md
