---
title: Health and cost
description: How Cryo grades tables from A to F, how deductions work with a worked example, and what each maintenance operation projects.
canonical: https://tech-pw.github.io/cryo/health/
updated: 2026-11-13
---

# Health and cost

The scanner grades a table from stored metadata. It does not scan data files on the request path, and object storage is not an input.

## Grades

Every table starts at 100 points and loses points for each problem the scan finds. The grade is the letter for the final score. The home chart counts tables per grade. It is not a size-weighted median. UI words map to letters: A Excellent, B Good, C Fair, D Poor, F Critical.

| Grade | Score |
| --- | --- |
| **A** | 90 or more |
| **B** | 75 to 89 |
| **C** | 60 to 74 |
| **D** | 40 to 59 |
| **F** | Below 40 |

The home screen rolls those grades up across the lake, so you can see at a glance how many tables need work and how much storage maintenance could give back. It shows whether the count of D and F tables moved since yesterday, which D and F tables people query most, and which tables Cryo moved up a grade in the last 24 hours. A table only counts as fixed once a scan after its maintenance job shows the better grade. The [quickstart](https://tech-pw.github.io/cryo/quickstart/#next) shows that screen after the first scan of the seeded lake.

## Storage impact on home

The **Storage impact** card at the top of home answers one question: is Cryo making the lake smaller? Every figure carries a **Measured** or **Estimate** label.

- **Last 24 hours (measured).** Bytes freed by successful cleanup actions, the number of tables cleaned and a monthly saving at list price. "Measured on 75% of actions" means three in four actions reported a byte count. The rest still ran, they just were not counted. Below it, a running total of bytes freed since the first daily report, counting measured days only.
- **Where storage goes (estimate).** One bar splits the lake into live data, snapshot history still inside retention, dead data that snapshot expiry frees, orphan files that orphan cleanup frees, and metadata. Dead and orphans are marked **freeable**. Orphans and retained history need an S3 listing of the table, so the card says how many tables have one ("Storage listing for 812 of 1,044 tables"). A compaction estimate sits under the bar.
- **Last 60 days.** Total storage, live data and the running total freed by Cryo, one point per daily report. The gap between total and live is what maintenance can still remove. Days recorded before this card existed show the total only.
- **Versioned buckets.** When the scanner reports that a bucket has versioning on without a noncurrent-version expiration rule, a warning names the bucket. Deletes there leave billed old versions, so cleanup does not lower the bill until a lifecycle rule is added.

When there is nothing to show yet, the card says why and what fills it in, instead of showing zero.

cryo.local/

*The Storage impact card with demo data. Measured results on the left, the estimated breakdown on the right, the 60-day trend below.*

## Deductions

There are two deduction sizes and no others: a warning costs 8 points and a critical finding costs 20. Two warnings and one critical is 100 − 8 − 8 − 20 = 64, grade C. The [seeded demo run](https://tech-pw.github.io/cryo/maintenance/#canonical-order) is the same lake, not a production fleet.

Worked example: how a table reaches 64.

| Step | Points | Running score |
| --- | --- | --- |
| Start | | 100 |
| Small-file fragmentation (warning) | −8 | 92 |
| Manifest files bloat (warning) | −8 | 84 |
| Position delete file buildup (critical) | −20 | 64 |
| Final score and grade | | **C** 64 |

### Fragmented partitions

This check counts files a partition holds beyond what its data needs, not the raw file count. A partition needs one file per 75% of the target file size (`write.target-file-size-bytes`, or 256 MB when unset), because Iceberg compaction leaves files above that size alone. A 150 GB unpartitioned table in 448 files of 345 MB needs about 600 files, so it has no excess and loses no points. A partition of 448 files of 2 MB needs 5, so it has 443 extra files and loses 20. The warning and critical limits are 50 and 200 extra files per partition, scaled up for tables over 100 GB. When the table has no compaction candidates, the Overview shows no Compact files button for this check.

On the [Tables page](https://tech-pw.github.io/cryo/tables/), every row carries its grade, its score change over 7 days and the evidence behind it: small files out of total files, size and reads. The **Health triage** view lists grade D and F tables first.

cryo.local/tables

*The Tables page. A grade is never shown without the counts that produced it, and every row says what maintenance last did.*

Open a table to see its score and how it got there. This one scores 28: it has lost 72 points to two critical findings and four warnings.

cryo.local/tables/analytics.events

*A grade F table. The score gauge, the compaction candidates and the snapshot count all come from the last scan.*

## Archived tables

A table with no commit in 30 days loses 20 points for **Stale table commits**. That is right for a table that should be written to, and wrong for a backup, a snapshot copy or a retired table that is read-only on purpose. Mark those tables **archived**.

- **Where:** the table's Config tab (**Lifecycle**, admin only), or **Mark as archived** in Path to A. You type the table name and give a reason, and the change is audited. **Return to active** undoes it.
- **Health:** the stale-commit check stops applying to the table. Every other check still counts, at the same weight, and the stored grade updates at once.
- **Maintenance:** plans stop compaction, delete rewrites, data expiry, partition-spec migration and statistics, and Path to A stops proposing sort, layout and property changes. Snapshot expiry, orphan cleanup and manifest rewrites keep running, and cost reporting is unchanged.
- **Finding them:** archived tables carry an **Archived** badge on the Tables list and the table header. Filter by **Lifecycle** in the list's advanced filters.
- **Suggestions:** an active table with no commit for 90 days or more, and no recorded reads in 30 days, is shown as an archive candidate. Cryo never archives a table on its own.

## Projection: from scan to stored cost

The cost estimator turns the scanner's stored statistics into a projected byte count for each operation and stores it. Nothing is read from object storage to produce it. Orphan-file precision is an opt-in scanner tier. Without it, the number is flagged as last-known.

Figure 1Cost is estimated from scan stats, without reading object storage

*Object storage is not an input to the projection.*

- **Write or execute
Text description
- Step 1: Scanner to Cost estimator (stats)
- Step 2: Cost estimator to Stored projection (store)
- Object storage is not read by Cost estimator

Bytes first, then a rate

Projections are byte classes: live data, expirable, manifests and metadata, and orphan candidates. The bytes are what Cryo measures. The [Cost & Savings tab](https://tech-pw.github.io/cryo/table-detail/#cost) multiplies them by the AWS S3 Standard rate to show a monthly figure, labelled as modelled. For a table outside S3 it shows bytes only, because the rate wouldn't apply.

## What each operation projects

| Operation | Projected effect |
| --- | --- |
| `expire_snapshots` | Bytes referenced only by snapshots the retention policy would drop, and only when no surviving snapshot still references the file. |
| `remove_orphan_files` | Bytes present in storage but referenced by no snapshot. Precise only on the scanner's opt-in listing tier. |
| `optimize` | Delete-file bytes merged away. Data bytes are rewritten, so the storage change shows up in the later expiry projection. |
| `optimize_manifests` | Manifest and metadata bytes reclaimed by consolidation. Small in volume, large in plan-time latency. |

## Where to go next

MaintenanceThe fixed operation order. It does not clear every cause of a low grade, including format upgrades, streaming velocity, property settings, and partition skew.How Cryo fitsWhy the scan path never touches a data file.ArchitectureWhere the scanner and cost estimator sit in the running system.ReferenceHow Cryo's health grading compares with adjacent tools.

Source: https://tech-pw.github.io/cryo/health/
Markdown: https://tech-pw.github.io/cryo/health/index.md
