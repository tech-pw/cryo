# Research & Roadmap

> Last reconciled: 2026-07-05. Sources (merged into this doc):
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (what to build next),
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (performance + OSS strategy),
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (competitors + research papers),
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (FORMATS '26 triage),
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (UI/UX spec),
> [`research-and-roadmap.md`](https://github.com/shelf-project/Cryo/blob/main/docs/research-and-roadmap.md) (cost-savings UI spec).
> The architecture decisions are in [`architecture.md`](./architecture.md); the executor /
> engine-layer plan is in [`execution-plan.md`](./execution-plan.md).

---

# Part 1 — What to build next (RECOMMENDATIONS)

> Rewritten 2026-07-03. Grounded in three inputs: (1) the actual code in `cryo/` and the phase
> status in [`todo.md`](../todo.md) / [`docs/current-state.md`](./current-state.md), (2)
> independently re-verified competitor research (see the caveats at the bottom — several numbers
> in the previous draft of this doc were vendor marketing or unsupported by their own citations),
> and (3) real Iceberg ecosystem facts checked against primary sources. Where a "competitor
> feature" turned out to already exist in Cryo's scanner, that's called out explicitly instead of
> re-recommending it.

## How to read this

Every recommendation below is tagged:

- **✅ Already done** — exists in the codebase today; no action needed, just noting it so it
  doesn't get re-invented.
- **🟡 Partial** — the hard/data part exists, only wiring or UI remains. Highest ROI per hour of
  work.
- **🔴 Real gap** — genuinely not built, worth prioritizing.
- **⏳ Later** — real and valuable, but blocked on something else landing first.

## Part 0 — Finish the plumbing before optimizing anything

This is the single most important recommendation in this document. Per `todo.md`, Cryo's core
execution path is **not yet end-to-end**:

| Gap | File(s) | Why it matters |
|-----|---------|----------------|
| Trino execution engine incomplete | `cryo/engines/trino/engine.py` (missing), `client.py` (missing), `load.py` (missing) | SQL is rendered (`engines/trino/sql.py` ✅) but never actually executed against a coordinator. |
| Worker/Governor/Scheduler loops partial | `cryo/worker/worker.py`, `cryo/governor/governor.py`, `cryo/scheduler/scheduler.py` (all `[~]`) | Admission math exists (`governor/admission.py` ✅) but nothing runs it continuously in production. |
| Spark/EMR engine barely started | `cryo/engines/spark/emr.py`, `engine.py` (missing) | Large-table routing (>100GB → Spark) can't happen yet — only the SQL renderer exists. |
| No CI | — | Every other recommendation regresses silently without it. |

**Nothing about Rust, DataFusion, or JVM-vs-native performance matters until a preset can run
against a real Trino cluster and a real Spark/EMR job end-to-end, with an audit row to show for
it.** A perfectly-optimized engine with no working orchestration around it ships nothing. Treat
closing P2/P3/P6 as the prerequisite for every performance idea in Part 1 and most feature ideas
in Part 2.

## Part 1 — Performance, realistically sequenced

### 1.1 Do first: make the existing Python/Trino/Spark path work end-to-end
Covered in Part 0. This is a correctness/completeness task, not a performance task — but it is the
biggest performance lever available right now, because right now throughput is zero.

### 1.2 Do second: fix the scanner's actual bottleneck (not the JVM) ✅

**Implemented 2026-07-03.**

`cryo/scanner/analyze.py::iter_data_files` now parallelizes manifest fetches using a thread pool:

- **Parallel manifest reads:** `iter_data_files()` accepts `parallel=True` (default) and
  `max_workers=8` (configurable via `CRYO_SCAN_PARALLEL_MANIFESTS`). Each manifest's entries
  are fetched concurrently, overlapping I/O-bound S3/catalog calls.
- **Parallel table scanning:** `run_scan()` accepts `parallel_tables` (configurable via
  `CRYO_SCAN_PARALLEL_TABLES`, default 0=serial). When >0, tables within a chunk are scanned
  concurrently using separate DB sessions.
- **Session reuse:** Catalog instances are already created once per source in `_discover_candidates`
  and reused across all tables from that source.
- **File size passthrough:** PyIceberg already provides `file_size_in_bytes` in manifest metadata;
  the scanner uses this directly without additional S3 calls.

CLI: `cryo scan --parallel-tables=4` enables parallel table scanning.

### 1.3 Do third, and only if profiling justifies it: a scoped Rust component
See Part 4 (PERFORMANCE_AND_OPENNESS) for the deep dive. The short, corrected version:

- Rust/DataFusion compaction engines (LakeOps) claim "95% faster than Spark" on their own
  marketing site, with ~80% lower cost — this is **vendor-self-reported**, not independently
  verified, and their own blog benchmark works out to **86% faster on one 5.5TB test**, not a
  universal constant.
- Generic "Rust vs. Python" ETL benchmarks range **2.65x–15.14x depending on the task shape** —
  there's no single reliable multiplier to plan around.
- **Recommendation:** don't commit to a Rust rewrite speculatively. Once P2/P3/P6 are done and 1.2
  is done, benchmark Cryo's *actual* compaction workload on Trino/Spark. If — and only if —
  profiling shows the execution engine itself (not orchestration, not scanning, not S3 latency) is
  the bottleneck, prototype a narrow Rust/DataFusion component for that specific operation
  (compaction is the most defensible candidate — it's the CPU/IO-heavy path). Keep it isolated
  behind the existing `MaintenanceEngine` interface (`cryo/engines/base.py`) exactly as the
  original hybrid-architecture proposal suggested — that part of the design was sound, it was just
  sequenced too early relative to where the codebase actually is.
- **Update 2026-07-07:** the `cryo-engine` Rust crate was removed from the repo. The native engine
  scaffold is preserved in [`architecture.md §1.5`](./architecture.md) but is not in active
  development.

### 1.4 Governor tuning
`governor/admission.py` already implements caps, cooldown, off-peak windows, adaptive budget, and
deadlines — this is more sophisticated than most competitors' documented approach. Once workers
execute real jobs (Part 0), load-test admission control against actual Trino/EMR cluster behavior
and tune the constants from real data rather than assumptions.

## Part 2 — Features: what's already there vs. real gaps

The single biggest finding from reading the actual scanner code: **several "P0 competitor
features" from the previous draft of this document are already implemented.** Re-prioritized
below by actual status.

| Feature | Status | Detail |
|---|---|---|
| Delete file ratio & count health thresholds | ✅ Already done | `scanner/health.py` has `delete_file_pct_warn/crit` and `delete_file_count_warn/crit` deductions today. |
| Maintenance operation sequencing (expire → orphan → compact → manifests) | ✅ Already done | `planner/planner.py::build_plan` already enforces this exact order. The earlier competitor-research draft flagged this as a gap; it isn't one. |
| Partition skew / cardinality warnings | ✅ Already done | `partition_skew_ratio_warn/crit` and `avg_files_per_partition_*` thresholds exist in `health.py`. |
| Manifest fragmentation alerting | ✅ Already done (scoring) | `manifest_count_warn/crit` thresholds exist. Gap: no standalone "just run `rewrite_manifests`" quick action separate from full optimize — cheap to add since `OPTIMIZE_MANIFESTS` is already a defined operation type. |
| Cost dashboard (current vs. projected, live/dead/metadata/orphan breakdown) | 🟡 Partial — **finish this first** | Tier 1 (`account_metadata_cost`) and Tier 2 orphan scan are fully built and persisted to `table_cost`/`table_cost_history`. Only `/tables/{id}/cost`, `/dashboards/cost`, and the UI (spec'd in [`docs/research-and-roadmap.md`](./research-and-roadmap.md#part-5-cost--savings-ui-specification)) remain. All the hard data-modeling work is done — this is the highest ROI-per-hour item in this entire document. |
| Metadata-to-data byte ratio as a health signal | 🟡 Partial | The byte-level live/dead/metadata split is already computed for cost accounting. Surfacing `metadata_bytes / data_bytes` as an explicit health metric (competitors like LakeOps/Ryft treat this as primary) is nearly free — it's a read of data already collected, not new instrumentation. |
| Zero-scan data quality signals (null rates, value bounds, distribution drift) | ✅ Implemented 2026-07-05 | `cryo/scanner/quality.py::ColumnQualityAccumulator` aggregates per-column `null_value_counts`/`value_counts`/`lower_bounds`/`upper_bounds` during the existing manifest walk (`analyze_table`), zero extra data scans; bounds decoded via `pyiceberg.conversions.from_bytes` with the schema's real field type (verified via round-trip, not assumed). Persisted to `table_stats.raw["column_quality"]` and `table_stats_history`; feeds `detect_anomalies` as a new `avg_null_rate` metric. Also fixed a real bug this surfaced: `cryo/quality/profiler.py` (an existing, more sophisticated profiler with baseline/spike/schema-drift detection, exposed at `GET /data-quality/{table_id}`) was reading from `raw["manifest_stats"]`, a key nothing in the scanner ever wrote — it was dead code passing its own tests only because the tests hand-seeded the phantom key. Rewired it to consume the real `column_quality` data instead. NDV via Puffin sketches remains a stub (`_puffin_ndv_counts` returns `{}` safely — Puffin stats aren't a per-`DataFile` attribute, they need a separate table `statistics-files` read, a real follow-up feasibility check, not implemented yet). Source paper: FORMATS '26, [arXiv:2605.30308](https://arxiv.org/pdf/2605.30308v1) — first-edition workshop, no benchmarks/OSS shown, but the technique itself is now implemented and tested independent of the paper's own validation status. |
| AWS S3 free-tier cost/usage intelligence | ✅ Implemented 2026-07-05 | `cryo/integrations/aws_s3_metrics.py` — per-source opt-in integration (`AWSIntegration` model, `POST/PATCH/DELETE /sources/{id}/aws-integration`, `POST .../aws-integration/test`, `GET /sources/{id}/aws-usage`). Connection check via `sts:get_caller_identity`. Bucket size/object-count via CloudWatch's daily auto-published `BucketSizeBytes`/`NumberOfObjects` storage metrics, read with `get_metric_statistics` — deliberately **not** `get_metric_data`, which AWS bills unconditionally even within the free tier. Real S3 pricing via the free `pricing:GetProducts` API (queried against `us-east-1` regardless of bucket region, per AWS's own API constraint). No Cost Explorer, no CUR, no paid CloudWatch metrics-configuration — everything used falls under AWS's always-free quota, per explicit user requirement ("I want free integration, I don't want to pay anything to AWS"). UI: `web/src/components/AwsIntegrationPanel.tsx`, embedded in `Connections.tsx`. Tested: `tests/test_aws_s3_metrics.py` (includes an explicit assertion that `get_metric_data` is never called), `tests/test_aws_integration_api.py`. |
| Streaming table / commit-velocity detection | 🔴 Real gap | `table_stats_history` is persisted every scan but nothing computes commits/day or classifies a table as high-velocity. Derivable from snapshot-count deltas between consecutive history rows — no new infrastructure needed, just a query + a flag + a tighter compaction cadence for flagged tables. |
| Hot/cold partition tracking for conflict avoidance | 🔴 Real gap | Per-table locks exist (`queue/locks.py`) but nothing partition-level. Once writers are actively committing (post P6), compacting a partition that's being written to causes `CommitFailedException` churn. Add a "no writes in N hours" filter to partition selection in the planner. |
| V2/V3 format detection + deletion-vector upgrade flag | 🔴 Real gap, cheap | `format-version` is already available on table metadata read in `analyze_table`; just surface it and flag V2 tables with a high delete ratio as V3-upgrade candidates. Business case: a verified AWS EMR benchmark found V3 deletion vectors **55% faster for deletes, 28.5% faster for full-table reads** vs. V2 position deletes — but that's one synthetic 10k-row benchmark on EMR 7.10/Iceberg 1.9.2, cite it as directional, not a guarantee. |
| Maintenance-operation sequencing enforcement in the UI/API (not just planner) | 🔴 Small gap | Planner gets this right internally; there's no user-facing guardrail if someone manually triggers ops out of order via the API. Low effort, prevents a real footgun once multi-engine ad-hoc runs are common. |
| Compliance / GDPR-CCPA deletion tracking | 🔴 Real gap, genuine differentiator | Verified as a real, named Ryft feature ("Ryft ensures your tables are fully GDPR and CCPA compliant"). Mostly a reporting layer over data Cryo already produces: `expire_snapshots` is a first-class operation and `audit_log` already tracks execution — track which soft-deletes are pending a snapshot-expiry cutoff and surface "N pending hard deletions, blocked until snapshot expiry after &lt;date&gt;." |
| Catalog latency monitoring (P50/P99 per catalog operation) | 🔴 Real gap, low effort | `cryo/sources/` already wraps catalog calls (REST/HMS/Glue); add timing around them and surface P50/P99 in dashboards. Note: we could not verify a specific published "alert above 500ms" threshold from any vendor — make the threshold configurable rather than presenting it as an industry standard. |
| Disaster recovery / named backup snapshots | 🔴 Real gap, medium effort | Standard `expire_snapshots` deletes everything past retention with no concept of "keep this one." Requires tagging specific snapshots as protected so expiry skips them — a real, verified Ryft feature ("Snapshot Lifecycle"). |
| Query-pattern-aware sort optimization, layout simulation, NDV stats | ✅ Implemented 2026-07-07 | Per-destination `query_log_config` block on the Connections page (Trino only) drives the new tiered pipeline: Redis Streams buffer -> Iceberg archive (`cryo_internal.queries`, partitioned by day) -> curated Postgres `query_logs` -> `query_patterns`. Retention enforced via `expire_snapshots` (default 30d, 7d on the curated Postgres table). Multi-coordinator clusters supported (`telemetry_coordinators` list). Sampling is deterministic by `query_id` hash so a crash + re-fetch selects the same subset. Files: `cryo/telemetry/iceberg_writer.py`, `cryo/telemetry/curator.py`, `cryo/telemetry/query_buffer.py`, `cryo/telemetry/ingestion.py` (rewritten to fetch + XADD only), `web/src/components/QueryLogIngestionFields.tsx`. Migration: `20260707_0018_query_log_config.py`. Tests: `tests/test_query_log_pipeline.py` (33 tests covering parse, cadence, dedupe, multi-coordinator, retention, curator no-source bootstrap). |
| AI/MCP interface | ⏳ Later, low risk | The FastAPI surface is already extensive (`current-state.md` P4). A thin read-only MCP wrapper over existing routes ("which tables need attention?") is low-effort once the API stabilizes. Both Ryft ("Data Lake for AI Agents" — verified, literal homepage headline) and LakeOps market this positioning; worth doing for parity but not urgent. |
| Multi-engine query routing, data tiering/lifecycle | ⏳ Later | Real competitor features (verified: Ryft does storage tiering; LakeOps does engine routing) but presuppose the Spark/EMR engine (P6) and cost dashboard both existing first. |

## Part 3 — Suggested sequencing

1. **Now (2–4 weeks):** Close P2 (Trino engine execute/client/load-probe) and P3
   (worker/governor/scheduler loops) end-to-end on the local stack; add CI. Nothing below this line
   is demoable without it.
2. **Next, parallelizable (2–4 weeks):** Finish the cost API/dashboard (data's ready — see Part 2);
   add metadata-ratio, streaming-velocity, and V2/V3-flag as scanner/health extensions (all cheap,
   all reuse fields already being collected).
3. **Then (4–6 weeks):** Hot/cold partition-aware scheduling; delete-ratio-triggered compaction
   mode; standalone manifest-rewrite quick action; in-UI sequencing guardrails.
4. **Then (4–6 weeks):** Compliance tracking, catalog latency monitoring, DR/backup snapshots.
5. **Later:** Scanner concurrency pass (Part 1.2); only after that, evaluate a scoped Rust
   compaction component if profiling actually justifies it.
6. **Long-term / optional:** AI/MCP interface, multi-engine routing, data tiering, and — only if
   Cryo is actually open-sourced with outside contributors — the Apache-style governance track in
   Part 6 below. For a pre-v1 project with no external users yet, GitHub-first governance is the
   right amount of process; don't build foundation-style governance ahead of having a community
   that needs it.

## Verification notes

Independently re-checked 2026-07-03 against primary sources:

- **Real, confirmed:** LakeOps (lakeops.dev), Ryft (ryft.io — acquired by Cyera April 2026, discontinued),
  Apache Amoro (ASF **incubating**, not yet a top-level project), Floe (github.com/nssalian/floe — a real
  but small/solo project, not weighted the same as the funded competitors), Firn
  (github.com/basekick-labs/firn — real, but explicitly "not yet suitable for production use,"
  pre-v1.0).
- **Corrected:** LakeOps "10x cheaper" → their own site says ~80% cost reduction. "95% faster" is
  their own marketing headline, not an independently reproduced benchmark.
- **Corrected/removed:** A claimed "RisingWave recommends alerting on catalog P99 > 500ms" — no
  such published recommendation could be found; reworded as a configurable default, not a sourced
  industry standard.
- **Corrected:** "iceberg-rust single-threaded fast path, 2.5x faster for small files" — not found
  in the cited issue (`apache/iceberg-rust#2172`) or its linked PRs; the closest real fix (a
  different PR, #1684) resolved a multi-threading bug for a 2.8x scan speedup, which is a different
  claim.
- **Confirmed with citation added:** V3 deletion vector numbers (55%/28.5%) trace to a specific AWS
  Big Data Blog EMR benchmark, not a generic industry constant — now cited as such.
- **Confirmed exactly:** delete-file-threshold default of `Integer.MAX_VALUE` (it's an action
  parameter default in Iceberg's `SizeBasedDataRewriter`, not a persisted table property — wording
  corrected); GitHub issue `apache/iceberg#16217` (DROP COLUMN on identity partition field
  corruption); AWS Glue's Puffin-based NDV stats via Theta Sketch.

## References

- [LakeOps — compaction benchmark](https://medium.com/@amitgil87/building-a-distributed-compaction-engine-for-apache-iceberg-with-rust-datafusion-09faa63c1438) — vendor blog, treat numbers as marketing, not neutral benchmark.
- [Ryft — Snapshot Lifecycle / Iceberg Backups](https://www.ryft.io/blog/announcing-iceberg-backups-in-ryft)
- [Apache Amoro — self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)
- [Floe](https://github.com/nssalian/floe)
- [Firn](https://github.com/basekick-labs/firn)
- [iceberg-rust Performance Epic](https://github.com/apache/iceberg-rust/issues/2172)
- [AWS: Iceberg v3 deletion vectors on EMR](https://aws.amazon.com/blogs/big-data/unlock-the-power-of-apache-iceberg-v3-deletion-vectors-on-amazon-emr/)
- [Iceberg issue #16217 — DROP COLUMN corruption](https://github.com/apache/iceberg/issues/16217)
- [IOMETE — Iceberg production anti-patterns](https://iomete.com/resources/blog/apache-iceberg-production-antipatterns-2026)
- [AWS Glue — Iceberg column statistics (Puffin/NDV)](https://docs.aws.amazon.com/glue/latest/dg/iceberg-column-statistics.html)
- Internal: [`todo.md`](../todo.md), [`docs/current-state.md`](./current-state.md), [`docs/research-and-roadmap.md`](./research-and-roadmap.md)

---

# Part 2 — Competitive landscape (COMPETITIVE_ANALYSIS_2026)

> Research-driven snapshot of the Iceberg maintenance landscape, Cryo's current position, and a
> prioritized backlog sourced from SIGMOD 2025 papers and production deployments.

## 1. Competitive landscape (July 2026)

### Tier 1 — Managed cloud platforms

| Platform | Key claim | Strength | Cryo gap |
|----------|-----------|----------|----------|
| **Databricks Predictive Optimization** | On-by-default for Unity Catalog tables since 2025. Uses platform telemetry to skip maintenance when ROI is low. | Deep engine integration, zero-config | Cryo needs cost-benefit gating; see Part 1.2 §3.1. |
| **AWS S3 Tables** | Managed Iceberg with auto-compaction. 90% fee reduction on compaction (July 2025). | Zero-ops for AWS-native teams | Cryo targets self-hosted; not a direct threat |
| **Snowflake Managed Iceberg** | Automatic maintenance within Snowflake. | Fully integrated for Snowflake users | Out of scope for Cryo |

### Tier 2 — Dedicated control planes

| Platform | Status | Key differentiator |
|----------|--------|-------------------|
| **LakeOps** | Active, well-funded (2026) | Rust compaction engine claiming 20x speed / 98% cheaper than Spark. Multi-engine telemetry (Trino, Spark, Snowflake, DuckDB, Flink). Signal-driven triggers on latency/cost spikes. |
| **Apache Amoro (incubating)** | Apache project, active | Self-optimizing with Flink/Spark. Mixed-Iceberg for CDC. |
| **Ryft** | **Acquired by Cyera, April 2026. Discontinued.** | Was query-aware, full lifecycle, data reordering. Customers are now in-market. |
| **Floe** | Early stage, pre-GA | Python-native, lightweight |

### Tier 3 — DIY / scripts

Scripts, Airflow DAGs, dbt macros — the baseline Cryo replaces.

### Where Cryo stands

**Cryo is squarely in Tier 2, competing directly with LakeOps as the self-hosted alternative.** The key differentiation opportunity: Cryo is open-source, self-hosted, and Trino-first — LakeOps is SaaS-only and requires sending telemetry to their platform. Ryft's exit creates a vacuum for teams who need an on-premise control plane.

**Cryo advantages today:** load-aware Trino admission (`system.runtime.queries`); A–F health grade + deduction model; per-table locks, failure isolation, auditing; partition-aware chunking with GB budgets; open source, self-hosted, no data leaves your infra.

**Cryo gaps vs LakeOps:** Puffin statistics management (NDV/bloom filters after compaction); multi-engine telemetry breadth; cost-benefit gating (AutoComp-style: skip compaction when ROI < threshold); signal-driven triggers; Flink TableMaintenance API integration; compaction effectiveness ratio tracking.

## 2. Research paper findings

### 2.1 AutoComp — SIGMOD 2025 (LinkedIn)

**Paper**: *AutoComp: Automated Data Compaction for Log-Structured Tables in Data Lakes* (arXiv:2504.04186).

**Core insight**: Compaction should be **cost-benefit gated**, not schedule-gated.

AutoComp models compaction as an optimization problem:

```
Compact a partition if:
  expected_scan_savings(partition) × query_frequency > compaction_cost(partition)
```

Key mechanisms: partition-scope vs table-scope selection; hybrid strategy; priority queue; OpenHouse exposes the trigger and AutoComp is the pluggable executor underneath. This matches Cryo's governor → engine architecture exactly.

**What to implement in Cryo (§3.1 below):** A `CompactionBenefitScore` per partition, stored in `TablePartition`, that combines `small_file_count`, `query_frequency` (from QueryLog), and `last_compacted_at`. Governor uses this score to prioritize work.

### 2.2 Puffin Bloom Filter Index — Iceberg Core (2025–2026)

Source: iceberg/pull/15311. POC merged into Spark 4.1; spec discussion active in `dev@iceberg.apache.org`.

Benchmark result: Puffin Bloom filter reduced planning from 658 files → 1 file for point lookups on high-cardinality columns. Latency dropped from 676ms → 201ms.

**Why it matters for Cryo**: Cryo already runs `rewrite_manifests` and `expire_snapshots`. Adding `compute_table_stats` (Puffin NDV + Bloom filter generation) as a 5th maintenance operation, triggered **after** compaction, is the highest-leverage single change for query performance. Stats staleness problem: Puffin blobs reference specific data files. After compaction, old Puffin files are stale — they point to files that were rewritten. This means `compute_table_stats` must be sequenced **after** compaction in every maintenance run. Cryo's current op order (`expire → orphan → optimize → manifests`) needs a 5th step.

### 2.3 Flink TableMaintenance API — Iceberg Docs (2025)

Source: iceberg.apache.org/docs/latest/flink-maintenance.

Iceberg now ships a native `TableMaintenance` API for Flink that runs compaction, snapshot expiration, and orphan cleanup as Flink streaming operators, with a `TriggerLockFactory` for concurrency safety.

**Cryo opportunity**: Add a Flink engine type that generates `TableMaintenance` configuration (scheduleOnDataFileCount, scheduleOnDataFileSize) rather than Spark SQL. The `cryo/engines/flink/` scaffold exists (`cryo/engines/flink/engine.py`).

### 2.4 Iceberg v3 Deletion Vectors

Production announcements (AWS, Databricks — 2025). Iceberg v3 introduces **deletion vectors** (similar to Delta Lake) and **single-file commits** that reduce write amplification from streaming. For streaming tables, fewer small delete files = less compaction overhead. Cryo should detect v3 tables and adjust maintenance strategies (skip eq-delete → pos-delete conversion when deletion vectors are already in use).

## 3. Prioritized feature backlog

### Priority 1 — High impact, builds on existing work

#### 3.1 AutoComp-style compaction benefit score

**What**: Per-partition `CompactionBenefitScore = (small_file_count × query_frequency) / estimated_compaction_cost`.
**Where**: `TablePartition` model + planner guardrails.
**Impact**: Stops wasting compute on cold tables; focuses budget on high-value partitions.
**Work**: Add `query_frequency` column to `TablePartition` (populated from `QueryLog`), add benefit score to planner's partition ranking (currently ranks only by small_file_count).

#### 3.2 Puffin statistics as 5th maintenance operation

**What**: Add `COMPUTE_STATISTICS` as an `OperationType`. Renders as Spark `CALL system.compute_table_stats(...)` or Trino `ANALYZE TABLE`. Always scheduled **after** `OPTIMIZE_MANIFESTS`.
**Where**: `domain/enums.py`, `planner/planner.py`, `engines/spark/sql.py`, `engines/trino/sql.py`.
**Impact**: NDV sketches improve join ordering; Bloom-filter-level pruning for point lookups.
**Config**: `compute_stats_after_optimize: bool` (default: true). Respect `analyze_columns` list for selective column stats.

#### 3.3 Compaction effectiveness ratio tracking

**What**: After each `OPTIMIZE` job completes, record `input_files_count` and `output_files_count` in `AuditLog`. Alert when ratio < 10:1.
**Where**: `cryo/db/repositories/run_finalizer.py`, `cryo/events/`.
**Impact**: Detects wasted compute immediately. Industry target is 50:1.

### Priority 2 — High impact, new subsystems

#### 3.4 Signal-driven maintenance triggers

Three new trigger signals beyond schedule + cooldown:
- `file_degradation_rate`: trigger when `small_files / total_files` crosses a threshold
- `query_latency_spike`: trigger when P95 query latency on a table increases >30%
- `cost_anomaly`: trigger when `projected_savings_usd_month` increases unexpectedly

**Where**: New `cryo/triggers/` module. Governor polls trigger conditions.

#### 3.5 Flink engine type

A new `EngineType.FLINK` that generates Flink `TableMaintenance` job configurations rather than SQL. Cryo renders the config; the Flink job executes it. Scaffold exists at `cryo/engines/flink/`.

#### 3.6 Bloom filter column recommendations

Using `QueryPattern` data, identify high-cardinality columns that appear frequently in equality filters. Recommend enabling Parquet bloom filters on those columns via `ALTER TABLE SET PROPERTIES`. **Unique feature** — no competitor surfaces this recommendation.

### Priority 3 — Medium impact, incremental

#### 3.7 Partition evolution recommendations

Analyze tables where a different partition spec would significantly improve pruning.

#### 3.8 Multi-engine telemetry breadth

Extend `TrinoQueryIngester` to also ingest from Spark History Server, Athena query history, and DuckDB query log.

#### 3.9 Iceberg v3 / deletion vector awareness

Detect Iceberg format version from table metadata. For v3 tables, skip the eq-delete → pos-delete conversion optimization.

#### 3.10 Incremental Puffin sketch maintenance

Rather than full-table re-analyze after every compaction, maintain Theta sketches incrementally — merge sketches from newly compacted files with the existing table-level sketch.

## 4. Cryo feature matrix vs competitors

| Feature | Cryo (today) | Cryo (roadmap) | LakeOps | Amoro | Databricks |
|---------|-------------|----------------|---------|-------|-----------|
| Partition-aware OPTIMIZE | ✅ | ✅ | ✅ | ✅ | ✅ |
| Load-aware Trino admission | ✅ | ✅ | ❓ | ❌ | ❌ |
| A–F health grade | ✅ | ✅ | ❌ | ❌ | ❌ |
| Per-table audit log | ✅ | ✅ | ❌ | ✅ | ✅ |
| GB-budgeted chunking | ✅ | ✅ | ❌ | ❌ | ❌ |
| Z-order / sort layout | ✅ (Phase 3) | ✅ | ✅ | ✅ | ✅ |
| Query telemetry ingestion | ✅ (Phase 1) | ✅ | ✅ | ❌ | ✅ |
| Layout recommendations | ✅ (Phase 2) | ✅ | ✅ | ❌ | ✅ |
| Native Rust compaction | 🔨 (skeleton) | ✅ | ✅ | ❌ | ❌ |
| Puffin statistics mgmt | ❌ | §3.2 | ✅ | ❌ | ✅ |
| Cost-benefit gating | ❌ | §3.1 | ✅ | ❌ | ✅ |
| Signal-driven triggers | ❌ | §3.4 | ✅ | ❌ | ✅ |
| Flink engine | ❌ | §3.5 | ✅ | ✅ | ❌ |
| Bloom filter recommendations | ❌ | §3.6 | ❌ | ❌ | ❌ |
| Compaction effectiveness ratio | ❌ | §3.3 | ❌ | ❌ | ❌ |
| Multi-catalog federation | ❌ | §3.8 | ✅ | ✅ | ✅ |
| Open source / self-hosted | ✅ | ✅ | ❌ | ✅ | ❌ |

## 5. Recommended next steps (ordered by ROI)

1. **§3.2 — Puffin statistics** as 5th maintenance op. Single PR, high impact, closes a visible gap vs LakeOps.
2. **§3.1 — AutoComp benefit score**. Improves governor prioritization directly from SIGMOD 2025 research.
3. **§3.3 — Effectiveness ratio tracking**. Cheap, prevents compute waste, shows up as a dashboard metric.
4. **§3.4 — Signal-driven triggers**. Required to claim "autonomous control plane" positioning.
5. **§3.6 — Bloom filter column recommendations**. Unique feature.
6. **§3.5 — Flink engine**. Opens streaming-team adoption path.
7. **Finish Rust engine** — get `cryo-native` to a production-ready binpack.

## 6. Key sources

- AutoComp paper: https://arxiv.org/abs/2504.04186 (SIGMOD 2025)
- LakeOps blog: https://lakeops.dev/blog/
- IOMETE competitive analysis: https://iomete.com/resources/blog/iceberg-maintenance-alternatives
- Iceberg Puffin Bloom PR: https://github.com/apache/iceberg/pull/15311
- Flink TableMaintenance API: https://iceberg.apache.org/docs/latest/flink-maintenance/
- Polaris 1.5 credential vending: https://polaris.apache.org/releases/1.5.0/
- LakeOps cost analysis: https://lakeops.dev/blog/7-iceberg-lakehouse-cost-reduction-strategies
- 2026 maintenance guide: https://dsndaily.com/apache-iceberg-table-maintenance/

---

# Part 3 — FORMATS '26 research triage

Workshop: [1st International Workshop on Data FORMATS](https://dataformats.org/) (June 2026).
Accepted papers: [dataformats.org/accepted-papers.html](https://dataformats.org/accepted-papers.html).

| Paper | PDF found? | Cryo action |
|-------|------------|-------------|
| **Zero-Scan DQ** (Verma et al., LinkedIn) | Yes — arXiv:2605.30308 | **Already shipped** (`cryo/scanner/quality.py`, profiler, Puffin NDV stub) |
| **Li et al. — How Consistent and Fresh are Lake Tables Really** | **No** — ACM DL only | **Implemented metadata freshness** (`last_snapshot_committed_at` from Iceberg snapshots; SLA uses commit time not `scan_at`) |
| **Commutative Compaction** (Douglas/Hellerstein) | ACM DL only | **Watch list** — no sprint until a concrete Iceberg/Trino integration path exists |
| **Metadata-as-Data** (Hudi, Sumit et al.) | ACM DL only | **Do not build** — Hudi-specific write-path index substrate; off-mission for Cryo |
| **Puffin vector indexes** (Borycki et al., if cited) | N/A at FORMATS | **Pattern only** — sidecar binding for sketches; no ANN index work |
| **LakeVilla / TreeCat** | arXiv for LakeVilla | **Excluded** — transaction tooling, not maintenance observability |

### What we implemented (without waiting for Li PDF)

Li et al. almost certainly measures **catalog/metadata-visible** consistency and freshness
(snapshot commit timestamps, isolation semantics) — the same class of signals Iceberg already
stores at write time. Cryo had a semantic bug: SLA "freshness" used **scanner `scan_at`**, not
**last snapshot commit**.

**Fix (2026-07-05):**
- `cryo/scanner/analyze.py` — `_snapshot_info()` now derives `first_snapshot_at` and
  `last_snapshot_committed_at` from all snapshots (min/max commit time).
- `cryo/scanner/health.py` — snapshot-age deduction uses `last_snapshot_committed_at`
  (staleness), not table creation time.
- `cryo/api/dashboard_routes.py` — `/api/sla-status` freshness = hours since last commit; adds
  `scan_staleness_hours` for Cryo scan lag.

When the Li paper becomes public, compare their SLO definitions and any cross-engine consistency
metrics; extend only if they define checks we do not already cover.

### Explicitly deferred AI tracks (still correct)

These are **by design** in [`architecture.md §2.6–2.7`](./architecture.md) and
[`execution-plan.md` Phase 9](./execution-plan.md) — not backlog neglect.

**Full RL scheduling (Decima-style):** sim-to-real transfer remains unsolved; policies are hard
to audit when they gate compaction on production tables. What we ship instead: contextual
bandit (`cryo/governor/bandit.py`).

**Chronos / TimesFM / Moirai (TS foundation models):** 2025 observability benchmarks show these
models degrade on Cryo's exact data shape (sparse, per-table, low-frequency fleet metrics across
thousands of entities). What we ship instead: rule + history-based anomaly detection and
TabPFN-style **static** health classification from current stats.

**TabPFN-TS (tabular forecasting with metadata covariates):** best *conceptual* fit but
NeurIPS-workshop stage and unproven outside GIFT-Eval. Status: dedicated research spike only.

**GNN join / lineage discovery (OmniMatch et al.):** real research but needs fleet-scale column
sampling, negative mining, and model serving — a separate product surface. What we ship instead:
embedding similarity over table/column names (`cryo/ai/similarity.py`).

**Causal Sawmill (realized savings rigor):** needs outcome history (actual_bytes_freed, latency
before/after maintenance with controls). When to revisit: after ≥90 days of paired maintenance
outcomes + query latency per table.

---

# Part 4 — Performance architecture (PERFORMANCE_AND_OPENNESS §1–§3)

## Executive summary

Performance work must serve a real bottleneck — Cryo's bottleneck right now is end-to-end path
completion, not execution-engine speed. The Python/Trino/Spark orchestration layer is correct
but incomplete (P2/P3/P6); once it runs jobs against a real Trino/Spark cluster, **then** profile
the workload and decide whether a Rust/DataFusion component is justified.

## 1. Current state analysis

Cryo's hot path is metadata-IO-bound, not compute-bound. Concretely:

- **Scanner I/O**: PyIceberg manifest reads from S3/REST/HMS — bound by object-store latency,
  not by anything in Python. Already parallelized (Part 1.2).
- **Planner**: in-memory chunk packing, no I/O — sub-millisecond per table.
- **Governor**: in-memory admission math, no I/O — sub-millisecond per request.
- **Worker → Trino**: network round-trip to Trino coordinator. Bound by Trino's own cluster
  latency, not Cryo's overhead.
- **Worker → Spark/EMR**: EMR Serverless job submission. Bound by AWS control plane.

The single biggest performance lever today is **end-to-end throughput**: a working scheduler → governor →
worker loop that actually admits and executes jobs. Right now throughput is zero because the
loop doesn't run in production.

## 2. Performance landscape

| Claim | Source | Verification status |
|-------|--------|---------------------|
| "LakeOps 95% faster than Spark" | vendor blog | **Vendor-self-reported**, single 5.5TB test → ~86% in their own numbers; not a universal constant. |
| "Rust vs Python ETL: 2.65x–15.14x" | various benchmarks | Wide range, no single multiplier. |
| "iceberg-rust 2.8x scan speedup" | PR #1684 (different from #2172) | Real, but a multi-threading bug fix, not the claim in earlier draft. |
| "V3 deletion vectors 55% faster deletes" | AWS EMR blog (specific 10k-row benchmark) | Real, but one synthetic workload. |
| "Zero-scan DQ signals" | FORMATS '26 paper | First-edition workshop; no benchmarks/OSS reference impl. |

## 3. The case for Rust + DataFusion

### Why Rust?

- **No GC** — predictable tail latency for long-running maintenance workers.
- **Native SIMD/AVX** for sort/compaction — vectorized primitives exceed JVM by single-digit
  multipliers on the relevant ops.
- **Single binary** — zero cluster to stand up; orgs with no existing Trino/Spark footprint get
  working maintenance.

### Why DataFusion?

- **Arrow-native** — zero-copy with Parquet reads via `parquet` crate.
- **Vectorized** — same execution model that makes Trino fast.
- **Embeddable** — runs in-process in a Rust binary, no separate cluster needed.

### Why only for execution?

Per [`architecture.md §1.5`](./architecture.md), the orchestrator stays in Python (FastAPI +
SQLAlchemy). Only the execution engine is a candidate for Rust. Pluggability is via the existing
`MaintenanceEngine` Protocol (`cryo/engines/base.py`).

## 4. Hybrid architecture (now superseded — see note)

> **Status:** the `cryo-engine` Rust crate was removed from this repo on 2026-07-07. The
> reference architecture in [`architecture.md §1.5`](./architecture.md) is preserved for
> reference only. Active execution paths: Trino, Spark/EMR, Flink config emitter.

---

# Part 5 — Open-source strategy (PERFORMANCE_AND_OPENNESS §4–§7)

## 1. Governance model options

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **GitHub-only, BSD-3 / Apache-2** | Lowest overhead; perfect for pre-v1 | No external review process; relies on GitHub's protection | **Recommended for now** |
| **Apache-style (incubator → TLP)** | Strong community signals; vendor-neutral | Heavy process (CLA, voting, mailing lists); needs an actual community | Defer until ≥10 external contributors |
| **Open-core / dual-license** | Revenue path for a future commercial entity | Confusing for early contributors; risks the "open core but mostly closed" perception | Reject unless there's an actual commercial plan |
| **BSL / source-available** | Recoup R&D; lets community see code | Not OSS by OSI/FSF definition; loses many benefits | Reject — defeats the goal |

## 2. Apache Way principles (applied at small scale)

A few habits that scale without process overhead:

- **Public-by-default communication** — design docs, decisions, and rationale in `docs/` and
  GitHub issues, not in private Slack. (Cryo already does this.)
- **Lazy consensus** — propose in an issue or PR, get one +1 from a maintainer with domain
  context, ship.
- **Vendor-neutral** — no features that require AWS/GCP/Azure-specific APIs in the core path.
  Cloud-specific integrations go behind a `Source`/`Destination` adapter.
- **Independent releases** — every PR is a release candidate. Tag often.

## 3. Recommended governance timeline

1. **Pre-v1** (today) — GitHub-only governance, Apache-2.0 license, `docs/GOVERNANCE.md` published.
2. **At v1.0.0** — add `CONTRIBUTING.md` review SLA, a maintainer rotation doc, and a public
   roadmap.
3. **If/when external contributors ≥ 10** — revisit Apache incubation candidacy. Requires at
   least one employer-permissive CLA flow.
4. **If/when external contributors ≥ 50** — formal TLP graduation.

## 4. Building community

- **Reproducible build + dev env** (Docker Compose three-file split — done).
- **Real CONTRIBUTING.md** with concrete setup steps (done).
- **Code of conduct** (done — added 2026-07-05).
- **Per-release changelog** (done).
- **Public roadmap** in [`execution-plan.md`](./execution-plan.md).
- **Demo deployment** that anyone can `make up` against their own data.

## 5. Licensing recommendation

**Apache License 2.0** for the core platform. Permissive enough for downstream consumption;
explicit patent grant avoids the friction MIT/BSD has with corporate legal teams.

## 6. Risk analysis

| Risk | Mitigation |
|------|------------|
| Single maintainer burnout | Document everything; "design by writing down"; minimize bus factor on critical paths |
| Big-tech forks | Apache-2 allows it; competitive feature parity keeps the project interesting |
| Cloud-provider conflict | All features are engine- and cloud-agnostic; the Flink engine emits config rather than running a Flink cluster, no provider lock-in |
| Security disclosure | `SECURITY.md` published; private disclosure channel; coordinated disclosure SLA |
| Scope creep | Roadmap in [`execution-plan.md`](./execution-plan.md); phases beyond MVP are gated on real demand |

## 7. Success metrics

| Metric | Target |
|--------|--------|
| Time-to-first-success (clone → first scan result) | < 10 min on a laptop |
| Trino / Spark engine golden-test parity | 100% (every plan fixture renders identically) |
| End-to-end maintenance job success rate | ≥ 95% after a clean install |
| OSS contributor wait time on a first PR | < 7 days |
| Public roadmap updates | Monthly |
| Median time-to-incident-root-cause | < 30 min with logs + metrics |

---

# Part 6 — UI/UX design specification (research-and-roadmap.md)

> Written 2026-07-03. Grounded in the actual implementation in `web/src/` (7 pages, AntD 6 +
> TanStack Query + Recharts). This is the canonical source for tokens, shell, patterns, and
> page-by-page behavior.

## 0. Audience and north star

The UI targets a **platform engineer** running Cryo on their own data:
- They are technical, prefer information density over decoration.
- They want to know "is my data healthy" and "what will the next maintenance run do" without
  reading documentation.
- They expect dark mode by default because that's what they look at all day.

**North star:** every page answers one operator question in under 3 seconds, with the most
useful answer at the top.

## 1. Design tokens

### 1.1 Color — light theme (main content)
- Background: `#fafafa` (panel) / `#ffffff` (surface)
- Text: `#0f172a` (primary), `#475569` (secondary), `#94a3b8` (muted)
- Accent: `#1677ff` (AntD default blue)

### 1.2 Color — dark theme (main content)
- Background: `#0b1220` (page) / `#0f172a` (panel) / `#1e293b` (elevated)
- Text: `#f8fafc` (primary), `#cbd5e1` (secondary), `#94a3b8` (muted)
- Accent: `#60a5fa` (lighter blue for contrast on dark)

### 1.3 Sidebar is always dark
- The left navigation rail is `#0f172a` in both themes -- dark always. This anchors the layout and
  matches operator eye-strain expectations.

### 1.4 Semantic color — grades, priority, status
- **Health grades**: A=`#10b981`, B=`#3b82f6`, C=`#f59e0b`, D=`#f97316`, F=`#ef4444`
- **Priority**: P0=`#ef4444`, P1=`#f59e0b`, P2=`#3b82f6`, P3=`#6b7280`
- **Status**: success=`#10b981`, warning=`#f59e0b`, error=`#ef4444`, info=`#3b82f6`, neutral=`#6b7280`

### 1.5 Typography
- Sans: IBM Plex Sans (self-hosted; see §8)
- Mono: IBM Plex Mono
- Type scale: 12, 14, 16, 18, 24, 32

### 1.6 Spacing, radius, elevation
- Spacing scale: 4, 8, 12, 16, 24, 32, 48
- Radius: 4 (inputs), 8 (cards), 12 (modals)
- Elevation: flat with subtle borders in dark mode; soft shadows in light mode

### 1.7 Motion
- 150ms ease-out for hovers
- 200ms for state transitions
- No animation on data refresh (operators hate flickering numbers)

### 1.8 Iconography
- AntD icons + Tabler for the analytics-heavy views
- Consistent stroke width (1.5px)
- Icon buttons get a tooltip with the action verb, not just the icon name

## 2. Global shell and navigation

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Cryo logo]  Overview  Health  SLA  Catalogues  Tables  Scanner   │
│  Inventory    Presets  Runs  Intelligence  Governance  Queries      │
│  Insights     Savings  Attribution                                  │
│              Settings   AWS   Cost insights   Slack   Ops          │
│                                                                       │
│                                              [search]  [theme] [user]│
└─────────────────────────────────────────────────────────────────────┘
```

- Left nav: dark, collapsible, grouped by task (Inventory / Operations / Insights / Settings).
- Top-right: global search, theme toggle, user menu.
- The Cost attribution page is intentionally not in the nav (per [`OSS_READINESS.md`](./oss-readiness.md)).

## 3. Cross-cutting patterns

### 3.1 Status / badge system (`components/status.tsx`)
Single source of truth for "this is healthy / failing / pending" UI. Every grade, priority,
status, and health signal goes through this. Status colors are theme-aware (light + dark variants).

### 3.2 Tables — the core surface
- **Header**: title + count + filter summary chips + bulk-select
- **Filter bar**: `FilterBar` with the shared predicate grammar (see
  [`developer-guide.md`](./developer-guide.md))
- **Bulk actions**: `BulkAssignBar` shows selected count + a primary action
- **Columns**: always include `qualified_table_name` tooltip, last scan time, grade, priority
- **Server pagination** above 500 rows; client pagination below

### 3.3 Filtering and bulk selection
- Filter spec is sent server-side, never a list of page IDs (per `lld.md` §13)
- "Select all matching" sends the filter spec, not a list of selected rows
- Selected count is sticky across pagination

### 3.4 Forms and drawers (Presets, Connections)
- Right-side drawer, 480-620px wide
- Form values decompose from API shape; `decompose(config) → form values` and
  `build(values) → config` are symmetric
- "Test connection" must pass before Save is enabled

### 3.5 Notifications
- AntD `notification` for transient (auto-dismiss) feedback
- AntD `message` for one-line success/error
- Alert panels (inline) for context-aware warnings

### 3.6 Loading and error states
- Every data-fetching component uses the `QueryErrorAlert` if upstream fails
- Loading skeletons are subtle (no spinners for < 200ms responses)
- Empty states are educational, not just decorative ("No tables yet — connect a lakehouse to start")

## 4. Page-by-page specification

### 4.1 Datalake Health (`/health`) — landing page
Answer: *"Is my data healthy right now?"*

Layout: KPI cards (health distribution, total tables, reclaimable storage, projected savings) →
grade distribution chart → table list filtered to D/F grades → recent maintenance runs.

### 4.2 Cost & Savings (`/cost`)
Answer: *"What is my data costing and what will maintenance save?"*

Layout: cost-overview KPIs → current-vs-projected waterfall → savings-by-operation breakdown →
per-source cost breakdown → recent cost scan history. See Part 5 for the full chart spec.

### 4.3 Table Explorer (`/tables`)
Answer: *"What tables do I have and which need attention?"*

Layout: filter bar → grade/priority columns → bulk assign bar → inline preview drawer.

### 4.4 Scanner (`/scans`, `/scans/:runId`)
Answer: *"What did the scanner just find?"*

Layout: schedule status → chunk progress → per-table scan results → live log panel with WS
connection status (per the LogList component added in the 2026-07-07 hardening pass).

### 4.5 Table Detail (`/tables/:tableId`)
Answer: *"Tell me everything about this one table."*

Layout: identity + health → partition breakdown → file-count history → recent runs → actions
(preview, run, scan).

### 4.6 Presets (`/presets`)
Answer: *"Which rules apply to which tables?"*

Layout: preset list → rules editor → assignment preview → coverage heatmap.

### 4.7 Runs (`/runs`, `/runs/:runId`)
Answer: *"What did maintenance do, and why?"*

Layout: recent runs table → per-run job list → live console (WebSocket log, manual reconnect).

### 4.8 Connections (`/connections`)
Answer: *"What can Cryo talk to?"*

Tabs: Lakehouses / Engines / AI Providers / Slack / Ops. The Engines tab now has the
QueryLogIngestionFields section per the 2026-07-07 hardening pass.

## 5. Component library reference

`web/src/components/` is the single source of truth. See `components/index.ts` for the export
list. New components ship with: tests, dark-theme variant, accessibility props, and a
storybook-style demo.

## 6. Accessibility checklist

- All actionable elements are keyboard-reachable (Tab order matches visual order)
- Color is never the only signal (grade chips always carry a letter)
- Form labels are associated with inputs
- Modals trap focus and restore on close
- ARIA live regions for WS-driven status updates

## 7. What "premium enterprise" means here, concretely

- Sub-second navigation on a 5k-table fleet
- Zero flicker on data refresh
- Every status color is theme-aware
- Every long-running action has a recoverable failure path
- Every error message tells the operator what to do next, not just what broke

## 8. Implementation notes

- AntD 6 with custom theme tokens; CSS variables for theme switching.
- TanStack Query for all data fetching; no hand-rolled `fetch()` calls in pages (per the
  2026-07-05 hardening pass).
- Recharts for bar/line/donut; waterfall is custom SVG.
- IBM Plex fonts: **self-host** `woff2` instead of pulling from Google Fonts CDN (todo item).
- WebSocket auth via short-lived ticket after handshake (per [`premortem.md §3`](./premortem.md)
  — the current `?token=` query string leaks into proxy logs; mitigation in the next iteration).

## 9. Implementation status

Per the 2026-07-05 hardening pass, every UI hardening finding from PREMORTEM_UI was addressed:
`ConfirmAction` on all destructive actions, `QueryErrorAlert` on every dashboard page, server
pagination on Runs, `TruncatedResultsBanner` on `/tables`, server-driven bulk selection, and a
new `/governance` page that surfaces previously backend-only advanced APIs.

---

# Part 7 — Cost & Savings UI specification (research-and-roadmap.md)

> Implementation guide for visualizing storage gains and cost savings in the web UI. Pairs with
> `lld.md` §16 and the cost-savings backend (Tier 0/1/2 byte accounting). Backend already
> computes `live_bytes / dead_bytes / metadata_bytes / orphan_bytes` and per-operation savings
> projections; this spec is for the UI presentation layer.

## 1. What we're adding (at a glance)

- A **hero waterfall** on the Datalake Health page showing current → projected storage drop
  broken down by operation type.
- A **savings-by-operation donut** showing relative contribution of each maintenance
  operation to total projected savings.
- A **per-table savings card** on Table Detail.
- A dedicated `/cost` page (optional) for deep cost analysis.

## 2. Choosing the right chart

| Data shape | Chart |
|------------|-------|
| Current vs projected | Waterfall (custom SVG; Recharts doesn't have one) |
| Per-operation contribution | Donut (Recharts `<PieChart innerRadius={70}>`) |
| Time series | Recharts `<LineChart>` with date axis |
| Per-source / per-schema breakdown | Recharts `<BarChart layout="vertical">` |

## 3. Data contract (types)

### 3.1 Extend `TableStatsRead` (denormalized cost summary — `lld.md` §3.2)

```typescript
interface TableCostSummary {
  table_id: UUID;
  live_bytes: number;
  dead_bytes: number;
  metadata_bytes: number;
  orphan_bytes: number;
  projected_savings_bytes: number;
  projected_savings_usd_month: number;
  computed_at: string;          // ISO 8601, UTC
}
```

### 3.2 New cost dashboard + per-table cost shapes

```typescript
interface CostDashboardSummary {
  total_live_bytes: number;
  total_dead_bytes: number;
  total_metadata_bytes: number;
  total_orphan_bytes: number;
  projected_savings_bytes: number;
  projected_savings_usd_month: number;
  by_operation: Record<OperationType, number>; // bytes freed per op
  by_source: Record<UUID, SourceCostSummary>;
  history_30d: TimeSeriesPoint[];
}
```

## 4. API client wiring

- `getCostDashboard(api, { lookbackDays = 30 }): CostDashboardSummary`
- `getTableCost(api, tableId): TableCostSummary`
- `getTableCostHistory(api, tableId): TimeSeriesPoint[]`

All in `web/src/api/client.ts`, generated from the OpenAPI schema.

## 5. Formatters & theme additions

- `formatBytes(bytes: number, opts?: { binary?: boolean; precision?: number })` — IEC binary
  default, SI decimal optional.
- `formatUSD(amount: number, opts?: { precision?: number; compact?: boolean })`.
- Theme tokens: `var(--chart-fg)`, `var(--chart-grid)`, `var(--chart-axis)` for SVG colors.

## 6. The hero: Cost-Savings Waterfall

A custom SVG waterfall on the Datalake Health page. Structure:

```
Live (start)  →  -expire  →  -orphan  →  -compact  →  -manifests  →  Projected (end)
```

Each step is a bar (current values in solid, projected values in striped). Net change is
annotated above each transition.

## 7. Savings-by-operation donut

`Recharts <PieChart>` with 5 segments: `expire_snapshots`, `remove_orphan_files`,
`rewrite_data_files`, `rewrite_manifests`, `compute_statistics`. Hover tooltip with byte
count and USD projection. Center label shows total projected savings USD/month.

## 8. Wiring into the Datalake Health page

The Health page already has the KPI strip + grade distribution. Insert the waterfall below
the KPI strip and the donut below the waterfall. Both should fetch from
`getCostDashboard(api, { lookbackDays: 7 })`.

## 9. Empty / loading / error / precision states

- **Loading**: subtle skeleton, no spinners < 200ms
- **Error**: `QueryErrorAlert` with retry
- **Empty** ("no tables scanned yet"): educational copy pointing to `/scans`
- **Precision**: tier the precision by magnitude — show "$1.2M" not "$1,234,567" at the top
  level, but full precision in tooltips

## 10. Table Detail card + optional dedicated page

Table Detail gets a compact card with the waterfall (single-table scope) and a "what will
maintenance save?" inline prediction. The dedicated `/cost` page is the drilldown.

## 11. Accessibility & responsiveness

- All charts include a sr-only table that mirrors the visual data (screen-reader support)
- Charts collapse to bar viewports < 768px wide (no horizontal scroll)
- Color-blind palette verified against deuteranopia + protanopia simulators

## 12. Testing

- Storybook stories for each chart variant (`CostWaterfall.stories.tsx`,
  `CostDonut.stories.tsx`)
- Unit tests on the formatter (`formatBytes`, `formatUSD`) — locale-aware edge cases
- Integration test that asserts the Health page renders the waterfall when data is present

## 13. Build order / checklist

- [ ] Extend OpenAPI types (`TableCostSummary`, `CostDashboardSummary`)
- [ ] Add API client helpers
- [ ] Implement `CostWaterfall` SVG component + storybook story
- [ ] Implement `CostDonut` Recharts wrapper
- [ ] Wire both into Datalake Health page
- [ ] Add `/cost` page (optional, depending on time)
- [ ] Accessibility audit + sr-only tables
- [ ] Storybook stories + unit tests
- [ ] Visual QA in both themes