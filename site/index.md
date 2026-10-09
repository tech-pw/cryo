---
title: Cryo
description: Cryo is an Apache-2.0, self-hosted Apache Iceberg maintenance control plane. It reads your catalogs, plans maintenance, admits the work to Trino or Spark, and leaves a receipt for every decision.
canonical: https://tech-pw.github.io/cryo/
updated: 2026-10-06
---

# Cryo

Cryo reads your Apache Iceberg catalogs, plans maintenance, and admits the work to Trino or Spark. Reading and writing never share a code path, and every run leaves a receipt.

Pre-1.0 · Apache-2.0→

# Iceberg maintenance with a receipt for every decision.

Cryo reads your catalogs, plans compaction and cleanup, and admits the work to Trino or Spark only when they have headroom.

Operations7
Engines2
SQL in planner0
Receipt per action1

Start with the quickstart
See how it fits together

Runs against the stack you already have:

- Apache Iceberg

- Iceberg REST

- Hive Metastore

- AWS Glue

- Trino

- Spark on EMR

- PostgreSQL

- Redis

- Helm

- Kubernetes

- Amazon S3

- Apache Iceberg

- Iceberg REST

- Hive Metastore

- AWS Glue

- Trino

- Spark on EMR

- PostgreSQL

- Redis

- Helm

- Kubernetes

- Amazon S3

What Cryo does

## Plans a safe rewrite. Runs it with headroom.

One table passes through Cryo and leaves maintained, with a receipt.

01 SCAN 02 ADMIT 03 REWRITE CRYO CONTROL PLANE admits work when engines have headroom TABLE AFTER MAINTAINED TABLE same table, with a receipt 01 SCAN 02 ADMIT 03 REWRITE TABLE AFTER MAINTAINED TABLE same table, with a receipt The rewrite stays partition-aware and load-aware. [See the maintenance flow →](https://tech-pw.github.io/cryo/maintenance/)

How Cryo works

## The stack you run, Cryo in the middle, the lake on the right.

Full map →

The order

## Compact first, then clean up.

A run on one table walks seven operations in a fixed order. Compaction creates new files and expiry removes old ones, so a single pass reclaims the space its own compaction freed.

[Read the guardrails](https://tech-pw.github.io/cryo/maintenance/)

**Compact**

- `convert_deletes` Apply delete files into data files.

- `rewrite_position_deletes` Consolidate position deletes (Spark only).

- `optimize` Merge small files, one partition chunk at a time.

**Measure**

- `compute_statistics` Refresh table statistics after the rewrite.

**Clean up**

- `optimize_manifests` Consolidate manifests.

- `expire_snapshots` Drop snapshots the retention policy would not keep.

- `remove_orphan_files` Delete files that no snapshot references.

Guarantees

## Four things Cryo will not trade away

These are design rules of the platform, not options you switch on.

Read stays apart from write
Sources are where tables live. Destinations are where work runs. They have separate tables, configs and code.
How the split works

The plan contains no SQL
The planner emits operations and partition predicates. Trino and Spark each render their own dialect.
Engines and renderers

Engine load decides concurrency
The governor probes live Trino and Spark load before admitting work. Adding workers does not add parallel rewrites.
How admission works

Every action leaves a receipt
Table, operation, engine, status and duration are written to an audit row, whether the run succeeded or not.
Who does what

audit log · live example

- `optimize`analytics.events*succeeded*4m 12s

- `expire_snapshots`analytics.events*succeeded*18s

- `optimize`billing.invoices*deferred · trino busy*—

- `remove_orphan_files`analytics.events*succeeded*51s

- `optimize_manifests`crm.accounts*succeeded*9s

- `optimize`billing.invoices*succeeded*2m 40s

- `compute_statistics`crm.accounts*succeeded*22s

- `expire_snapshots`ops.metrics*failed · retried*3s

## See it on a demo lake

The local UI comes up empty. `make seed` writes five small tables and does not scan them. The screenshots on this site are synthetic demo data.

**terminal**

```
$ git clone https://github.com/tech-pw/cryo.git && cd cryo
$ ./start-local.sh start
```

[Open the quickstart](https://tech-pw.github.io/cryo/quickstart/)

Thirty seconds

## Compaction. Visibility. Cost control.

0:00

0:30

*Sound is on. The lines on screen stand alone if you mute it.*

## Built & run by engineers at *Physics Wallah*

The same people who keep the lake write the tool that maintains it.

- 01 Name to come Data Platform

- 02 Name to come Data Platform

- 03 Name to come Data Platform

Source: https://tech-pw.github.io/cryo/
Markdown: https://tech-pw.github.io/cryo/index.md
