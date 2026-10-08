---
title: Health and cost
description: How Cryo grades tables from A to F, how deductions work with a worked example, and what each maintenance operation projects.
canonical: https://tech-pw.github.io/cryo/health/
updated: 2026-10-06
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

The home screen rolls those grades up across the lake, so you can see at a glance how many tables need work and how much storage maintenance could give back.

cryo.local/

*Lake health on the home screen. Each slice is a grade; the panel on the right is Cryo's own platform health, not a table grade.*

## Deductions

There are two deduction sizes and no others: a warning costs 8 points and a critical finding costs 20. Two warnings and one critical is 100 − 8 − 8 − 20 = 64, grade C. The [seeded demo run](https://tech-pw.github.io/cryo/maintenance/#canonical-order) is the same lake, not a production fleet.

Worked example: how a table reaches 64.

| Step | Points | Running score |
| --- | --- | --- |

| Start |  | 100 |

| Small-file fragmentation (warning) | −8 | 92 |

| Manifest files bloat (warning) | −8 | 84 |

| Position delete file buildup (critical) | −20 | 64 |

| Final score and grade |  | **C** 64 |

In the table list, every row carries its grade and the evidence behind it: small files out of total files, size, and when the scanner last looked. Sorting by grade puts the tables that need work first.

cryo.local/tables

*The table explorer. A grade is never shown without the counts that produced it.*

Open a table to see its score and how it got there. This one scores 28: it has lost 72 points to two critical findings and four warnings.

cryo.local/tables/analytics.events

*A grade F table. The score gauge, the compaction candidates and the snapshot count all come from the last scan.*

## Projection: from scan to stored cost

The cost estimator turns the scanner's stored statistics into a projected byte count for each operation and stores it. Nothing is read from object storage to produce it. Orphan-file precision is an opt-in scanner tier. Without it, the number is flagged as last-known.

Figure 1Cost is estimated from scan stats, without reading object storage

*Object storage is not an input to the projection.*

- **Write or execute
Text description
- Step 1: Scanner to Cost estimator (stats)

- Step 2: Cost estimator to Stored projection (store)

- Object storage is not read by Cost estimator

Bytes, not money

A modelled saving is not shown as money anywhere in Cryo or on this site. Projections are byte classes: live data, expirable, manifests and metadata, and orphan candidates.

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
