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

Follow one fragmented partition through Cryo, from scan to receipt. Click a step to pause on it.

analytics.events · day = 2026-10-11

Every action audited

TABLE BEFORE

chunk 1 · 38 GB

Files
2,140
Avg size
18 MB
Small files
2,140

Scan · metadata only

manifests read
212
data files listed
2,140
partition size
38 GB
data files opened
0

Plan · 1 chunk

38 of 100 GB
WHERE event_date = DATE '2026-10-11'

No SQL yet: the plan holds this predicate.

Admit · live Trino slots

trino-a  full
trino-b  this job

Counted from `system.runtime.queries`

Rewrite · `optimize` on `trino-b`

2,140 *→* 152 files

Only this partition. The rest of the table is untouched.

Receipt · audit log

✓`optimize` succeeded

engine
trino-b
duration
4m 12s
files
2,140 → 152

TABLE AFTER
receipt

Files
152
Avg size
256 MB
Small files
0

- **01 Scan** Reads metadata. Never opens data files.

- **02 Plan** Fits the partition into a size-budgeted chunk.

- **03 Admit** Waits for a free slot on a Trino coordinator.

- **04 Rewrite** OPTIMIZE on this partition only, never the whole table.

- **05 Receipt** Logs what ran, where, how long, and what changed.

The rewrite stays partition-aware and load-aware. [See the maintenance flow →](https://tech-pw.github.io/cryo/maintenance/)

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
