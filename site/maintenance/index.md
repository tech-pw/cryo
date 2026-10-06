---
title: Maintenance
description: Why Iceberg tables need maintenance, the seven operations Cryo runs in a fixed order, how the planner chunks work, how admission works, and the guardrails that stay on.
canonical: https://tech-pw.github.io/cryo/maintenance/
updated: 2026-10-06
---

# Maintenance

A maintenance run compacts first, then cleans up, in one fixed order. The order is defined in code and no preset can change it.

## Canonical order

Seven operations, skipping the ones a table does not need. Expiry comes after compaction so one run reclaims the space its own compaction freed.

**Compact**

- [`convert_deletes`](#convert-deletes): Rewrites data files so delete files are applied and no longer re-merged on every read.

- [`rewrite_position_deletes`](#rewrite-position-deletes): Consolidates position-delete files without rewriting data. Spark only.

- [`optimize`](#optimize): Partition-aware compaction of small files, planned from stored scan data.

**Measure**

- [`compute_statistics`](#compute-statistics): Refreshes table statistics straight after compaction, while the new layout is fresh.

**Clean up**

- [`optimize_manifests`](#optimize-manifests): Consolidates manifests. Trino calls it `optimize_manifests` and Spark calls it `rewrite_manifests`.

- [`expire_snapshots`](#expire-snapshots): Drops snapshots the retention policy would not keep, and the files only they referenced.

- [`remove_orphan_files`](#remove-orphan-files): Deletes files in storage that no snapshot references.

A finished run shows that order table by table. Each bar is one table's job; a failed job stays on its table and the rest of the run carries on.

cryo.local/runs/7f3c9a21

*Seeded demo lake, not a production fleet. The failed job is isolated to its table.*

## Why maintenance matters

Apache Iceberg gives you hidden partitioning, snapshot isolation, schema evolution and engine-agnostic metadata. It does not promise that tables stay fast. Left alone, four things pile up, and each one compounds independently, so fixing one does not fix the others.

| What piles up | What it costs | What fixes it |
| --- | --- | --- |

| **Small files** | Every write adds a file, and every file adds open, seek and planning cost. | [`optimize`](#optimize) |

| **Delete files** | Every scan has to merge them back into the data it reads. | [`convert_deletes`](#convert-deletes), [`rewrite_position_deletes`](#rewrite-position-deletes) |

| **Snapshot history** | Planning slows as metadata grows, and old files stay referenced. | [`optimize_manifests`](#optimize-manifests), [`expire_snapshots`](#expire-snapshots) |

| **Layout drift** | Scans read more than the query needs, because the layout no longer matches the filters. | [`optimize`](#optimize) with a sort or z-order strategy |

A table left alone does not fail loudly

It becomes a slow, compounding tax that shows up as someone else's incident: a timed-out connection pool, or a scan that reads far more than it should.

### Compaction is not one operation

Teams pick a strategy from their write pattern and query filters. The same primitive, `optimize`, does very different work depending on which one runs.

| Strategy | Use it for | Compute cost |
| --- | --- | --- |

| **Binpack** | Streaming tables and tight windows. Merges small files and reconciles deletes. | Lowest |

| **Sort** | Predictable filters on one or two columns. | Higher |

| **Z-order** | Several common filter dimensions at once. | Highest |

## What each operation does

### convert_deletes

Rewrites data files so delete files are applied and no longer re-merged on every read. On Spark it passes a delete-file threshold. On Trino it runs as an `OPTIMIZE`.

### rewrite_position_deletes

Consolidates position-delete files without rewriting data. Spark only, because Trino does not support it.

### optimize

Partition-aware compaction of small files, planned from stored scan data. This is the step the chunking rules below exist to protect.

### compute_statistics

Refreshes table statistics straight after compaction, while the new layout is fresh.

### optimize_manifests

Consolidates manifests. Trino calls it `optimize_manifests` and Spark calls it `rewrite_manifests`.

### expire_snapshots

Drops snapshots the retention policy would not keep, and the files only they referenced. Guarded, and forcing it is audited.

### remove_orphan_files

Deletes files in storage that no snapshot references. Guarded, and forcing it is audited.

## Planner chunking

A large table is never compacted in one statement. The planner ranks partitions by small-file count, highest first, skips those under the `min_small_files` threshold, and then picks one of three paths.

Figure 1The planner never sends one giant OPTIMIZE to a big table

*The planner picks one path per run.*

- **Write or execute
Text description
- Ranked partitions to Chunk under a GB budget

- Ranked partitions to Run the partition alone

- Ranked partitions to One full-table OPTIMIZE

- **Chunk under a GB budget.** Ranked partitions are grouped until the budget is full, and a cap on partitions per chunk keeps Trino's filter planning tractable.

- **Oversize partition alone.** A partition larger than the budget runs by itself.

- **Small-table bypass.** A small table skips chunking and gets a single full-table `OPTIMIZE`.

## Admission: load-aware, never eager

A worker asks the governor for permission before it runs anything. The governor probes live load, from `system.runtime.queries` on Trino and from slots on Spark on EMR, and either admits the job or defers it. It also applies per-replica caps and round-robins across replicas.

Figure 2A job starts only when the engine has room for it

*Scaling workers does not raise the number of parallel rewrites. The governor does.*

- **Write or execute

- **Control or probe
Text description
- Step 1: Worker wants a job to Governor (ask)

- Step 2: Governor to Trino (probe)

- Step 3: Governor to Admit (room)

- Governor to Spark on EMR

- Governor to Defer (no room)

You can watch admission in the Runs view. Engine load shows the slots the governor is using against the cap for each destination.

cryo.local/runs

*Runs: engine load at the top, the run history below. demo-trino is using 2 of its 6 slots, so the governor is still admitting.*

Workers are not concurrency

A worker runs one job at a time, so the fleet drains at roughly replicas divided by job duration. Engine headroom decides how much work is admitted, and worker count decides how fast it is picked up.

## Three guardrails, always on

Cooldown
Skip tables maintained within the last N hours, read from the audit log and engine history.
Per-table lock
A Redis lock held for the job's lifetime, so a table is never maintained twice at once.
Failure isolation
One job's failure is recorded against that table. The run continues with the next one.

Destructive operations are guarded

`expire_snapshots` and `remove_orphan_files` require the relevant tier in the scanner, and forcing them is audited. Cryo never touches snapshots the retention policy would keep.

## Where to go next

EnginesHow this same order renders as Trino SQL and Spark SQL.Health and costHow a table's grade improves after a run.ArchitectureScheduler, governor and worker as running components.How Cryo fitsWhy a preset binds tables to a destination, not to raw SQL.

Source: https://tech-pw.github.io/cryo/maintenance/
Markdown: https://tech-pw.github.io/cryo/maintenance/index.md
