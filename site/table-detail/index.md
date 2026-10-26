---
title: Table detail
description: A guided tour of Cryo's table detail page, covering the header, the actions menu and table settings, plus every section of the Overview, Schema & files, Snapshots, Maintenance, Config, Usage, Quality and Cost & Savings tabs, including archiving cold partitions.
canonical: https://tech-pw.github.io/cryo/table-detail/
updated: 2026-10-18
---

# Table detail

One page per Iceberg table. The header says what state the table is in, and eight tabs show the evidence, the history and the controls behind it.

Open any row on the [Tables page](https://tech-pw.github.io/cryo/tables/) to get here. Every screenshot below is the same table from the seeded demo lake, `analytics.events`: 412.6 GB in 48,210 files, 41,930 of them small, scoring 28 (grade F). Use it to learn where each answer lives before you need it on a real table.

## The header

The header stays in place across all eight tabs. It answers three questions without a click: what this table is, how healthy it is, and what Cryo will do to it next.

cryo.local/tables/analytics.events

*The header for a grade F table. Every number comes from the last scan, and the workflow tile names the engine that will run its maintenance.*

| Part | What it shows |
| --- | --- |

| Breadcrumb | Tables, then the lakehouse (source), the namespace and the table. **Copy link** copies a link to the current tab. |

| Name and badges | Table format, the partition column (or *Unpartitioned*), the health score with its grade letter, and the owner. |

| Summary tiles | **Table size**, **Files**, **Compaction candidates** (small files, also as a share of all files), **Snapshots**, and **Maintenance workflow**: the preset that maintains the table, how it was assigned, and its engine. |

| Activity line | When the scanner last read the table, the last commit, the latest maintenance action, and a **Storage location** disclosure with a copy button. |

| Run maintenance | Runs the table's workflow now, or previews it when you can't run it. The arrow opens the actions menu. |

| Table settings | The gear icon. Opens compliance, the maintenance hold, ownership and alert subscribers. |

cryo.local/tables/analytics.events

*Operations that delete files say so before you pick them.*

**Preview workflow plan** shows the plan Cryo would build before anything runs. **Add to run** picks single operations for an on-demand run. **Refresh stats** queues a scan of this one table, so the page reflects a commit you just made.

cryo.local/tables/analytics.events

*Deleted rows stay readable until the snapshots holding them expire. The sheet says when.*

**Compliance & protection** answers three questions. **Deleted rows still readable**: rows deleted from the table that older snapshots still hold, and when expiry will remove them, if the date was recorded. **Snapshots protected from expiry**: how many snapshots Cryo's expiry must keep, which can hold deleted rows longer; manage them on the [Snapshots tab](#snapshots). **Access policies**: row filters and column masks recorded for the table. Cryo records them but does not enforce them; your catalog or engine does.

**Maintenance hold** pauses compaction and cleanup for this table, for a backfill, a migration or an incident. Give a reason and pick how long, from a day up to 90 days. Scheduled and on-demand maintenance both skip the table until the hold ends or someone releases it, and scans keep running so the grade still shows the cost. Holds need the operator role and are audited. A hold set for the whole schema shows here too, but is released from the schema, not one table.

cryo.local/tables/analytics.events

*A hold always has a reason and an end date, so it can't be forgotten.*

**Ownership** sets the owner email, which also receives SLA and quality-rule alerts. **Alert subscribers** adds more recipients.

## The eight tabs

Each tab owns one question. The address bar carries the open tab as `?tab=`, so a link to `?tab=snapshots` opens that tab directly. On narrow screens the tab bar becomes a **Table section** dropdown.

| Tab | The question it answers | Sections |
| --- | --- | --- |

| [Overview](#overview) | Why is the score what it is, and what fixes it? | Score, Layout checks, File layout, History & activity, Health over time, Context |

| [Schema & files](#schema-files) | What is the table made of? | Columns, Files, Small-file candidates over scans, Delete files, Layout, Partitions |

| [Snapshots](#snapshots) | What changed, and what is being kept? | Snapshot history, Commits, Excluded from Cryo expiry |

| [Maintenance](#maintenance) | Will it be maintained tonight, and what happened last time? | Readiness, Choose an action, Follow the outcome, Compaction analysis |

| [Config](#config) | What rules is it maintained by? | Maintenance settings, Scheduling window, Table configuration |

| [Usage](#usage) | Who reads it, and which parts? | Query activity, Partition access, Table relationships, Similar schemas |

| [Quality](#quality) | Is the data fresh and complete? | Freshness vs target, What changed?, Quality rules |

| [Cost & Savings](#cost) | What does it cost to store, and what would maintenance give back? | What this table costs, Where storage accumulates, Ways to save, Archive cold partitions |

## Overview

The default tab. It leads with the score and the deductions behind it, then the evidence those deductions came from.

cryo.local/tables/analytics.events?tab=overview

*Six findings explain the 72 lost points. Each row offers the operation that addresses it.*

### Score

The gauge shows the score, its grade and the change over the recorded history, plus whether the table is eligible for nightly selection. **What's pulling the score down** lists each deduction, critical first, with the reason in plain words and the operation that addresses it. A critical finding costs 20 points and a warning 8, as [Health and cost](https://tech-pw.github.io/cryo/health/#deductions) explains. **Preview full workflow plan** opens the plan, and **Schedule, history & settings** jumps to the Maintenance tab.

### Layout checks

Twelve weighted checks of how well the table is set up for its engine, storage and workload: file size against the target, delete-file ratio, whether the partition scheme matches query predicates, whether compaction keeps up with write velocity, sort order, distribution mode, manifests, statistics, retention, orphan cleanup, schema width and storage class. Only the checks to act on are shown as cards, marked **Fix** or **Improve**, each with a link to where you change it: a setting on Config, an operation on Maintenance, or archiving on Cost & Savings. Passing and unknown checks are collapsed underneath.

Under the checks, **Manifests & metadata** shows the manifest count, the average manifest size against Iceberg's 8 MiB target, files per manifest, and whether old `metadata.json` files get deleted after each commit. With `write.metadata.delete-after-commit.enabled` off, which is Iceberg's default, every commit leaves one behind until orphan cleanup. Manifests the scan couldn't read are counted here too.

cryo.local/tables/analytics.events?tab=overview

*The checks explain the deductions above them. Each card links to the place that fixes it.*

One score on the page

The checks are shown as reasons and fixes, not as a second score. The grade at the top is still 100 minus the deductions, so a table can pass most checks and still grade F.

### File layout

A histogram of data files by size, in binary units, against the table's write target. Bars are coloured by band: tiny (up to an eighth of the target), undersized, near target, and oversized (twice the target or more). The headline counts files in undersized ranges. Below it are the average file size, partition count, partition skew and delete files, with **Partitions and files** linking to the next tab.

### History & activity

Snapshot count, commits per day and the last commit. **What maintenance achieved** totals what successful operations reported, for the last 30 days and since the first one: storage freed and files removed by compaction, then a line per operation. Only numbers the engine reported count. An operation that didn't say what it freed is counted as unmeasured, not as zero, so the card says how many of them reported. Then **Recent maintenance**: each audited action with its result, bytes freed or files before and after, duration, engine and trigger.

cryo.local/tables/analytics.events?tab=overview

*Statistics and manifest rewrites report nothing to measure, so they count as unmeasured rather than zero.*

### Health over time

The score across the last 30 days of recorded scans, drawn over shaded grade bands. **View score history** opens the same points as a table.

### Context

Queries in the last 30 days, estimated storage per month, S3 requests, rows and the owner. **Edit owner & alerts** opens table settings. Triggered maintenance signals, such as a high small-file ratio, appear at the bottom with a link to review them.

## Schema & files

The physical table: its columns, its files and how they are laid out. The pills at the top jump between sections: Schema, Files, Layout and Partitions. Table properties live on the [Config tab](#config).

cryo.local/tables/analytics.events?tab=files

*A dash means the scan didn't record that value. Columns that nothing recorded are hidden.*

### Columns

Every column with its type, null rate, value range and distinct values. Range, distinct-value and percentile columns appear only when the scan recorded them for at least one column, and a dash marks a single missing value. Signals flag what deserves a look: **Drift** when the null rate moved from its baseline, **Partition** for partition sources, and **Bloom candidate** when query patterns recommend a bloom filter on the column. A recommendation doesn't change files already written.

**Query use · 30 days** shades each column by how many logged queries filtered on it and joined on it; hover for equality versus range filters and selectivity. Columns that are filtered often are candidates for partitioning, sort order or a bloom filter. Column use comes from per-query logs. A source that records only table-level query counts can't say which columns were used, and the page says so instead of drawing an empty column.

cryo.local/tables/analytics.events?tab=files

*user_id is filtered by equality in most queries, which is why it shows up again as a partition suggestion.*

### Files

Data files, position and equality delete files, manifests, average file size, the writer target and small-file candidates. Small-file candidates are counted only in partitions selected for compaction, so they can be lower than the histogram on Overview. Beside it, **Small-file candidates over scans** shows whether the backlog is growing.

**Delete files** shows the share of files that are delete files, their trend over scans and a verdict. The verdict uses the same cut-offs as the health score: a warning at 5% or 100 delete files, critical at 20% or 1,000. **Minor delete compaction recommended** means the delete files can be merged without touching data, which Spark's rewrite position deletes does; Trino has no such procedure, so on Trino convert deletes rewrites the affected data files instead. **Major rewrite recommended** means convert deletes, which works on both engines.

cryo.local/tables/analytics.events?tab=files

*640 delete files are past the 100-file warning, even though they are only 3.9% of files.*

### Layout

The partition spec and sort order in words, then the granularity the scan observed, such as 412 partitions by date with 117 files each. A badge appears only when the granularity is a problem. **Workload-aware layout recommendations** opens reclustering suggestions built from query patterns.

### Partitions

The partitions with the most small files, ranked. **Exact partition counts** shows the same list as a table with each partition's small-file rate.

When query patterns point to a better partition spec, **Suggested partition change** shows the current and suggested specs, why, the confidence, how many queries were analysed and an estimated pruning gain. Partition evolution only changes metadata: new writes use the new spec, existing files keep the old one until compaction rewrites them, and queries still prune both. Cryo doesn't run this change; apply it with your engine's `ALTER TABLE` once the table owner agrees.

cryo.local/tables/analytics.events?tab=files

*The pruning gain is an estimate from query patterns, not a measurement.*

## Snapshots

The table's retained history, read from its metadata.

cryo.local/tables/analytics.events?tab=snapshots

*A replace commit is a compaction. Its record count stays flat while the file count drops.*

### Snapshot history

Live snapshots, how many are referenced by tags or branches, how many are excluded from Cryo's expiry, and the newest and oldest retained commits. **Expire snapshots…** queues snapshot expiry for this table. It doesn't list the snapshots that will be removed, so check retention on the Config tab first.

### Excluded from Cryo expiry

Right under the summary: snapshots Cryo's expiry must keep, with a label, who excluded them and when. On Spark, expiry keeps each excluded id by tagging it `cryo-protect-<id>`. Trino's `expire_snapshots` can't skip a single snapshot, so exclusions only hold on Spark workflows.

### Commits

One row per retained snapshot: id with its parent underneath, commit time (hover for the sequence number), operation, the change it made, partitions changed, the table's rows and size after it, the writer, and retention: tags, branches and whether it's excluded from Cryo's expiry. **Change** shows rows added and removed, and a badge when the commit added delete files. **Writer** names the engine and version from the commit's summary, such as Trino, Spark or Flink; hover for the user and query or job id. Selecting a snapshot opens its full summary: rows, data files and bytes added and removed, delete files by kind, partitions changed, and who committed it. Anything the writer didn't record says so, rather than showing zero. Select snapshots to compare their recorded summaries. A row's actions can **Protect** the snapshot, which excludes it from Cryo's expiry, or roll the table back to it after a confirmation.

cryo.local/tables/analytics.events?tab=snapshots&snapshot=8812286588

*A compaction commit: 15,882 files became 165 across 31 partitions, written by Cryo on Trino.*

Rollback is a commit

Rolling back makes an older snapshot current. It doesn't delete the newer ones, but the next expiry run can, once they age out of retention.

## Maintenance

Three numbered steps, then the evidence the planner ranks compaction with.

cryo.local/tables/analytics.events?tab=maintenance

*Each card shows how many score points its findings cost, so you can see which operation matters most.*

### Readiness

**Nightly selection** says whether tonight's scheduled run may pick this table. A table can be eligible and still not be picked, because engine capacity and the run's budget decide. The tiles show when eligibility was last evaluated, the cooldown between maintenance runs, how fresh scan stats must be, and the next nightly run. The **Eligibility checklist** lists every blocking reason, and **How nightly selection works** explains the gates. The preset and overrides that set these thresholds are on the [Config tab](#config).

### Choose an action

**Run on demand** has one card per operation, each with the measurement that motivates it, the score points it affects and when it last ran. **Add recommended** adds the operations with score findings behind them, and **All four** adds every one. You review the plan before it runs. Nightly eligibility and on-demand runs have separate checks, so a table that isn't eligible tonight can still be maintained on demand.

cryo.local/tables/analytics.events?tab=maintenance

*Before and after come from scans, so new writes during the run show up too.*

### Follow the outcome

**Runs** is a grid of operations by run, filterable to manual or nightly runs. Selecting a run shows its status, trigger, mode and duration; the table's stats before the run and at the next scan; and, under **Operations & statements**, each operation in order with its timing and recorded statement. **Live log** streams log lines while a run is going and keeps them for 24 hours; after that, the recorded statements are the log. **Open full run** goes to the run page.

The grid also shows the nights the scheduled run looked at this table and didn't pick it, merged when consecutive nights had the same reason. Each one says why: cooling down, scan too old, excluded, on hold, retry backoff, nothing to do, or over the night's table cap. A stripe marks nights when the table needed work but wasn't picked. Select one to see the nights it covers and what to do about it. Each step in a run's detail also shows what the engine reported it did, such as files in and out or bytes freed, or says it didn't report.

cryo.local/tables/analytics.events?tab=maintenance

*The tiles answer the question first; the rankings and signals are the evidence.*

### Compaction analysis

Why some partitions are compacted before others. The tiles at the top summarise it: how many partitions are eligible, the top benefit score, what the last compaction achieved, whether delete files need their own pass, and how many trigger signals fire. Below, one ranking at a time: **Stored score** ranks partitions by the benefit score stored with the last scan, with skipped partitions and why; **Query frequency · 30 d** ranks by how often each partition is read. They are different populations, so the toggle keeps them apart. **Maintenance trigger signals** lists the triggered signals first and collapses the rest. **Refresh benefit scores** recomputes the ranking.

## Config

Everything that decides how the table is maintained and written, in one place.

cryo.local/tables/analytics.events?tab=config

*The workflow sets the thresholds; overrides pin one parameter for this table only.*

### Maintenance settings

The workflow (preset) that maintains the table and how it was chosen: assigned by an operator, picked by a rule, or the platform default. **Change workflow** reassigns it. **Effective parameters** lists every value the planner uses and where it comes from, **Preset** or **Override**; an overridden row also shows the preset value it replaced. **Override a parameter** lets an admin pin one value for this table. Overrides survive a change of workflow.

### Scheduling window

The off-peak window in which heavy operations run. It is a governor-wide setting, so it is shown here but changed in governor settings.

### Table configuration

Every setting that shapes how the table is written, committed and cleaned up, read live from the catalog. Each row shows the current value and where it comes from, Cryo's recommendation, and the risk of changing it in that direction. The ⓘ next to a name explains the setting, and the risk badge explains the consequence.

cryo.local/tables/analytics.events?tab=config

*Rows that differ from the recommendation carry a bar on the left. Click the first tile to show only those.*

Below the inventory, **More suggestions from the last scan** lists what the scan's property checks suggest where no row already makes a recommendation, so a property never gets two answers. **Stage** adds the suggestion to the same review as a row edit.

cryo.local/tables/analytics.events?tab=config

*Suggestions go through the same review and audited apply as any other change.*

### Stage

Edit a value with its typed editor, or stage Cryo's recommendation. Nothing is written yet, and staged rows stay highlighted.

### Review

**Review and apply** previews every staged change with its risk. A reason is optional, except that a batch with a high-risk change needs both a reason and an explicit acknowledgement.

### Apply

The changes are committed to the catalog in one transaction and audited, and the page re-reads the table to show the result.

cryo.local/tables/analytics.events?tab=config

*Shortening snapshot retention is high risk: it limits time travel and rollback.*

**Maintenance operations** lists the seven operations in the order Cryo runs them, grouped into compaction and cleanup. Each shows whether it runs for this table under its workflow, and why not if it doesn't. Switch one off to exclude it for this table only; the planner then drops it from every plan, nightly or on demand, and the plan says why. Saving needs a reason, and excluding a high-risk operation such as snapshot expiry needs an acknowledgement.

cryo.local/tables/analytics.events?tab=config

*Exclusions are per table and audited. Every other table on the workflow keeps running the operation.*

**Table metadata** shows the format version, UUID, locations, schema, partition spec, sort order and refs. **All table properties** lists every property on the table with a short explanation of what it does. The ⓘ links to the matching setting row and the Iceberg documentation.

cryo.local/tables/analytics.events?tab=config

*Each property is labelled by kind: a Cryo setting, read, write, table behaviour, reserved, catalog, engine or custom.*

Writes go to the live table

Applying a configuration change commits new table properties through the catalog, the same as `ALTER TABLE … SET TBLPROPERTIES`. Write settings take effect for the next writer, not for files already written. Without edit access, rows show their values but can't be staged.

## Usage

How the table is read, from the query log and storage request logs.

cryo.local/tables/analytics.events?tab=usage

*Query counts and storage requests are separate sources and are never added together.*

### Query activity

Logged queries and average bytes scanned for the window shown, with median, p95 and p99 latency. **Inspect slowest queries** lists up to the 100 slowest with their SQL, user, engine and duration. Reads that bypass the query log aren't counted.

### Partition access

Storage requests per day and the most-read partitions, from recorded request counts. This is where hot and cold partitions show up.

### Table relationships

Upstream and downstream tables from recorded lineage. Check them before changing how this table is written. **Explore lineage** expands the graph.

### Similar schemas

Tables whose schemas overlap this one: candidates for reuse or consolidation, with suggested union SQL. Overlapping schemas don't prove the data is duplicated.

## Quality

Whether the data is fresh and complete, checked against metadata the scanner already collects.

cryo.local/tables/analytics.events?tab=quality

*A failed rule names the measured value and the threshold it missed.*

### Freshness vs target

The age of the latest data against the table's freshness target, as a bar. Age uses the latest snapshot commit, or the latest scan when the commit time wasn't recorded.

### What changed?

Schema and arrival anomalies over the last 30 days: added or dropped columns, schema version changes, data arrival and row count. No recorded anomaly is not a guarantee that the data is correct.

### Quality rules

Rules on freshness, completeness, value ranges, size and size change. They are **zero scan**: each is decided from the metadata every scan already collects, so no data is read. A rule the metadata can't decide shows Unknown and says why. On failure, a rule either raises an alert or is only shown here. **Add rule** creates one, and **Re-check against latest scan** evaluates them again.

The layout checks that used to sit here moved to the [Overview tab](#overview-checks), next to the deductions they explain.

## Cost & Savings

What the table costs to keep in object storage, and what maintenance could give back.

cryo.local/tables/analytics.events?tab=cost

*Each lever names its own input, so they're side by side rather than added together.*

### What this table costs

Estimated storage per month at the AWS S3 Standard rate, the S3 request cost when recorded activity exists, and what maintenance can save per month. Request counts appear only when they were measured. For a table that isn't on S3, dollar figures aren't shown, because the rate wouldn't apply.

### Where storage accumulates

Live data, dead bytes still held by snapshots, metadata, and orphan files outside the table's metadata. The bars are byte counts, not shares of a total.

### Ways to save

Three levers, each with its number and where to act. **Reclaim with maintenance**: bytes that expiry and orphan cleanup would free, linking to Maintenance. **Cheaper storage class**: the projected monthly saving from archiving cold partitions, and whether S3 Intelligent-Tiering would pay off for the whole table. **Cheaper queries from compaction**: the modelled 30-day saving for the ranked compaction candidates. They measure different things, so they're shown next to each other and not totalled.

### Archive cold partitions

Moves the data files of partitions nobody reads to a cheaper S3 storage class, in place. Paths and Iceberg metadata don't change, so every query keeps working. Only instant-retrieval classes are offered: **Intelligent-Tiering**, **Standard-IA** and **Glacier Instant Retrieval**. Restoring copies the files back to Standard.

cryo.local/tables/analytics.events?tab=cost

*Partitions are suggested from their age and read evidence. Nothing moves until an operator confirms.*

### Select

Tick partitions, or use **Archive all suggested…**. On the **Archived** view, select partitions to restore.

### Preflight

The dialog checks before anything moves: the data and file count, the one-time copy cost, the change in monthly storage, retrieval and early-delete charges, the bucket's versioning, Object Lock and replication, and whether the source's S3 credentials can do the copy. Without read evidence, Glacier IR and Standard-IA need an explicit confirmation.

### Apply

Give a reason and start the job. It runs in the background under the table's maintenance lock, and the job list shows progress, files moved, skipped and failed. Every job is audited.

cryo.local/tables/analytics.events?tab=cost

*Cryo reuses the lakehouse connection's credentials. When a permission is missing it shows the exact policy to add.*

What archiving changes

Compaction skips archived partitions, because rewriting them would move the data back to Standard and bill the class's minimum storage duration. Restore a partition before compacting it. Archiving is manual only: it never runs on a schedule.

## Where to go next

Health and costHow the score is built from deductions, with a worked example.MaintenanceThe fixed operation order, admission and what a run records.EnginesWhich engine runs which operation, and why some can't.Connect a lakehouseAdd a catalog so its tables show up here.

Source: https://tech-pw.github.io/cryo/table-detail/
Markdown: https://tech-pw.github.io/cryo/table-detail/index.md
