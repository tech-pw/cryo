---
title: Cryo
description: Cryo is an Apache-2.0, self-hosted Apache Iceberg maintenance control plane. Connect your lake and keep every table healthy without deciding when or how to optimize it — Cryo ranks the work from real query workload and explains every decision.
canonical: https://tech-pw.github.io/cryo/
updated: 2026-10-06
---

# Cryo

Cryo reads your Apache Iceberg catalogs, ranks what is worth doing from real query workload, and admits the work to its native engine when the cluster has headroom. Reading and writing never share a code path, and every run leaves a receipt.

Pre-1.0 · Apache-2.0→

# Keep every table healthy without deciding when or how.

Cryo reads your catalogs, plans compaction and cleanup, and admits the work to its native engine when the cluster has headroom — every decision explained.

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

trino-a full
trino-b this job

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

The life of one table

## Thirty days of writes. One maintenance run.

Follow a 1 TB Iceberg table through a month of streaming writes, then through each Cryo operation in order. Beside it: what is inside the table, and what S3 charges for it.

Day 1

### Meet `analytics.events`

Monthly bill **$23.53** Healthy. What this table should cost.

1 TB of events in about 2,000 Parquet files of 512 MB, one partition per day. A dashboard that reads one day opens 66 files. Beside it is the table as Cryo sees it: a core sample, cut open so you can see inside.

**What it costs** $23.00 a month to store. $0.53 a month in GET requests for 20,000 dashboard queries.

Day 1 to day 30, no maintenance

### Thirty days of streaming writes

Day 1 **$23.53** → Day 30 **$110.72** 4.7× the bill for the same rows

A job commits every five minutes, 288 times a day. Each commit adds a few small files and a snapshot. Upserts add delete files instead of rewriting rows, and failed tasks leave files that no snapshot ever recorded. Scroll slowly and watch the day count.

**In plain terms** S3 charges postage per envelope, not per page. The table still holds 1 TB of rows, but it now arrives in 250,000 envelopes.

**What decayed** 250,000 files of about 4 MB, 40,000 delete files, 8,640 snapshots, 300 GB of replaced files that old snapshots still hold, and 100 GB of orphans.

**What it costs** The same rows now cost $110.72 a month. Storage rose to $33.12, but requests rose more: one day of data is about 9,700 files, so the same dashboards make 194 million GETs, $77.60.

**The cost that isn't on the bill** When 50 dashboards refresh at 9 a.m., they ask this table for 485,000 files at once. S3 supports at least 5,500 GET requests a second per prefix and scales up gradually; while it scales, it answers `503 Slow Down`. [AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance-design-patterns.html)

Step 1 of 6 · `convert_deletes` · `rewrite_position_deletes`

### Fold the corrections back in

Before **$110.72** → After **$102.08** $8.64 a month saved

On a merge-on-read table, every read opens the delete files for its partition and filters rows out as it scans. That work is not done once. It is repeated by every one of the 20,000 queries this month.

**In plain terms** A report with a stack of correction slips. Every reader checks every slip, every time, before trusting a line.

**What Cryo does** `convert_deletes` writes the deletes into new data files. On Spark, `rewrite_position_deletes` first merges small position-delete files.

**On the bill** A one-day query drops from about 9,700 files to 8,333, and requests fall to $66.66. The replaced files stay until their snapshots expire, so storage rises to $35.42.

Step 2 of 6 · `optimize`

### Pack the snow into ice

Before **$102.08** → After **$58.95** $43.13 a month saved

Files per query **8,333** → After **66** 126× fewer GETs

Every file a query opens costs at least one GET and one round trip to S3. Cryo rewrites each day into 512 MB files, a few partitions per chunk, and only while Trino has headroom. An oversized partition runs alone. This step is the picture in Cryo's logo: small cubes fused into one block.

**In plain terms** Binding loose pages back into books. The same words, for a fraction of the postage.

**On the bill** One day of data is 66 files again. Requests drop from $66.66 to $0.53 a month.

**Storage went up, not down.** Compaction writes new files, and old snapshots still reference the old ones, so S3 now holds both: 2.54 TB, $58.42 a month. Cleanup is what turns compaction into savings.

**The cost that isn't on the bill** Even a healthy small GET waits tens of milliseconds for S3 to answer. With 64 readers in parallel, 9,700 files is about 150 rounds of that wait before the last file arrives. 66 files is two. [AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance-design-patterns.html)

Step 3 of 6 · `compute_statistics`

### Give the planner real numbers

Join plans **guessed** → After **from real counts** Same bill, better plans

Trino chooses join order, and whether to broadcast a table to every node, from table statistics. After a month of writes, the distinct-value counts on this table are missing or stale.

