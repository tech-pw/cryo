# Archive and storage tiering for Cryo: research and recommendation

**Date:** 2026-10-01  
**Status:** P0 signals and P1 advisory landed in the working tree on 2026-10-01. Not committed. No S3 writes, no production configuration change, no deploy.  
**Question:** Can Cryo add an *Archive* section to the table-detail page, beside *Partition access*, that works like S3 Intelligent-Tiering but is Iceberg- and partition-aware: move cold partitions to cheaper tiers (up to Glacier) and bring them back when needed? How do competitors, and Cryo itself, handle it today?

**Evidence grades used below:** **R** primary source re-read in this session · **A** read by a research sub-agent, not re-fetched · **L** live Cryo API, read-only, 2026-10-01 · **C** code read in this repo · **I** inference. Source URLs are in [§11](#11-sources).

## Implementation status (2026-10-01)

The research below is unchanged. This section records what the follow-up implementation actually did. Unknown is not cold. Nothing copies or restores an S3 object.

| Item | Result |
|---|---|
| Recommender cold signal | Done. `analyze_table_for_tiering` falls back to `table_query_activity`. Missing `query_logs` and activity is `recommendation="unknown"`, `days_since_last_access=null`, annual savings `0`. It no longer says "not accessed in 90 days" or "82% cost reduction". |
| Placeholder savings | Done. `summarize_tier_savings` returns `estimated_savings_usd_month=null` and `note="not measured"` when candidate bytes are missing. |
| Mumbai prices | Done for `ap-south-1` in `cryo/cost/s3_pricing.py`. `us-east-1` and unknown regions are unchanged. |
| `last_write_at` | Done in both scanners, inside partition `raw` only. UTC ISO-8601 with milliseconds and a `Z` suffix. Omitted when no snapshot timestamp exists. Never `now()` and never a fake epoch. |
| Write signal for archive | Done. `partition_has_write_signal` is false when `last_write_at` is missing. Compaction's `_partition_is_cold` still treats a missing write as cold. That split is intentional. |
| Storage-class checklist | Done. Readable: `STANDARD`, `INTELLIGENT_TIERING` with no `ArchiveStatus`, `STANDARD_IA`, `ONEZONE_IA`, `GLACIER_IR`. Red: `GLACIER`, `DEEP_ARCHIVE`, and Intelligent-Tiering with `ArchiveStatus`. No sample stays unknown. Sampling still depends on file paths being passed in. |
| Partition-access query | Done. Aggregates in SQL and caps the read. Production emptiness and stale log ingest are unchanged. |
| Policy form | Done. Options are Cool (`STANDARD_IA`), Archive-instant (`GLACIER_IR`), and Auto (`INTELLIGENT_TIERING`). `GLACIER` and `DEEP_ARCHIVE` are not offered. |
| Advisory Archive section | Done, then moved on 2026-10-18 to Cost & Savings (`#archive`) and made executable. See [`ARCHIVE_EXECUTION_2026-10-18.md`](./ARCHIVE_EXECUTION_2026-10-18.md). |
| Dry-run plan | Done. `GET /api/v1/tables/{id}/tiering`, `GET .../tiering/partitions`, `POST .../tiering/plan`. No apply or restore route. |
| Missing read evidence | Caps the suggestion at Auto (`No evidence — Auto only`). The log-delivery delay is not stored, so the route treats read evidence as missing. |
| S3 log ingest repair | Not done. Bronze, silver, gold, and temp can still fail the date-partitioned listing. |
| `partition_last_access` rollup | Not done. "No read in 90 days" still cannot be proven after the 35-day prune. |
| Bytes by class on the source page | Not done. |
| Feature flag, OpenAPI client types, operation-cart card | Not done. |
| Execute, restore, IAM allow-list, realized savings | Execute, restore and the IAM policy done 2026-10-18 ([`ARCHIVE_EXECUTION_2026-10-18.md`](./ARCHIVE_EXECUTION_2026-10-18.md)). Realized savings not done. |
| Retrieval-cost guard | Not started (P3). |
| Offline tiers | Still deferred (P4). |

---

## 1. Answer

1. **Yes, but build the right thing.** The product that fits Iceberg is *partition-aware tiering to instant-access classes*: S3 Intelligent-Tiering where read evidence is thin, Glacier Instant Retrieval (or Standard-IA) where reads are provably rare, with a Cryo-owned ledger, guardrails and a one-click restore. It is **not** "Glacier for Iceberg". Objects in Glacier Flexible Retrieval, Deep Archive and the Intelligent-Tiering archive tiers fail with `InvalidObjectState` (HTTP 403), and no engine we run can skip them on an Iceberg table (§4.5).
2. **The opportunity is real.** In the 150 largest date-partitioned tables (154.7 TB), 79% of bytes sit in partitions older than 90 days, 61% older than 180 days and 36% older than a year (§3, **L**). At Mumbai list prices, moving the ≥180-day partitions to Glacier Instant Retrieval is worth at most about **$1.8k/month (≈ $22k/year, 23% of August's S3 storage line)**, and only if those bytes are not read.
3. **Three existing Cryo gaps block it, and they come first** (§2): the shipped tiering recommender is wrong in production (it tells large, actively queried tables "not accessed in 90 days"); there is no usable per-partition access or last-write signal (partition access is empty for all 25 large tables sampled, S3 log ingestion for bronze/silver/gold/temp has been stale since mid-September, and neither scanner records `last_write_at`); and Glacier-class objects are already making fleet tables unreadable with nothing watching for it.
4. **No Iceberg vendor ships this.** None of the 14 Iceberg maintenance vendors, catalogs and managed services checked offers Iceberg-aware tiering (§5). The nearest analogues are Databricks Delta archival (Delta only, Public Preview), Snowflake storage lifecycle policies (native tables only), S3 Tables Intelligent-Tiering (file-level, instant tiers only), Yelp (in-house, Intelligent-Tiering default plus IAM-gated access) and Uber (HDFS to S3 partition archive and restore). That is a differentiator, and also a warning that it is hard to do safely.
5. **Recommended order:** P0 fix the signals and the existing recommender → P1 advisory *Archive* section → P2 execute in-place class changes to instant-access classes, with restore → P3 closed-loop retrieval-cost guard → P4 offline tiers only if a concrete need appears (§8). Offline tiers add little in Mumbai: Glacier Flexible Retrieval is only ~10% cheaper than Glacier Instant Retrieval and Deep Archive adds about $300/month per 100 TB (§4.1).

---

## 2. What Cryo has today

### 2.1 Inventory

| Piece | Where | What it does | Gap for archiving |
|---|---|---|---|
| Cost-tab "Storage tiering" card | [`TableCostPanel.tsx`](../web/src/components/table-detail/cost/TableCostPanel.tsx) (`#cost-tiering`) → `GET /tables/{id}/s3-tiering` | Whole-table S3 Intelligent-Tiering recommendation and annual savings | Whole table only; wrong cold signal (§2.2) |
| Governance KPI and Policies tab | [`GovernanceKpiRow.tsx`](../web/src/pages/governance/GovernanceKpiRow.tsx), [`PoliciesTab.tsx`](../web/src/pages/governance/PoliciesTab.tsx) | Fleet "potential savings" from `GET /s3-tiering-recommendations`; tier-policy CRUD; per-table candidate list | Policy form offers `GLACIER` and `DEEP_ARCHIVE` (unreadable on Iceberg) and lacks `GLACIER_IR`; nothing executes |
| Tier policies and candidates | `tier_policies` ([`TierPolicyRecord`](../cryo/db/models.py)), [`cryo/tiering/`](../cryo/tiering/), [`phase8_routes.py`](../cryo/api/phase8_routes.py) | Age-since-`last_write_at` candidates, placeholder savings | `last_write_at` is never produced (§2.2) |
| Partition access | [`PartitionAccessDaily`](../cryo/db/models.py), [`routes_partition_access.py`](../cryo/api/routes_partition_access.py), Usage tab | GET counts per table, day, partition path and role, from S3 server access logs (Yelp-style, [plan](./PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md)) | 35-day retention; empty in production (§2.3); unbounded read |
| Partition stats | [`TablePartition`](../cryo/db/models.py) | Per-partition bytes, files, small files, values, transform; capped at 5,000 partitions per table | No write time, no read time |
| Storage-class checklist item | [`_check_storage_class`](../cryo/scanner/checklist.py) | `HeadObject` on up to 5 sample files; non-`STANDARD`/`INTELLIGENT_TIERING` is red | Inert (no file sample in production); wrong class semantics (§2.4) |
| AWS integration | [`aws_s3_metrics.py`](../cryo/integrations/aws_s3_metrics.py) | CloudWatch `BucketSizeBytes` by `StorageType`, Price List API; static keys, read-only | Bucket level only; not shown per table |
| Durable intents and receipts | `maintenance_intents`, `effect_receipts`, fences ([ledger doc](./WORKER_STEP_LEDGER.md)) | Write-ahead record of every external effect | Reusable as the safety kernel for tiering |
| Cost self-check | internal CUR notes, not published | bronze had Intelligent-Tiering, gold had none | See §4.3 |

### 2.2 The shipped recommender is misleading in production (C, L)

* **Cold signal, as observed on 2026-10-01 before the code fix.** `analyze_table_for_tiering` ([`s3_analyzer.py`](../cryo/tiering/s3_analyzer.py)) read only `QueryLog.executed_at`. A source in `iceberg_table` query-log mode writes `table_query_activity` and no `query_logs` rows (the same trap [`trino_hot_tables.py`](../cryo/planner/trino_hot_tables.py) already works around). `days_since_last_access` therefore fell back to the 90-day lookback, and every table whose average file is at least 128 KB got "enable … Archive Instant Access (82% cost reduction)". The working tree now falls back to `table_query_activity` and returns `unknown` with zero savings when both sources are empty.
* **Live check.** Sampled tables returned that text, including tables that had recent queries. Replaying the formula over the largest tables flagged many as "enable" even when they had been queried. The replay was offline rather than against the fleet endpoint.
* **Candidates were empty on 2026-10-01.** `GET /tables/{id}/tier-candidates` needs `raw.last_write_at`. At the time of the probe neither scanner wrote it, so 6 of 6 sampled tables returned 0 candidates. Both scanners now write `last_write_at` into partition `raw` when a snapshot timestamp exists. That does not fill history for partitions already scanned. `summarize_tier_savings` no longer multiplies a placeholder 10 GB by the candidate count.
* **Same missing field, opposite default, elsewhere.** The planner's hot-partition guard ([`_partition_is_cold`](../cryo/planner/planner.py)) reads the same never-populated field and treats "unknown" as **cold**. For compaction that is a quiet gap; for archiving it would be dangerous. Rule for everything below: **unknown is not cold.**
* **Prices.** [`s3_pricing.py`](../cryo/cost/s3_pricing.py) hardcodes us-east-1 rates. In Mumbai the absolute per-GB saving for Standard-IA and Glacier IR differs by under 3%, so this is not the main defect; the main defects are the cold signal, the all-or-nothing table model, and ignoring retrieval, request, minimum-duration and monitoring fees.

### 2.3 The access signal is not there yet (L)

* **Partition access is empty.** `GET /tables/{id}/partition-access?days=35` returned no rows for all 25 sampled large date-partitioned tables (72.6 TB, 100 to 1,000 partitions each), although the table-level GET rollups (`get_requests_30d`) were non-zero for the three of them I checked. Cause not diagnosed.
* **Log ingestion is stale for the big buckets.** `GET /sources/{id}/s3-log-configs`: bronze, silver and gold last ingested **2026-09-16**, temp **2026-09-22**; each carries `last_error: "No date-partitioned log directories found under the configured target ..."`. Their logs go to a shared central logging bucket. The most likely cause is the log delivery key format: S3 server access logs default to a flat `SimplePrefix`, and date-partitioned `PartitionedPrefix` delivery (added November 2023) is what a date-partitioned reader needs (**R**, Yelp's account of the same problem). Fixing either side (switch delivery format, or list flat keys by timestamp prefix) is a P0 item.
* **Retention.** `PartitionAccessDaily` is pruned at 35 days, so "no read in 90 days" cannot be proven from it. Server access logs are also best-effort: Yelp saw under 0.001% of logs arrive more than a couple of days late, worst case 9 days (**R**).
* **Unbounded read.** `get_partition_access` loads every (day, partition, role) row into Python and aggregates there, capping only the output at 500 partitions.

### 2.4 Related hazards already present

* **Glacier-class objects under live tables.** [`_check_storage_class`](../cryo/scanner/checklist.py) records that on 2026-09-11 "a rewrite on a fleet table hard-failed with InvalidObjectStateException because backing files had transitioned to a storage class (Glacier/IA-with-restore-required) that doesn't support direct GET". The August CUR shows `TimedStorage-GDA` (Deep Archive) in gold ($27.38 over 60 days) and bronze ($22.12), about 6.7 TB and 5.4 TB at $0.002/GB-month (**I**, derived from [`s3-cost-investigation-2026-08.md`](./s3-cost-investigation-2026-08.md)). Whether any of it is under a live Iceberg table is unknown.
* **The checklist item gets class semantics wrong.** It would flag Standard-IA and Glacier IR (both directly readable) red once Cryo starts using them, and it treats `INTELLIGENT_TIERING` as readable even when the object sits in an Intelligent-Tiering archive tier (`HeadObject` returns `ArchiveStatus` for those; **A**).
* **API headroom.** During the live probe one `cryo-api` replica was OOMKilled (6 GiB limit) while a paged `/partitions` and `/partition-access` loop was running. It had also restarted earlier that morning, before the probe began, and the other replica was holding about 3.8 GiB, so this is probably pre-existing; correlation is not proven. Probing stopped immediately. See §10.

---

## 3. The opportunity, sized with live data (L)

### 3.1 Fleet shape

| Measure | Value |
|---|---|
| Tables in the `cdp` source | 14,795 |
| 3,000 largest tables | **227.3 TB** of live data-file bytes (the 3,000th table is 0.25 GB) |
| Concentration | top 100 tables = 73.6%, top 500 = 95.9% |
| By bucket (those tables) | silver 108.3 TB, bronze 63.5 TB, gold 45.6 TB, temp 7.8 TB, other 2.1 TB |
| Partitioning | date/time-like partition column: **2,060 tables, 186.1 TB (81.9%)**; other partitioned: 17 tables, 29.3 TB (mostly `event_name`); unpartitioned: 923 tables, 11.9 TB |
| Files | bytes-weighted average 93 MB; 2.56 M data files (S3 Intelligent-Tiering monitoring for all of them ≈ $6/month) |
| Zero queries in 30 days | 937 tables, 37.2 TB (16.4% of bytes); deletion may beat archiving here |

Live bytes are not bucket bytes: CloudWatch showed about 316 TB in August (gold 121, silver 82, bronze 75, temp 27). The difference is snapshot-pinned files, orphans, metadata and non-Iceberg data. Archive applies to **live data files of current-snapshot partitions only**; dead bytes should be expired, not archived.

### 3.2 Partition age (150 largest date-partitioned tables, 154.7 TB)

Age is today minus the partition date, parsed from `partition_values` (ISO date, `yyyymmdd`, or month/day transform integers); sizes are the latest scan.

| Partition age | TB | Share |
|---|---:|---:|
| 0 to 30 days | 10.7 | 6.9% |
| 31 to 90 days | 22.0 | 14.2% |
| 91 to 180 days | 27.5 | 17.8% |
| 181 to 365 days | 38.6 | 25.0% |
| 366 to 730 days | 44.7 | 28.9% |
| over 730 days | 11.2 | 7.2% |

### 3.3 Upper-bound savings at Mumbai list prices

Assumes every one of those bytes is Standard today at the marginal $0.024/GB-month (the 50 to 500 TB tier), that none is ever read, and counts storage only (§4.1 prices).

| Policy | Bytes | → Glacier IR | → Standard-IA | Share of August storage line ($8,132 per 30 days) |
|---|---:|---:|---:|---:|
| age ≥ 90 d | 122.0 TB | $2,374/mo ($28.5k/yr) | $1,274/mo ($15.3k/yr) | 29% (Glacier IR) |
| age ≥ 180 d | 94.5 TB | $1,839/mo ($22.1k/yr) | $987/mo ($11.8k/yr) | 23% |
| age ≥ 365 d | 55.9 TB | $1,088/mo ($13.1k/yr) | $584/mo ($7.0k/yr) | 13% |

Caveats: some of these bytes are already cheaper (bronze has Intelligent-Tiering); reads are unknown (§2.3), so net-of-retrieval savings may be much lower; the 150 tables are 83% of date-partitioned bytes, so the full figure is somewhat higher. The shipped KPI's $49.9k/year (§2.2) is about 1.75× this bottom-up upper bound and rests on the wrong evidence. Deleting the zero-query tables outright would be worth up to about $0.9k/month at Standard, which is the same order as archiving; Yelp's 33% came from tiering **and** deletion-based retention ([§5.2](#52-field-evidence)).

---

## 4. S3 and Iceberg mechanics that shape the design

### 4.1 Classes and prices (ap-south-1, AWS Price List published 2026-09-28; **R** for Standard, IA, Glacier IR, Intelligent-Tiering and early-delete, **A** for Glacier Flexible and Deep Archive)

| Class | $/GB-month | Min duration | Retrieval $/GB | GET per 1,000 | Transition or COPY in, per 1,000 | Readable by a normal GET |
|---|---:|---|---:|---:|---:|---|
| Standard | 0.025 (first 50 TB), 0.024 (next 450 TB), 0.023 | none | none | 0.0004 | 0.005 | yes |
| Intelligent-Tiering, Frequent Access | as Standard | none | none | 0.0004 | 0.01 (lifecycle) | yes |
| Intelligent-Tiering, Infrequent (30 d idle) | 0.0138 | see note | none | 0.0004 | | yes |
| Intelligent-Tiering, Archive Instant (90 d idle) | 0.005 | see note | none | 0.0004 | | yes |
| Standard-IA | 0.0138 | 30 d | 0.01 | 0.001 | 0.01 | yes |
| One Zone-IA | 0.011 | 30 d | 0.01 | 0.001 | 0.01 | yes (single AZ) |
| **Glacier Instant Retrieval** | **0.005** | 90 d | 0.03 | 0.01 | 0.02 | yes |
| Glacier Flexible Retrieval | 0.0045 | 90 d | restore 0.012 standard, 0 bulk | after restore | 0.036 | **no** |
| Glacier Deep Archive | 0.002 | 180 d | restore 0.024 standard, 0.005 bulk | after restore | 0.07 | **no** |
| Intelligent-Tiering Archive Access and Deep Archive Access (opt-in) | 0.0045 and 0.002 | | | | | **no** |

Notes: Intelligent-Tiering monitoring is $0.0025 per 1,000 objects per month, and objects under 128 KB are never monitored or tiered. The docs say Intelligent-Tiering has no minimum duration but the price list still carries an `EarlyDelete-INT` 30-day charge; unresolved. Glacier Flexible and Deep Archive add 40 KB of overhead per object. Early deletion is billed at the class rate, prorated. Lifecycle skips objects under 128 KB by default.

**Mumbai makes Flexible Retrieval a trap.** It is $0.0045 against Glacier IR's $0.005, about $51/month per 100 TB cheaper, in exchange for unreadable data. Deep Archive saves about $307/month per 100 TB over Glacier IR (**I**, from the table).

### 4.2 Choosing a class by expected reads

Let *r* be the share of a partition's bytes actually returned by reads per month (columnar pruning means *r* is usually below the partition size). Net monthly saving per GB against Standard at $0.024, ignoring request fees and minimum-duration charges:

* Standard-IA: 0.0102 − 0.01 *r*, break-even *r* = 1.02
* Glacier IR: 0.019 − 0.03 *r*, break-even *r* = 0.63

So Glacier IR wins for *r* < 0.44, Standard-IA for 0.44 to 1.0, and the data should stay in Standard above that. Glacier IR GET requests cost $0.01 per 1,000 against $0.0004, 25× Standard. Worked example for one TB of 512 MB files (**A**): one full read of Glacier IR costs about $30.74 against a $19.46 monthly saving; with the transition fee, break-even for "archive, read once" is about 48 days, and a full round trip back to Standard breaks even at about 56 days.

**Blast radius.** One careless full scan of 50 TB sitting in Glacier IR costs 50 × 1,024 × $0.03 ≈ $1,536, which erases about 1.6 months of that tier's savings (50 TB saves about $972/month). Engines give no warning (§4.5), so the guard has to come from Cryo (§7.8).

### 4.3 S3 Intelligent-Tiering, and what limits it on Iceberg (R)

* Objects move to Infrequent Access after 30 days without access and to Archive Instant Access after 90. **Any** read (GetObject, and the source of CopyObject or UploadPartCopy) moves an object back to Frequent Access, which is priced like Standard. HEAD, LIST and tagging calls do not count (**A**).
* On Iceberg, Trino scans, compaction rewrites and any other reader of old partitions reset the clock. AWS's own Iceberg guidance says to use Intelligent-Tiering but **not** its optional Archive Access and Deep Archive Access tiers with Iceberg tables [S3].
* Cryo's evidence: over 60 days bronze paid $2,470 in Frequent Access and only $43 in Infrequent Access (CUR, [investigation](./s3-cost-investigation-2026-08.md)). That is consistent with either "not matured yet" (the existing note) or "reads keep resetting it". It can be settled cheaply: the existing free CloudWatch integration (`fetch_bucket_storage_metrics`) reports `IntelligentTieringIAStorage` over time; steady growth means maturation, a flat line means resets.
* S3 Tables adopted Intelligent-Tiering at re:Invent 2025 and made its compaction **tier-aware**: it compacts only Frequent Access files "to ensure that compaction does not promote data … you have not touched over 30 days" (**R**, [S4]). Cryo's compaction should do the same for cold partitions (§7.6).

### 4.4 Ways to change a storage class

| Mechanism | Selects objects by | Partition-level? | Side effects and limits |
|---|---|---|---|
| Lifecycle rule | prefix, tag, size | Only with the default layout and one rule per partition; **not** when `write.object-storage.enabled=true` (hash directories precede the partition); max 1,000 rules per bucket | Transitions only move down; asynchronous (about daily); minimum-duration constraints; objects under 128 KB skipped by default (**A**) |
| Lifecycle by object tag | tag | Needs one `PutObjectTagging` per object | Evaluated daily; removing a tag does not reliably cancel a queued action (**A**) |
| In-place `CopyObject` with a new class | explicit key list | **Yes**, driven by manifest `file_path`s, independent of layout | New object and `LastModified`; creates a new version on versioned buckets (the old Standard version keeps billing); 5 GB single-call limit (multipart copy above); needs KMS, Object Lock and replication checks; billed as a request in the destination class |
| S3 Batch Operations | manifest of keys | yes | Right tool for 100k+ object backfills; needs a job role |
| `RestoreObject` | key | yes | Only for Glacier Flexible, Deep Archive and Intelligent-Tiering archive tiers; 3 to 5 h standard, 5 to 12 h bulk for Flexible; up to 12 h and 48 h for Deep Archive; restored copy lasts the days you set (**A**) |
| Iceberg `s3.write.storage-class` (since 1.4.0) and Trino `s3.storage-class` (since 471) | catalog-wide write setting | No | Applies to metadata files too (**A**) |

The hashed-layout caveat is why a manifest-driven executor beats lifecycle rules: it needs no assumption about key layout. Whether the large tables use `write.object-storage.enabled` is unknown (§9).

### 4.5 What engines do with archived objects

* A GET on an archived object returns `InvalidObjectState` (HTTP 403); `HeadObject` exposes `x-amz-restore` and `x-amz-archive-status`, and `ListObjectsV2` returns `StorageClass` per object (**A**).
* **No Iceberg-aware skip exists.** Trino's `hive.s3-glacier-filter` is in the Hive connector only; Athena's `read_restored_glacier_objects` is for Hive tables only; Spark `S3FileIO` has write-side options only (**A**). The failure text in Trino and Spark on an archived Iceberg file was not observed; a StarRocks-over-Iceberg production incident from Glacier lifecycle rules plus Intelligent-Tiering archive tiers is documented in `mitodl/ol-infrastructure#5108` (**A**).
* Standard-IA and Glacier IR are served by a normal GET at millisecond latency and need no restore (**R**, [S3]).
* Time travel to a snapshot whose files are archived fails until they are restored (**R**, [S3]).
* Maintenance (**I** unless noted): compaction must read data files, so it fails or pays retrieval on archived partitions; `expire_snapshots` and `remove_orphan_files` delete without a restore but incur early-deletion charges before the minimum duration; `remove_orphan_files` lists objects and a copy refreshes `LastModified`, so verify orphan age logic after any in-place copy.

### 4.6 Iceberg-level facts

* The spec has no per-file or per-partition tier or "offline" field, and no open proposal was found; the nearest items are `apache/iceberg#8339` (export cold partitions and reload, closed stale), `#8037` (docs warning about `add_files` and Intelligent-Tiering, merged), `#10372` and `#5557` (**A**).
* Data files can live outside the table location (`file_path` is a full URI; `write.data.path` redirects writes), and `remove_orphan_files` scans only the table location (**A**). Moving files to another prefix needs a metadata rewrite (`rewrite_table_path` is whole-table, with several open correctness issues such as `#17498`) (**A**). A storage-class change on the **same key** changes no Iceberg path and needs no metadata change.
* AWS's documented "archive" pattern is for **expired snapshots**, not cold live partitions: `s3.delete-enabled=false` plus `s3.delete.tags.*` so `expire_snapshots` tags files instead of deleting them, a lifecycle rule moves them to Glacier, and restore plus `register_table` re-attaches (**R**, [S3]).

---

## 5. How competitors and analogous systems do it

### 5.1 Systems that tier or archive

| System | Unit | Trigger | Restore | What queries see | Notable rails or pitfalls |
|---|---|---|---|---|---|
| **Databricks Delta archival** (Public Preview, DBR 13.3+, Delta only) | File | Age since file creation, via a table property that must mirror an S3 lifecycle rule you create yourself | Manual: `SHOW ARCHIVED FILES` lists what to restore | Queries needing archived files **fail early**; Databricks never returns results that would need them | Fail-closed; two sources of truth that drift; Intelligent-Tiering archive tiers unsupported; Glacier IR: "all scanned files are retrieved" (**R**) |
| **Snowflake storage lifecycle policies** | Rows (standard, interactive and dynamic tables; Iceberg not supported) | Row expression, runs daily; archive to COOL or COLD, then expire | `CREATE TABLE … FROM ARCHIVE OF … WHERE …` into a **new** table; `EXPLAIN` estimates files | Archived rows not queryable in place | Billing and early-deletion views; minimum 90 d (COOL) and 180 d (COLD); tier fixed per table (**R** top-level, **A** detail) |
| **S3 Tables + Intelligent-Tiering** | File | 30 d and 90 d without a read | None; all tiers instant, no archive tiers | Transparent | Compaction touches only Frequent Access files; any read promotes (**R**) |
| **S3 Intelligent-Tiering (general)** | Object | Last access | Archive tiers need restore | Archive tiers fail | AWS: do not enable archive tiers with Iceberg (**R**) |
| **GCS Autoclass, Azure lifecycle and smart tier, OneLake, R2 Infrequent Access** | Object or blob | Age or last access | Online tiers none; Azure Archive needs rehydration | Online tiers transparent | Object-level and Iceberg-unaware; Azure rehydration does not reset age so blobs bounce back (**A**) |
| **BigQuery long-term storage** | Table or partition | 90 days unmodified, about 50% discount | None | Transparent | Resets on modification, not on queries (**A**) |
| **Yelp** (in-house) | Partition, per IAM role | Server access logs per table, partition and role; Intelligent-Tiering by default | n/a | Beyond a per-owner "Default Access Retention" window reads fail with Access Denied until a cost-estimated, approval-scaled request is granted | 33% S3 saving from storage-class advice **and** deletion retention; warns full scans of cold tiers are "not obvious to users who are writing SQL" (**R**) |
| **Uber** (HDFS to S3) | Partition | Per-dataset TTL, scheduler → archival workflow | Explicit retrieval workflow, partition-pruned, user-specified range | Archived partitions optionally kept in the table | Metadata ledger; pitfalls: schema drift on restore, backfills overlapping archival, restore spikes (**R**) |
| **Elasticsearch ILM, Druid, Pinot, ClickHouse, Kafka KIP-405, HDFS storage policies, Hive archive** | Index, segment, part, log segment, path, partition | Age phases or explicit policy | Mostly none (data stays queryable; searchable snapshots and remote tiers) | Transparent, slower on cache miss | Desired-versus-observed reconcilers (HDFS Mover), rebalance `dryRun` (Pinot), repository objects must **not** be moved to Glacier (Elasticsearch) (**A**) |

### 5.2 Field evidence

* **Yelp (2026-05).** The 33% came from usage attribution that let the platform team *recommend* cheaper storage classes and push deletion-based retention. The post does not describe how classes were applied, and it names no class beyond Intelligent-Tiering. Our [partition-access plan](./PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md) says the 33% came from "manually re-tiering objects"; that is not what the post says (**R**).
* **Zalando (2020)** reported a 37% yearly saving from Intelligent-Tiering, using access logs plus Inventory to split hot from cold datasets (**A**).
* First-party tiering posts from Netflix, LinkedIn, Airbnb, Pinterest, Slack and Lyft were not found (about 10 queries); absence is not conclusive.

### 5.3 Vendors with no Iceberg-aware tiering

Tabular (now Databricks), Onehouse, Upsolver/Qlik, Dremio, Starburst Galaxy, Cloudera, Apache Amoro, Apache Polaris, Lakekeeper, Confluent Tableflow, AWS Glue optimizers, Databricks Predictive Optimization, Cloudflare R2 Data Catalog and BigLake managed Iceberg all stop at compaction, snapshot expiry and orphan cleanup (**A**, based on documentation greps and issue searches rather than exhaustive reads). Hudi's "archival" is timeline archival, not data tiering.

* **Ryft.** The roadmap's "Ryft does storage tiering" can be confirmed only as marketing: a partner blog lists "data tiering and data retention" under Ryft's lifecycle features. No independent source describes the unit, trigger, restore model or query behaviour. Ryft was acquired by Cyera on 2026-04-23 and its Terms of Service reportedly forbid automated access, so **a human must read Ryft's own documentation** if those details matter. Do not scrape it.
* **LakeOps** (lakeops.dev) routes queries across engines; no tiering was found. The name is shared by Starburst's "Icehouse LakeOps" and an unrelated lakeops.io.

### 5.4 Patterns to copy

1. **Whole-partition policy on an explicit age, plus a stability check** for late-arriving rows (Uber, BigQuery, Dremio-author blog).
2. **Desired state versus observed state with an idempotent reconciler**; S3 is the source of truth for class (HDFS Mover, Hudi TTL service).
3. **Fail closed, early, with a "what would I need to restore" report** (Databricks).
4. **Never touch metadata; change class on the same key** (Databricks warns an archived `_delta_log/` bricks a table).
5. **Restore is explicit, scoped and shows its cost first** (Snowflake `WHERE` plus `EXPLAIN`, Uber partition ranges, Yelp's cost-scaled approval).
6. **Surface billing exposure as bytes**: early-deletion exposure and double-storage windows (Snowflake).
7. **Dry run, per-run caps, exclusion lists** (Cloudera, Pinot, Hudi, Starburst).
8. **Maintenance awareness**: compaction skips cold data (S3 Tables).
9. **Two named tiers**: an instant "cool" tier and a restore-required "archive" tier.
10. **Projected versus realized savings**, which no source shows together with early-deletion and restore exposure; a place for Cryo to differentiate.

### 5.5 Patterns to avoid

Access-time auto-archive tiers on live Iceberg tables; prefix lifecycle rules as the partition selector under hashed layouts; silent skipping of archived data (Trino's Hive-only `READ_NON_GLACIER` can return partial results); two sources of truth that drift; irreversible tier binding; archiving small files; letting maintenance or replication reads promote cold data; time selectors that misread backfills (Pinot's default `endTime`); undefined write semantics on archived data; savings claims with no accounting.

---

## 6. Options evaluated

| # | Option | Safety | Cost-benefit | Complexity | Reversibility | Verdict |
|---|---|---|---|---|---|---|
| A | S3 Intelligent-Tiering at bucket level (archive tiers **off**) plus advice | Very high | Medium: only objects untouched for 30 or 90 days; any read resets | Very low | High | **Do now**, with Cryo changes that stop resetting it (compaction skips cold partitions) |
| B | **Cryo-managed in-place class change, instant-access classes only** (Intelligent-Tiering, Standard-IA, Glacier IR) via manifest-driven `CopyObject` | High: engines see a normal GET | High on provably cold partitions; takes effect on day 0 | Medium | High: copy back, paying retrieval and early-delete | **Recommended core** |
| C | Lifecycle rules by tag or prefix | High for IA and IR | Same as B | Medium; per-object tagging, asynchronous, hard to cancel; layout-dependent | Low to medium | Not for live partitions; fine for expired-snapshot files |
| D | Separate cold prefix or bucket plus Iceberg metadata rewrite | Medium: commit conflicts, immature tooling | Same as B, plus IAM gating | High | Medium | Not now |
| E | Detach and restore (offline classes) | Only design that makes Flexible or Deep Archive safe by construction | Highest per GB, small marginal gain in Mumbai (§4.1) | Very high | Low to medium: hours to days, spec drift | **Defer (P4)** |

What must be true before any offline class is safe on a live Iceberg table (**I**): no current or time-travel-reachable snapshot, engine or maintenance job may read an archived file (so files must be detached from the table, since predicates cannot guarantee it); compaction, `rewrite_data_files`, `add_files` and orphan removal must skip them; manifests, manifest lists and delete files stay in instant classes; hold time exceeds the minimum duration; a restore runbook exists; and read-side protection fails fast (Yelp's IAM gate) rather than slowly with `InvalidObjectState`.

---

## 7. Recommended design (options A and B, advisory first)

### 7.1 Product model

* **Tiers shown to users:** *Hot* (Standard), *Auto* (Intelligent-Tiering, archive tiers off), *Cool* (Standard-IA), *Archive-instant* (Glacier IR). *Offline* is reserved for P4. **Archive** demotes a partition, **Restore** promotes it back to Hot, **Re-archive** is the policy demoting it again after a cool-off.
* **Eligibility is an AND of gates**, each with a reason code the UI can show:
  1. *Age* by partition value (available today for 82% of bytes); non-temporal tables use last write.
  2. *Quiet*: no write for W days (needs scanner `last_write_at`); guards against backfills and late-arriving rows.
  3. *Compacted*: no small-file backlog and average file ≥ 8 MB (compact first, then tier; archiving small files wastes request fees and misses the 128 KB minimum).
  4. *Read evidence*: trustworthy logs for the window (ingestion healthy, window ≥ 2× the observed log delay and ≥ 35 days). Zero reads → Glacier IR or Cool by the §4.2 rule. **No evidence → Auto, never Glacier IR.** Recent reads → stay Hot.
  5. *Not excluded* (table, schema, tag, or "pin hot").
* **Unknown is not cold** (§2.2). A missing age or last-write signal blocks the partition ("blocked: no signal"). Missing read evidence caps it at Auto, which has no retrieval fee and corrects itself, and never allows Cool or Archive-instant.
* **Policy** extends [`tier_policies`](../cryo/db/models.py): scope inherited source → schema → table (as Polaris and Databricks do), `basis`, `min_age_days`, `min_quiet_days`, `min_idle_days`, `target_class` restricted to the readable enum, `mode` (`advisory` | `approval` | `auto`), exclusions, `max_bytes_per_run`, `hold_hot_days`. The form drops `GLACIER` and `DEEP_ARCHIVE` and gains `GLACIER_IR`.

### 7.2 The Archive section

**Placement.** Put it in the **Usage tab directly under "Partition access"**, because the evidence and the action belong together, and turn the Cost tab's `#cost-tiering` card into a read-only summary (bytes by tier, projected versus realized savings) linking to it. This avoids an eighth tab. An *Add to run* card in the existing operation cart ([`OperationLauncher`](../web/src/components/table-detail/operations/OperationLauncher.tsx)) makes it follow the canonical order. Ship it behind a feature flag; reuse `ChartCard`, the shared `DataTable`, and the confirmation-dialog pattern from [`operationCatalog.ts`](../web/src/components/table-detail/operations/operationCatalog.ts). Bulk selection sends a **filter spec**, never a list of page IDs; API types are generated from OpenAPI.

```
┌ Archive ────────────────────────────────────────────────────────────────────────┐
│ Policy: cdp-default (from source) · mode: advisory            [Edit] [Preview plan]│
│ Bytes by tier, observed from S3 2 h ago (1,204 of 1,310 partitions)               │
│ ██████████████████░░░░░░░░▒▒▒▒▒▒   Hot 14.2 TB · Auto 3.1 TB · Archive-instant 6.8 TB│
│ Projected saving $412/mo · Realized, last 30 d $188/mo · Retrieval fees $4         │
│                                                                                   │
│ Partition              Size    Age    Last write  Last read       Now   Suggest    │
│ event_date=2025-06-03  8.1 GB  485 d  2025-06-04  none in 35 d ✓  Hot   Archive    │
│ event_date=2025-09-17  8.4 GB  379 d  2025-09-18  12 d ago        Hot   Keep hot   │
│ event_date=2026-08-30  8.0 GB   32 d  2026-08-31  no log data     Hot   Blocked: too young │
│ …                                                          [Archive 38 selected…]  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

The confirmation dialog states: objects and bytes moved; the class; request cost; the minimum-duration commitment and early-delete exposure; the retrieval fee and break-even reads per month for this partition (§4.2); that compaction will skip these partitions; that it is reversible and what a restore costs. Restore and exclusion use the same pattern.

### 7.3 API (additive)

* `GET /api/v1/tables/{id}/tiering`: summary, bytes by tier (observed), resolved policy, projected and realized savings.
* `GET /api/v1/tables/{id}/tiering/partitions`: paged; per partition tier, eligibility, reason codes, break-even reads, net saving.
* `POST /api/v1/tables/{id}/tiering/plan`: dry run returning object and byte counts, request cost, guardrail results (versioning, Object Lock, KMS, replication).
* `POST /api/v1/tables/{id}/tiering/apply` and `/restore`: create a durable intent (operator role; approval above a dollar threshold).
* Existing `/tier-policies` extended; `/s3-tiering-recommendations` and `/tables/{id}/s3-tiering` fixed (P0), then redirected to the new summary.

### 7.4 Data model (Alembic, additive; schema `cryo`)

* Scanner: add `last_write_at` to each partition's `raw` in both scanners (max snapshot timestamp of its manifest entries).
* `partition_last_access` (table_id, partition_path, last_read_at, reads_35d, bytes_sent_35d, source, observed_at): a durable rollup fed by the log ingest, so "no read in 90 days" survives the 35-day prune. Keep bytes sent (an S3 log field) to price retrieval.
* `partition_tier_state` (table_id, partition_key, spec_id, class_mix JSONB, objects, bytes, observed_at, desired_class, state, intent_id, last_error): **observed** state, refreshed from S3 (`ListObjectsV2` returns `StorageClass`; about 168 list calls for a 168k-file table, under a cent), never trusted over S3.
* Reuse `maintenance_intents`, `effect_receipts` and `job_steps` for every transition; add `max_objects_transitioned` and `max_bytes_transitioned` to the intent budget.

### 7.5 Execution

* One operation, `tier_partitions`, with a target class (promotion is tiering back to Standard). It is an object-store effect, not SQL, so the planner stays engine-agnostic; it runs on a new **Destination** of kind `object_store` (write credentials are a Destination concern, not a Source one, per [`AGENTS.md`](../AGENTS.md)). Add it **last** in `CORRECT_ORDER` ([`guardrails.py`](../cryo/planner/guardrails.py)) and mirror it in `web/src/lib/operationOrder.ts`, with a parity test.
* **Manifest-driven and layout-independent:** at job time (off the request path) read the exact `file_path`s of the partition's live data files from the current snapshot; keep only objects ≥ 128 KB; compare against observed class; `CopyObject` in place for the rest (`StorageClass=target`, metadata and tagging copied) with bounded concurrency; verify with `HeadObject`; write the receipt. Data files only: manifests, metadata JSON and delete files stay in Standard. Files only older snapshots reference are left alone (expire them instead).
* **Idempotent and resumable.** The effect strategy is `stable_reference_probe` ([`models.py`](../cryo/capabilities/models.py)): after an ambiguous outcome Cryo probes the object's class instead of replaying the copy.
* **Safety kernel:** per-table lock and fence so it never overlaps compaction, expiry or orphan removal; quiet-window probe ([`safety_gates.py`](../cryo/worker/safety_gates.py)); off-peak windows (IST); request-rate cap per prefix (S3 allows about 3,500 PUT/COPY per second per prefix).
* **IAM (least privilege, dedicated IRSA service account, never the shared worker role):** `s3:ListBucket`, `s3:GetObject` (HEAD and copy source), `s3:PutObject` **with a `s3:x-amz-storage-class` condition limited to** `STANDARD`, `INTELLIGENT_TIERING`, `STANDARD_IA`, `GLACIER_IR` (the executor always sends the storage-class header, since a request without it would not satisfy the condition), plus the read-only pre-flight checks `s3:GetBucketVersioning`, `s3:GetLifecycleConfiguration`, `s3:GetBucketObjectLockConfiguration`, `s3:GetReplicationConfiguration`, `s3:GetBucketNotification` and `s3:GetIntelligentTieringConfiguration` (which also covers listing), and KMS permissions where needed. No delete, lifecycle-write or policy-write actions. The condition key exists for `s3:PutObject` (**R**, IAM definition dataset derived from the Service Authorization Reference); confirm it constrains `CopyObject` with one test copy before relying on it. This makes the unreadable classes impossible even if a bug asks for them. P4 would add `s3:RestoreObject`.
* Throughput: a server-side copy moves no data through Cryo and has no transfer charge in-region; request cost for the whole ≥ 90-day pool (about 1.4 M objects at 93 MB) is about $28 for Glacier IR (**I**).

### 7.6 Interaction with existing maintenance

| Operation or component | Change |
|---|---|
| `optimize` (compaction) | Skip partitions not in Hot or Auto; compact **before** archiving (a rewrite of an archived partition pays retrieval, writes new Standard files that undo the tiering, and early-deletes the old ones) |
| `expire_snapshots`, `remove_orphan_files` | No change to behaviour; account early-deletion exposure; orphan age logic verified after in-place copies (`LastModified` resets) |
| Planner | Pass `tier` with each partition row (beside `last_write_at`); fix `_partition_is_cold` to fail closed for tiering use |
| Scanner checklist `storage_class` | Classify readable versus restore-required (Standard-IA, Glacier IR, One Zone-IA readable; Glacier, Deep Archive, and Intelligent-Tiering objects with `ArchiveStatus` not) and fix the sampling so it runs |
| Cost model | Per-class, per-region prices from the Price List API (`fetch_s3_price_per_gb` exists) instead of us-east-1 constants; add retrieval, request and early-delete terms |
| Intelligent-Tiering config | Read-only check that no bucket enables archive tiers; raise a health finding if it does |

### 7.7 Restore and re-archive

* **Restore (instant tiers):** `CopyObject` back to Standard, billed as retrieval fee plus request, with early-delete exposure if inside the minimum duration; the dialog shows the number first (Snowflake `EXPLAIN`, Yelp cost estimate). Scope by partition range, like Uber.
* **Auto-promote guard (P3):** from `partition_last_access.bytes_sent`, estimate retrieval fees per archived partition; if 30-day retrieval cost exceeds half of its saving, restore it and set `hold_hot_days` (default 30) so it does not ping-pong. Attribute the cost to the querying client with the existing cost-attribution tables.
* **Re-archive** is simply the policy re-evaluating after `hold_hot_days`.

### 7.8 Guardrails

Dry-run default; `advisory` mode until an admin promotes a policy; approval above a dollar threshold (Yelp scales approvals by cost); per-run and per-day byte and object caps; exclusion lists and a per-table "pin hot"; pre-flight refusal when the bucket has versioning without a non-current-version expiry rule, Object Lock, or replication/event notifications not yet reviewed; refusal on KMS without permissions; a global kill switch; every action audited (table, action, status, detail, duration); no secrets in logs. For read protection beyond advice, evaluate Yelp's IAM gate only for the Offline tier, since Cool and Archive-instant never fail a read.

### 7.9 Savings accounting

Show **projected versus realized** side by side: bytes by class per table from `partition_tier_state`; bucket-level truth from CloudWatch `BucketSizeBytes` by `StorageType` (already integrated) and CUR usage types (`TimedStorage-SIA`, `TimedStorage-GIR`, `Retrieval-SIA`, `Retrieval-GIR`, `EarlyDelete-*`); S3 Inventory (`StorageClass`, `IntelligentTieringAccessTier`, about $0.0028 per million objects listed in Mumbai) where a per-object join is worth it. Prediction quality (share of archived partitions read within 30, 60, 90 days) is a first-class metric.

### 7.10 Tests

Golden tests for policy evaluation including every blocked reason; a parity test for `CORRECT_ORDER` and its UI mirror; executor tests against a mocked S3 that supports storage classes (moto) covering resume, partial failure, `NoSuchKey` after a concurrent expire, unknown-outcome probing and the class allow-list; guardrail tests for versioning and Object Lock; a canary against real S3 in a sandbox bucket (MinIO does not model these classes, so local dev cannot exercise it).

---

## 8. Phased plan

Rough sizing for one engineer who knows the codebase.

| Phase | Deliverable | Exit criteria | Size |
|---|---|---|---|
| **P0** Truth and signals | (1) Fix the recommender: fall back to `TableQueryActivity` and return `unknown` rather than 90 days; Mumbai prices; drop the placeholder. (2) Add `last_write_at` to both scanners (also revives the planner guard). (3) Repair S3 log ingest for bronze, silver, gold, temp (flat versus partitioned prefix) and add the durable `partition_last_access` rollup; bound the `partition-access` query in SQL. (4) Make the storage-class checklist class-aware and actually sampled; alert on Glacier-class objects under live tables. (5) Show bytes by storage class per bucket (CloudWatch) on the source page. | Cost-tab card and KPI stop claiming savings for queried tables; partition access non-empty for the top tables; one week of fresh logs | Partial. (1), (2), the SQL bound in (3), and the class rules in (4) are in the working tree. Log ingest, the 90-day rollup, sampling in production, the Glacier alert, and the source-page class chart are not. |
| **P1** Advisory Archive section | Usage-tab section, policy editor limited to readable classes, dry-run plan, per-partition reason codes, projected savings. No S3 writes. | Operators review candidates for 2 to 4 weeks; measured "false cold" rate (archived-would-have-been-read within 30 days) under 5% | Advisory code is in the working tree. The operator review has not started. Feature flag and OpenAPI client types are not. |
| **P2** Execute instant tiers | Executor, intents and receipts, restore, planner and checklist integration, dedicated IAM role with class allow-list, realized-savings view; canary on 3 to 5 tables, then policy rollout | Zero failed reads; realized within 20% of projected; restore drill passed | Not started. |
| **P3** Closed loop | Retrieval-cost guard and auto-promote, Slack report line, approvals by dollar threshold | Retrieval fees under 10% of savings | Not started. |
| **P4** Offline tiers (deferred) | Detach and restore ledger for Deep Archive, only for a concrete need (for example more than 100 TB of provably dead but must-retain data) | Separate design review | Still deferred. |

Cryo gains most from P0 alone: it removes a misleading dollar claim and fixes two signals the planner already depends on.

---

## 9. Things nobody could verify from this session

This session had no valid AWS credentials. Someone with access should check:

1. **Bucket features** on the seven lake buckets: versioning, Object Lock, replication, event notifications, SSE-KMS. Each changes the side effects of in-place copy (§4.4).
2. **Existing lifecycle rules**, especially what moves to Deep Archive (about 12 TB across gold and bronze per §2.4) and whether any Iceberg data files are affected (the 2026-09-11 incident).
3. **Intelligent-Tiering configurations**: are archive tiers enabled on any bucket (`aws s3api list-bucket-intelligent-tiering-configurations`)?
4. **Key layout:** does `write.object-storage.enabled` apply to the large tables? One `aws s3 ls` of a table's data prefix answers it. This also settles whether lifecycle-by-prefix was ever viable.
5. **Log delivery format** for the central logging bucket (`SimplePrefix` versus `PartitionedPrefix`).
6. **Lab tests:** the exact Trino and Spark error text and timing on an archived Iceberg data file; whether a lifecycle transition changes `LastModified`; whether the `s3:x-amz-storage-class` condition constrains `CopyObject`; whether `PutObjectTagging` bills as a PUT-tier request.
7. **Bronze Intelligent-Tiering trend:** maturation or resets (§4.3).
8. **Standard-class marginal rate:** the 50 to 500 TB tier assumes the whole 316 TB is in one billing account.

---

## 10. Method and caveats

* **Live evidence** (**L**) was gathered on 2026-10-01 through the documented read-only path: a port-forward to `cryo-api` and a short-lived `viewer`-role dev token, `GET` requests only (a few hundred), against the production `cdp` source. The token was deleted and the tunnel closed afterwards. One `cryo-api` replica was OOMKilled during the probe (§2.4); it had restarted earlier that morning and its sibling holds about 3.8 GiB, so API memory headroom deserves a separate look, and `get_partition_access` should aggregate in SQL.
* **Reproducing the numbers:** `GET /tables?limit=500&offset=N&sort=-total_size_gb` (stats carry `total_size_gb`, `partition_col`, `partition_col_type`, `query_count_30d`); `GET /tables/{id}/partitions?limit=1000&offset=N` (partition value, transform, `partition_size_gb`); `GET /tables/{id}/s3-tiering` and `/tier-candidates`; `GET /tables/{id}/partition-access?days=35`; `GET /sources/{id}/s3-log-configs`. Use a viewer token, pace requests a few seconds apart, watch `kubectl top pod -n cryo` between batches, and avoid `/partition-access` on tables with tens of thousands of partitions until that query is bounded.
* **Sizes** are the scanner's live data-file bytes in GiB, matching S3's GB-month billing unit; the partition list is capped at 5,000 partitions per table (most small files first), which did not materially truncate the 150 tables analysed (parsed partition bytes equalled table bytes to 0.01% overall; one 330 GB table was at 93.5%).
* **Not measured:** actual current storage class per object, actual reads by partition (§2.3), query-side retrieval exposure, and anything requiring AWS credentials (§9).
* **Research method:** two read-only research sub-agents (S3 and Iceberg mechanics; competitors and analogous systems) plus my own re-reads of the primary sources marked **R**. Ryft's own documentation was deliberately not used (§5.3).
* **Corrections to existing docs:** the Yelp 33% attribution (§5.2) and the shipped recommender's claims (§2.2). Pointers were added to [`todo.md`](../todo.md), [`EXECUTION_PLAN.md`](./EXECUTION_PLAN.md) and the [partition-access plan](./PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md).

---

## 11. Sources

**AWS and S3** (**R** unless noted)
* [S1] Price List, S3 ap-south-1: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-south-1/index.json ; Deep Archive offer (**A**): https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3GlacierDeepArchive/current/ap-south-1/index.json
* [S2] Intelligent-Tiering overview: https://docs.aws.amazon.com/AmazonS3/latest/userguide/intelligent-tiering-overview.html
* [S3] Prescriptive Guidance, Iceberg on AWS, optimizing storage: https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/best-practices-storage.html
* [S4] S3 Tables with Intelligent-Tiering: https://aws.amazon.com/blogs/storage/optimize-data-management-on-s3-tables-with-intelligent-tiering/
* [S5] (**A**) Storage classes: https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html ; restore options: https://docs.aws.amazon.com/AmazonS3/latest/userguide/restoring-objects-retrieval-options.html ; lifecycle: https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html and https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html ; GetObject: https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html ; Athena: https://docs.aws.amazon.com/athena/latest/ug/querying-glacier.html
* [S6] IAM definition dataset (condition keys and actions, derived from the Service Authorization Reference): https://github.com/iann0036/iam-dataset

**Iceberg, Trino, Spark** (**A**)
* Iceberg `S3FileIOProperties` and PR #8154: https://github.com/apache/iceberg/blob/main/aws/src/main/java/org/apache/iceberg/aws/s3/S3FileIOProperties.java ; spec https://iceberg.apache.org/spec/ ; Spark procedures https://iceberg.apache.org/docs/latest/spark-procedures/ ; issues https://github.com/apache/iceberg/issues/8339 , /8037 , /10372 , /5557
* Trino Hive Glacier filter and S3 `s3.storage-class`: https://trino.io/docs/current/connector/hive.html , https://trino.io/docs/current/object-storage/file-system-s3.html
* StarRocks incident: https://github.com/mitodl/ol-infrastructure/pull/5108

**Field evidence** (**R**)
* Yelp, partition access visualizations (2026-05): https://engineeringblog.yelp.com/2026/05/partition-access-visualizations.html
* Yelp, S3 server access logs at scale (2025-09): https://engineeringblog.yelp.com/2025/09/s3-server-access-logs-at-scale.html
* Uber, From Archival to Access (2025-06): https://www.uber.com/us/en/blog/from-archival-to-access/

**Vendors** (**R** where marked)
* Databricks archival (**R**): https://docs.databricks.com/aws/en/optimizations/archive-delta ; Predictive Optimization: https://docs.databricks.com/aws/en/optimizations/predictive-optimization
* Snowflake storage lifecycle policies (**R**): https://docs.snowflake.com/en/user-guide/storage-management/storage-lifecycle-policies ; billing and retrieval pages under the same path (**A**)
* Ryft, third-party only: https://www.cyera.com/press-releases/cyera-acquires-ryft-to-extend-its-agentic-ai-security-platform , https://streamnative.io/blog/streamnative-ryft-real-time-iceberg-ingestion-intelligent-lakehouse-management
* LakeOps: https://lakeops.dev/docs/query-routing
* Others (**A**): Onehouse https://docs.onehouse.ai/product/manage-data/table-services/ · Upsolver/Qlik https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/DataIntegration/Lakehouse/iceberg-optimization.htm · Dremio https://docs.dremio.com/current/developer/data-formats/apache-iceberg/table-maintenance-optimization/automated-maintenance/ · Starburst https://docs.starburst.io/starburst-galaxy/data-engineering/optimization-performance-and-quality/observability/icehouse-lakeops.html · Cloudera https://docs.cloudera.com/cdp-private-cloud-base/7.3.2/iceberg-how-to/topics/clo-supported-features.html · Amoro https://github.com/apache/amoro/blob/HEAD/docs/user-guides/configurations.md · Polaris https://github.com/apache/polaris/blob/main/site/content/in-dev/unreleased/policy.md · Lakekeeper https://github.com/lakekeeper/lakekeeper/blob/HEAD/docs/docs/table-maintenance.md · Tableflow https://docs.confluent.io/cloud/current/topics/tableflow/operate/configure-tableflow.md · R2 https://developers.cloudflare.com/r2/buckets/storage-classes/ · GCS Autoclass https://cloud.google.com/storage/docs/autoclass · BigQuery pricing https://cloud.google.com/bigquery/pricing · Azure lifecycle https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-overview and smart tier https://learn.microsoft.com/en-us/azure/storage/blobs/access-tiers-smart · OneLake https://learn.microsoft.com/en-us/fabric/onelake/onelake-storage-tiers

**Analogous systems** (**A**): Druid https://druid.apache.org/docs/latest/operations/rule-configuration/ · Pinot https://docs.pinot.apache.org/operate-pinot/segment-management/separating-data-storage-by-age/moving-segments-across-tenants · ClickHouse https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree · Elasticsearch https://www.elastic.co/docs/manage-data/lifecycle/data-tiers · Kafka KIP-405 https://cwiki.apache.org/confluence/display/KAFKA/KIP-405%3A+Kafka+Tiered+Storage · HDFS https://hadoop.apache.org/docs/stable/hadoop-project-dist/hadoop-hdfs/ArchivalStorage.html · Hive archiving https://cwiki.apache.org/confluence/display/Hive/LanguageManual+Archiving · Hudi https://hudi.apache.org/docs/configurations/ and https://github.com/apache/hudi/issues/18135

**In-repo:** [`S3_STORAGE_COST_GAP_ANALYSIS_2026-08.md`](./S3_STORAGE_COST_GAP_ANALYSIS_2026-08.md), [`s3-cost-investigation-2026-08.md`](./s3-cost-investigation-2026-08.md), [`s3-request-cost-tracking.md`](./s3-request-cost-tracking.md), [`PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md`](./PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md), [`TABLE_DETAIL_REDESIGN_V2_2026-09-30.md`](./TABLE_DETAIL_REDESIGN_V2_2026-09-30.md), [`WORKER_STEP_LEDGER.md`](./WORKER_STEP_LEDGER.md), [`TABLE_DEDUPLICATION_DESIGN.md`](./TABLE_DEDUPLICATION_DESIGN.md).
