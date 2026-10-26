---
title: Benchmark
description: Measured results from CryoBench V2. Cryo versus scheduled Spark and hand-run Spark on table health, a 100 GiB table, live CDC, and query impact, plus the trade-offs.
canonical: https://tech-pw.github.io/cryo/benchmark/
updated: 2026-10-23
---

# Benchmark

We ran Cryo against the scheduled Spark jobs teams usually write for themselves, on real Iceberg tables, with three trials per test and every row checked afterwards. Here is what we measured.

151 / 151Cryo maintenance jobs succeededAcross every test, with no failed or partial commits.
0rows lost or changedTable fingerprints matched the untouched copy after every run.
4.3×fewer delete files under live CDCTime-weighted average of 4.9 vs 20.9, while the scheduled DAG's compactions all failed.
1.76×faster queries on a 100 GiB tableGeometric mean of the whole query mix. Up to 4.3× on individual queries.
79%fewer pointless rewrites than a scheduled DAG43% fewer unnecessary operations once snapshot expiry is counted.
≤ 4%p95 slowdown for live queries during compactionEvery live query stayed between 0.88× and 1.04× of idle, with zero failures.

## What we compared

The real alternative to Cryo is usually not another product. It is a Spark job on a schedule that someone on the team wrote and now has to maintain. So that is what we measured Cryo against, using the same Spark and Iceberg versions and the same cluster.

No maintenance

The table as ingestion leaves it. Sets the baseline.

Scheduled Spark DAG

Compact, rewrite manifests and expire snapshots on every table, every cycle. The common Airflow pattern.

Hand-run Spark

An engineer running the exact compaction SQL by hand, on the large-table test.

Cryo

Default settings. Grades each table, plans only what it needs, runs it on Spark.

For a feature comparison with Amoro, LakeOps, Crunch, Databricks and managed cloud services, see [Reference](https://tech-pw.github.io/cryo/reference/). This page reports only what we ran and measured.

## It touches only the tables that need work

We gave both approaches the same fleet: healthy tables, quiet ones, fragmenting ones, badly fragmented ones, and tables taking row updates. Over four cycles, the scheduled DAG rewrote everything it saw. Cryo left healthy tables alone and spent its work where the problems were.

*Green chips are rewrites that fixed a real problem. Orange chips rewrote a table that was already healthy. Both approaches expire snapshots on every table by design.*

### Unnecessary rewrites per trial

Scheduled DAG48
Cryo10

Identical in all three trials. Counting snapshot expiry, it is 88 vs 50.

### Every table ended healthy

No maintenance66%
Scheduled DAG100%
Cryo100%

Share of query runs that hit a healthy table. Cryo matched the brute-force DAG while doing less of the work nobody needed.

## On a 100 GiB table, queries get faster

One table, 100 GiB, fragmented the way streaming ingestion leaves it. One Cryo run at the default 256 MB target turned thousands of small files into a few hundred right-sized ones.

*67 GiB rewritten in one run of about 21 minutes. Same rows and same results, a tenth of the files.*

Query speed after one Cryo run

*Scans and aggregates gained the most. Single-row lookups got slower because each one now opens a larger file; see [trade-offs](#tradeoffs).*

1.73× – 1.83×

### Whatever target size you pick

128, 256 and 512 MB targets all landed in this range for the whole query mix. The default is a safe choice.

690 = 690

### Same output as hand-written Spark

Cryo's compaction produced exactly the same file count, bytes and results as an engineer running the same SQL by hand.

538 – 802

### Queries to pay back the rewrite

Customer filters repaid the compaction compute after 538 runs, whole-table aggregates after 588 and full reads after 802.

## Under live CDC, it keeps up

This is the case that breaks naive schedules. A MERGE landed on the table every 200 seconds for two hours. The scheduled DAG's compactions collided with the writer and failed every time. Cryo converted delete files in small commits between writes and kept the table lean.

- **No maintenance
- **Scheduled Spark DAG
- **Cryo

*The no-maintenance and scheduled-DAG lines overlap because the DAG's work never committed. Ticks along the bottom mark Cryo's checks.*

22 / 22

### Cryo jobs succeeded

Every job finished while the writer kept committing.

0 / 3

### DAG compactions committed

All three failed validation against the concurrent MERGE.

40 → 3

### Data files at the end

Same rows, and aggregate queries ran in 0.58× the time.

## Users don't feel it

We ran a steady stream of dashboard-style queries against the 100 GiB table while Cryo rewrote 67 GiB of it, then compared with the same queries on an idle table.

- **Idle table
- **During Cryo compaction

*p95 stayed within 0.88× to 1.04× of idle across every query, and p99 improved for all three. Zero query failures.*

## Scorecard

| Measured outcome | No maintenance | Scheduled Spark DAG | Cryo |
| --- | --- | --- | --- |
| Fleet restored to healthy | No | 100% | 100% |
| Leaves healthy tables alone | n/a | No48 pointless rewrites | Mostly10 not needed, none on healthy tables |
| Keeps up with live CDC | No39 delete files | No3 of 3 failed | Yes3 to 6 delete files |
| Data integrity | Intact | Intact | Intact |
| Live-query p95 during maintenance | n/a | not measured | ≤ 4% slower |
| Decides what to run | n/a | You do | Cryo does |
| Grade, history and audit trail per table | No | Build it | Built in |

## Trade-offs

A benchmark you can trust shows the costs too. These are the ones we measured.

Spark compute

Cryo used about twice the Spark compute of the scheduled DAG on small tables, and 1.67× hand-run SQL on the 100 GiB table. It starts one Spark application per operation and expires snapshots on every table. Compute is cheap next to engineer time, but Cryo is not a compute saver.

Point lookups

Bigger files make single-row lookups slower (1.8× at 256 MB). If your workload is mostly point reads, use a smaller target or skip compaction for that table.

Latency to start

Jobs waited a median of about 3 minutes in the queue before Spark started. Fine for maintenance; not built for second-level reaction.

Rough edges

We filed what we hit: run status staying queued ([#153](https://github.com/tech-pw/cryo/issues/153)), chunked scans skipping tables ([#154](https://github.com/tech-pw/cryo/issues/154)), and missing file metrics on Spark-on-Kubernetes jobs ([#155](https://github.com/tech-pw/cryo/issues/155)). None affected data integrity.

## How we measured

- **3 trials**per test, 95% confidence intervals
- **100 GiB**largest single table
- **2 hours**of continuous MERGE for CDC
- **Every row**fingerprinted before and after
- **Same stack**Spark 4.1 and Iceberg 1.11 on Kubernetes, for every contender
- **Read-only**Trino for all query timing

Cryo ran with default settings throughout. The protocol was written and frozen before any scored run, compute was measured from Kubernetes pod requests, and rewritten bytes came from Iceberg's own snapshot summaries. Discovery was also checked at fleet scale: all 100 tables in a 100-table catalog were discovered, with exact file counts, in about two and a half minutes.

Try it on your own tables

The fastest way to see this on your lake is the Quickstart. Point Cryo at a catalog, let it grade every table, and review the plan before anything runs.

Source: https://tech-pw.github.io/cryo/benchmark/
Markdown: https://tech-pw.github.io/cryo/benchmark/index.md
