---
title: Table detail
description: A guided tour of Cryo's table detail page, covering the header, the actions menu and table settings, plus every section of the Overview, Schema & files, Snapshots, Maintenance, Config, Usage, Quality and Cost & Savings tabs.
canonical: https://tech-pw.github.io/cryo/table-detail/
updated: 2026-10-17
---

# Table detail

One page per Iceberg table. The header says what state the table is in, and eight tabs show the evidence, the history and the controls behind it.

Open any row on the Tables page to get here. Every screenshot below is the same table from the seeded demo lake, `analytics.events`: 412.6 GB in 48,210 files, 41,930 of them small, scoring 28 (grade F). Use it to learn where each answer lives before you need it on a real table.

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

| Table settings | The gear icon. Opens compliance, ownership and alert subscribers. |

cryo.local/tables/analytics.events

*Operations that delete files say so before you pick them.*

**Preview workflow plan** shows the plan Cryo would build before anything runs. **Add to run** picks single operations for an on-demand run. **Refresh stats** queues a scan of this one table, so the page reflects a commit you just made.

cryo.local/tables/analytics.events

*Table settings holds who owns the table and who hears about it.*

**Compliance & protection** shows ABAC policies on the table and any pending deletion delay. **Ownership** sets the owner email, which also receives SLA and quality-rule alerts. **Alert subscribers** adds more recipients. Excluding a snapshot from expiry is not done here; it lives on the [Snapshots tab](#snapshots).

## The eight tabs

Each tab owns one question. The address bar carries the open tab as `?tab=`, so a link to `?tab=snapshots` opens that tab directly. On narrow screens the tab bar becomes a **Table section** dropdown.

| Tab | The question it answers | Sections |
| --- | --- | --- |

| [Overview](#overview) | Why is the score what it is, and what fixes it? | Score, File layout, History & activity, Health over time, Context |

| [Schema & files](#schema-files) | What is the table made of? | Columns, Files, Small-file candidates over scans, Layout, Partitions, Properties |

| [Snapshots](#snapshots) | What changed, and what is being kept? | Snapshot history, Commits, Excluded from Cryo expiry |

| [Maintenance](#maintenance) | Will it be maintained tonight, and what happened last time? | Readiness, Choose an action, Follow the outcome, Compaction analysis |

| [Config](#config) | What rules is it maintained by? | Maintenance settings, Scheduling window, Table configuration |

| [Usage](#usage) | Who reads it, and which parts? | Query activity, Partition access, Archive, Table relationships, Similar schemas |

| [Quality](#quality) | Is the data fresh and complete? | Freshness vs target, What changed?, Quality rules, Optimization checklist |

| [Cost & Savings](#cost) | What does it cost to store, and what would maintenance give back? | What this table costs, Where storage accumulates, Where maintenance could help, Storage tiering, Compaction economics |

## Overview

The default tab. It leads with the score and the deductions behind it, then the evidence those deductions came from.

cryo.local/tables/analytics.events?tab=overview

*Six findings explain the 72 lost points. Each row offers the operation that addresses it.*

### Score

The gauge shows the score, its grade and the change over the recorded history, plus whether the table is eligible for nightly selection. **What's pulling the score down** lists each deduction, critical first, with the reason in plain words and the operation that addresses it. A critical finding costs 20 points and a warning 8, as [Health and cost](https://tech-pw.github.io/cryo/health/#deductions) explains. **Preview full workflow plan** opens the plan, and **Schedule, history & settings** jumps to the Maintenance tab.

### File layout

A histogram of data files by size, in binary units, against the table's write target. Bars are coloured by band: tiny (up to an eighth of the target), undersized, near target, and oversized (twice the target or more). The headline counts files in undersized ranges. Below it are the average file size, partition count, partition skew and delete files, with **Partitions and files** linking to the next tab.

### History & activity

Snapshot count, commits per day and the last commit, then **Recent maintenance**: each audited action with its result, bytes freed or files before and after, duration, engine and trigger.

### Health over time

The score across the last 30 days of recorded scans, drawn over shaded grade bands. **View score history** opens the same points as a table.

### Context

Queries in the last 30 days, estimated storage per month, S3 requests, rows and the owner. **Edit owner & alerts** opens table settings. Triggered maintenance signals, such as a high small-file ratio, appear at the bottom with a link to review them.

## Schema & files

The physical table: its columns, its files and how they are laid out. The pills at the top jump between sections: Schema, Files, Layout, Partitions and Properties.

cryo.local/tables/analytics.events?tab=files

*Missing measurements say Not measured instead of showing zero.*

### Columns

Every column with its type, null rate, lower and upper bounds, distinct values and quantiles where the scan recorded them. Signals flag what deserves a look: **Drift** when the null rate moved from its baseline, **Partition** for partition sources, and **Bloom candidate** when query patterns recommend a bloom filter on the column. A recommendation doesn't change files already written.

### Files

Data files, position and equality delete files, manifests, average file size, the writer target and small-file candidates. Small-file candidates are counted only in partitions selected for compaction, so they can be lower than the histogram on Overview. Beside it, **Small-file candidates over scans** shows whether the backlog is growing.

### Layout

The partition spec and sort order in words, then how well the layout fits the data: alignment and granularity, each with what the scan observed. **Workload-aware layout recommendations** opens reclustering suggestions built from query patterns.

### Partitions

The partitions with the most small files, ranked. **Exact partition counts** shows the same list as a table with each partition's small-file rate.

### Properties

Iceberg property recommendations for this table, each with its current and recommended value and where the recommendation came from. Editing properties happens on the [Config tab](#config).

## Snapshots

The table's retained history, read from its metadata.

cryo.local/tables/analytics.events?tab=snapshots

*A replace commit is a compaction. Its record count stays flat while the file count drops.*

### Snapshot history

Live snapshots, how many are referenced by tags or branches, how many are excluded from Cryo's expiry, and the newest and oldest retained commits. **Expire snapshots…** queues snapshot expiry for this table. It doesn't list the snapshots that will be removed, so check retention on the Config tab first.

### Commits

One row per retained snapshot: id, parent, commit time, sequence number, operation, record count and total size. Select snapshots to compare their recorded summaries. A row's actions can **Protect** the snapshot, which excludes it from Cryo's expiry, or roll the table back to it after a confirmation.

### Excluded from Cryo expiry

Snapshots Cryo's expiry must keep, with a label, who excluded them and when. On Spark, expiry keeps each excluded id by tagging it `cryo-protect-`. Trino's `expire_snapshots` cannot skip a single snapshot, so read the note before relying on an exclusion with a Trino workflow.

Rollback is a commit

Rolling back makes an older snapshot current. It doesn't delete the newer ones, but the next expiry run can, once they age out of retention.

## Maintenance

Three numbered steps, then the evidence the planner ranks compaction with.

cryo.local/tables/analytics.events?tab=maintenance

*Each card shows how many score points its findings cost, so you can see which operation matters most.*

### Readiness

**Nightly selection** says whether tonight's scheduled run may pick this table. A table can be eligible and still not be picked, because engine capacity and the run's budget decide. The tiles show when eligibility was last evaluated, the cooldown between maintenance runs, how fresh scan stats must be, and the next nightly run. The **Eligibility checklist** lists every blocking reason, and **How nightly selection works** explains the gates.

### Choose an action

**Run on demand** has one card per operation, each with the measurement that motivates it, the score points it affects and when it last ran. **Add recommended** adds the operations with score findings behind them, and **All four** adds every one. You review the plan before it runs. Nightly eligibility and on-demand runs have separate checks, so a table that isn't eligible tonight can still be maintained on demand.

cryo.local/tables/analytics.events?tab=maintenance

*Before and after come from scans, so new writes during the run show up too.*

### Follow the outcome

**Runs** is a grid of operations by run, filterable to manual or nightly runs. Selecting a run shows its status, trigger, mode and duration; the table's stats before the run and at the next scan; and, under **Operations & statements**, each operation in order with its timing and recorded statement. **Live log** streams log lines while a run is going and keeps them for 24 hours; after that, the recorded statements are the log. **Open full run** goes to the run page.

cryo.local/tables/analytics.events?tab=maintenance

*Two rankings, two inputs: stored benefit scores from the scan, and query frequency from the horizon model.*

### Compaction analysis

Why some partitions are compacted before others. **Stored benefit-score candidates** ranks partitions by the benefit score stored with the last scan, including those below the minimum and why any were skipped. **Horizon query-frequency candidates** ranks by how often each partition is read. **Effectiveness**, **Delete-file health** and **Maintenance trigger signals** show what past compaction achieved, whether delete files need their own pass, and which signals are triggered now. **Refresh benefit scores** recomputes the ranking.

## Config

Everything that decides how the table is maintained and written, in one place.

cryo.local/tables/analytics.events?tab=config

*The workflow sets the thresholds; overrides pin one parameter for this table only.*

### Maintenance settings

The workflow (preset) that maintains the table and how it was assigned. **Choose a workflow** reassigns it. **Parameters** lists the effective values, and **Parameter overrides** lets an admin pin a single parameter for this table. Overrides survive a change of workflow.

### Scheduling window

The off-peak window in which heavy operations run. It is a governor-wide setting, so it is shown here but changed in governor settings.

### Table configuration

Every setting that shapes how the table is written, committed and cleaned up, read live from the catalog. Each row shows the current value and where it comes from, Cryo's recommendation, and the risk of changing it in that direction. The ⓘ next to a name explains the setting, and the risk badge explains the consequence.

cryo.local/tables/analytics.events?tab=config

*Rows that differ from the recommendation carry a bar on the left. Click the first tile to show only those.*

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

### Archive

Advisory storage-class suggestions per partition, from the archive policy that covers the table, with the age, suggestion, reason codes and modelled saving. In advisory mode it doesn't change any storage class. **Preview plan** shows what an archive run would move and which guardrails it would check.

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

### Optimization checklist

Twelve weighted checks of optimization readiness across Trino, S3 and Iceberg, such as file size against the target, delete-file ratio, whether the partition scheme matches query predicates, and whether compaction keeps up with write velocity. Open the list to see each check's reason and weight, attention items first.

Two different scores

The checklist score is separate from the health grade. The grade is 100 minus fixed deductions; the checklist is a weighted readiness score for ranking work. A table can pass most checks and still grade F.

## Cost & Savings

What the table costs to keep in object storage, and what maintenance could give back.

cryo.local/tables/analytics.events?tab=cost

*Every dollar figure is an estimate and says which rate or model produced it.*

### What this table costs

Estimated storage per month at the AWS S3 Standard rate, request cost from recorded S3 activity in the last 30 days, and modelled monthly savings before execution costs. The precision badge says whether the numbers are estimated or measured, and when the cost scan ran.

### Where storage accumulates

Live data, dead bytes still held by snapshots, metadata, and orphan files outside the table's metadata. The bars are byte counts, not shares of a total.

### Where maintenance could help

Projected storage cost after maintenance and the bytes that could be reclaimed, broken down by operation when a cost scan recorded it. **Review maintenance** goes to the Maintenance tab.

### Storage tiering

Whether S3 Intelligent-Tiering would pay off for this table, based on how recently it was read. It is a separate estimate from the per-partition archive advice on the Usage tab.

### Compaction economics

Modelled savings over 30 days for up to 10 ranked partition candidates. **Open compaction ranking** shows the candidates. The model has a different scope from the storage savings above, so don't add the two together.

## Where to go next

Health and costHow the score is built from deductions, with a worked example.MaintenanceThe fixed operation order, admission and what a run records.EnginesWhich engine runs which operation, and why some can't.Connect a lakehouseAdd a catalog so its tables show up here.

Source: https://tech-pw.github.io/cryo/table-detail/
Markdown: https://tech-pw.github.io/cryo/table-detail/index.md