**In plain terms** A road map with distances. Without one, you drive the roads in the order someone wrote them down.

**What Cryo does** Collects distinct-value counts into a Puffin statistics file and registers it in the table metadata.

**On the bill** No change. Joins against this table are planned from counts instead of guesses.

**The cost that isn't on the bill** Without statistics, Trino stops reordering joins and runs them in the order the SQL lists them. [Trino](https://trino.io/docs/current/optimizer/cost-based-optimizations.html)

Step 4 of 6 · `optimize_manifests`

### Tidy the index drawer

Manifests per plan **640** → After **12** 53× less to read first

Before it reads any data, a query reads the manifest list, then every manifest that might match its filter. 288 commits a day leave hundreds of small manifests behind.

**In plain terms** A library catalogue spread over 640 thin drawers instead of 12 full ones. The same cards, but many more drawers to open before you find a book.

**What Cryo does** Rewrites them into a few large manifests grouped by partition.

**On the bill** Planning reads 12 manifests instead of 640. Queries start sooner; the bill barely moves.

Step 5 of 6 · `expire_snapshots`

### Lift out the oldest layers

Before **$58.95** → After **$25.83** $33.12 a month saved

Iceberg deletes a data file only when no snapshot references it. Every file that compaction replaced is still sitting in the old layers, and still on the bill.

**In plain terms** Keeping every saved version of a document. Each one looks free. Together they are now the biggest line on the bill, $33.12, more than the live data.

**What Cryo does** Keeps 7 days of snapshots by default. For a table that commits 288 times a day and already carries 8,640 snapshots, it shortens retention to 1 day. On Trino, lower the catalog's `iceberg.expire-snapshots.min-retention` from its 7-day default first, or Trino rejects anything shorter.

**On the bill** 1.44 TB leaves S3. Storage falls from $58.42 to $25.30 a month, and one day of time travel remains.

**The cost that isn't on the bill** A row you deleted, for a GDPR request say, still exists in old files until the snapshots holding them expire. And every commit writes a new metadata file that lists every snapshot, so 8,640 of them make that first read of every query longer. [Iceberg](https://iceberg.apache.org/docs/latest/maintenance/)

Step 6 of 6 · `remove_orphan_files`

### Sweep up the strays

Before **$25.83** → After **$23.53** $2.30 a month saved

Failed writes and retried tasks leave files in the table's S3 prefix that no snapshot ever recorded. Expiring snapshots cannot find them, because nothing points to them, so they stay on the bill for good.

**In plain terms** Boxes in a storage unit with no label and no inventory entry. Nobody will ever ask for them, and the rent still comes every month.

**What Cryo does** Lists the table location and deletes files that no metadata references and that are older than 7 days, so writes still in flight are never touched.

**On the bill** 100 GB gone. Storage is back to $23.00.

Receipt

### Same rows. Same queries. 79% less.

Day 30 **$110.72** → After one run **$23.53** 79% less, every month

One run took the table from $110.72 to $23.53 a month. Each step wrote an audit row with the table, operation, engine, status and duration, so the saving can be checked, not just claimed.

**Cryo maintenance receipt** `analytics.events` · Trino

| Operation | Saved per month |
| --- | --- |

| `convert_deletes` | $8.64 |

| `optimize` | $43.13 |

| `compute_statistics` | better joins |

| `optimize_manifests` | faster planning |

| `expire_snapshots` | $33.12 |

| `remove_orphan_files` | $2.30 |

| Saved | $87.19 |

6 operations · 6 audit rows · 0 skipped

[The maintenance flow and its guardrails →](https://tech-pw.github.io/cryo/maintenance/)[How Cryo projects cost from scan data →](https://tech-pw.github.io/cryo/health/#projection)

Illustrative example: one 1 TB table partitioned by day, a commit every five minutes, and 20,000 dashboard queries a month that each read one day. Prices are [S3 Standard in us-east-1](https://aws.amazon.com/s3/pricing/): $0.023 per GB-month and $0.0004 per 1,000 GET requests. Request counts assume one GET per file opened, which is a floor. Each step is shown at full effect with no new writes in between. File lifecycle follows the [Iceberg maintenance docs](https://iceberg.apache.org/docs/latest/maintenance/). Cryo's scanner reports the real numbers for your tables.

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

- 01 [Aamir Siddiqui](https://github.com/aamirpw306) Data Platform

- 02 [Shubham Maurya](https://github.com/ShubhamMaurya9) Data Platform

- 03 [Piyush Kumar](https://github.com/piyush-kumar20) Data Platform

- 04 [Niteesh Kumar Pandey](https://github.com/niteesh-pw) Data Platform

Source: https://tech-pw.github.io/cryo/
Markdown: https://tech-pw.github.io/cryo/index.md
