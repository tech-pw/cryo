# Cryo — Competitive Analysis & Feature Roadmap (July 2026)

> Research-driven snapshot of the Iceberg maintenance landscape, Cryo's current position,
> and a prioritized backlog sourced from SIGMOD 2025 papers and production deployments.
> **Update this document when the competitive landscape changes or new papers are published.**

---

## 1. Competitive Landscape (as of July 2026)

### Tier 1 — Managed Cloud Platforms

| Platform | Key Claim | Strength | Cryo Gap |
|----------|-----------|----------|----------|
| **Databricks Predictive Optimization** | On-by-default for Unity Catalog tables since 2025. Uses platform telemetry to skip maintenance when ROI is low. | Deep engine integration, zero-config | Cryo needs cost-benefit gating; see §3.1 |
| **AWS S3 Tables** | Managed Iceberg with auto-compaction. 90% fee reduction on compaction (July 2025). | Zero-ops for AWS-native teams | Cryo targets self-hosted; not a direct threat |
| **Snowflake Managed Iceberg** | Automatic maintenance within Snowflake. | Fully integrated for Snowflake users | Out of scope for Cryo |

### Tier 2 — Dedicated Control Planes

| Platform | Status | Key Differentiator | Cryo vs |
|----------|--------|-------------------|---------|
| **LakeOps** | Active, well-funded (2026) | Rust compaction engine claiming 20x speed / 98% cheaper than Spark. Multi-engine telemetry (Trino, Spark, Snowflake, DuckDB, Flink). Signal-driven triggers on latency/cost spikes. | LakeOps is the closest direct competitor. Cryo's Rust engine is at MVP; see §3.4 |
| **Apache Amoro (incubating)** | Apache project, active | Self-optimizing with Flink/Spark. Mixed-Iceberg for CDC. | Amoro is operationally heavy (mixed-Iceberg coupling, security issues in images, weak observability). Cryo is lighter and Trino-first. |
| **Ryft** | **Acquired by Cyera, April 2026. Discontinued.** | Was query-aware, full lifecycle, data reordering. | Ryft's customers are now in-market. Cryo is the best self-hosted alternative. |
| **Floe** | Early stage, pre-GA | Python-native, lightweight | Not a production threat yet |

### Tier 3 — DIY / Scripts

Scripts, Airflow DAGs, dbt macros — the baseline Cryo replaces. Every team doing this is a Cryo prospect.

### Where Cryo Stands

**Cryo is squarely in Tier 2, competing directly with LakeOps as the self-hosted alternative.** The key differentiation opportunity: Cryo is open-source, self-hosted, and Trino-first — LakeOps is SaaS-only and requires sending telemetry to their platform. Ryft's exit creates a vacuum for teams who need an on-premise control plane.

**Cryo advantages today:**
- Load-aware Trino admission (system.runtime.queries) — unique, preserves cluster health
- A–F health grade + deduction model — best-in-class observability
- Per-table locks, failure isolation, auditing — production-grade
- Partition-aware chunking with GB budgets — avoids FilterNode errors
- Open source, self-hosted, no data leaves your infra

**Cryo gaps vs LakeOps:**
- Puffin statistics management (collect NDV/bloom filters after compaction)
- Multi-engine telemetry breadth (LakeOps consumes from DuckDB, Snowflake, Athena too)
- Cost-benefit gating (AutoComp-style: skip compaction when ROI < threshold)
- Signal-driven triggers (file degradation rate, query latency spikes, cost anomalies)
- Flink TableMaintenance API integration
- Compaction effectiveness ratio tracking

---

## 2. Research Paper Findings

### 2.1 AutoComp — SIGMOD 2025 (LinkedIn)

**Paper**: *AutoComp: Automated Data Compaction for Log-Structured Tables in Data Lakes*  
**Authors**: LinkedIn Data Platform Team  
**Venue**: ACM SIGMOD 2025 ([arXiv:2504.04186](https://arxiv.org/abs/2504.04186))

**Core insight**: Compaction should be **cost-benefit gated**, not schedule-gated.

AutoComp models compaction as an optimization problem:

```
Compact a partition if:
  expected_scan_savings(partition) × query_frequency > compaction_cost(partition)
```

**Key mechanisms:**

1. **Partition-scope vs table-scope selection**: Identify partitions with high `small_file_count × query_frequency` scores; run partition-scoped rewrites. Only fall back to full-table scope when metadata is too fragmented.

2. **Hybrid strategy**: Run incremental (partition-scope) compaction frequently; periodic (table-scope) compaction less often. Avoids the cost of full rewrites.

3. **Priority queue**: Partitions are scored and dequeued by a scheduler that respects concurrency budgets. Cryo's governor already has this shape — we need to add the cost-benefit score.

4. **Integration point**: OpenHouse (LinkedIn's catalog control plane) exposes the trigger; AutoComp is a pluggable executor underneath it. This matches Cryo's governor → engine architecture exactly.

**What to implement in Cryo (§3.1 below):** A `CompactionBenefitScore` per partition, stored in `TablePartition`, that combines `small_file_count`, `query_frequency` (from QueryLog), and `last_compacted_at`. Governor uses this score to prioritize work.

### 2.2 Puffin Bloom Filter Index — Iceberg Core (2025–2026)

**Source**: [iceberg/pull/15311](https://github.com/apache/iceberg/pull/15311)  
**Status**: POC merged into Spark 4.1; spec discussion active in dev@iceberg.apache.org

**Benchmark result**: Puffin Bloom filter reduced planning from 658 files → 1 file for point lookups on high-cardinality columns. Latency dropped from 676ms → 201ms.

**Why it matters for Cryo**: Cryo already runs `rewrite_manifests` (metadata refresh) and `expire_snapshots`. Adding `compute_table_stats` (Puffin NDV + Bloom filter generation) as a 5th maintenance operation, triggered **after** compaction, is the highest-leverage single change for query performance.

**Stats staleness problem**: Puffin blobs reference specific data files. After compaction, old Puffin files are stale — they point to files that were rewritten. This means `compute_table_stats` must be sequenced **after** compaction in every maintenance run. Cryo's current op order (`expire → orphan → optimize → manifests`) needs a 5th step.

### 2.3 Flink TableMaintenance API — Iceberg Docs (2025)

**Source**: [iceberg.apache.org/docs/latest/flink-maintenance](https://iceberg.apache.org/docs/latest/flink-maintenance/)

Iceberg now ships a native `TableMaintenance` API for Flink that runs compaction, snapshot expiration, and orphan cleanup as Flink streaming operators, with a `TriggerLockFactory` for concurrency safety. This matters for teams with streaming-heavy workloads who want maintenance co-located with their Flink jobs.

**Cryo opportunity**: Add a Flink engine type that generates `TableMaintenance` configuration (scheduleOnDataFileCount, scheduleOnDataFileSize) rather than Spark SQL. This enables streaming teams to adopt Cryo without a separate Spark cluster.

### 2.4 Iceberg v3 Deletion Vectors

**Source**: Production announcements (AWS, Databricks — 2025)

Iceberg v3 introduces **deletion vectors** (similar to Delta Lake) and **single-file commits** that reduce write amplification from streaming. For streaming tables, fewer small delete files = less compaction overhead. Cryo should detect v3 tables and adjust maintenance strategies (skip eq-delete → pos-delete conversion when deletion vectors are already in use).

---

## 3. Prioritized Feature Backlog

### Priority 1 — HIGH IMPACT, builds on existing work

#### 3.1 AutoComp-Style Compaction Benefit Score

**What**: Per-partition `CompactionBenefitScore = (small_file_count × query_frequency) / estimated_compaction_cost`.  
**Where**: `TablePartition` model + planner guardrails.  
**Impact**: Stops wasting compute on cold tables; focuses budget on high-value partitions. LakeOps and Databricks both do this.  
**Work**: Add `query_frequency` column to `TablePartition` (populated from `QueryLog`), add benefit score to planner's partition ranking (currently ranks only by small_file_count).

#### 3.2 Puffin Statistics as 5th Maintenance Operation

**What**: Add `COMPUTE_STATISTICS` as an `OperationType`. Renders as Spark `CALL system.compute_table_stats(...)` or Trino `ANALYZE TABLE`. Always scheduled **after** `OPTIMIZE_MANIFESTS`.  
**Where**: `domain/enums.py`, `planner/planner.py`, `engines/spark/sql.py`, `engines/trino/sql.py`.  
**Impact**: NDV sketches improve join ordering; Bloom-filter-level pruning for point lookups. Highest-leverage improvement for query performance after compaction.  
**Config**: `compute_stats_after_optimize: bool` (default: true). Respect `analyze_columns` list for selective column stats.

#### 3.3 Compaction Effectiveness Ratio Tracking

**What**: After each `OPTIMIZE` job completes, record `input_files_count` and `output_files_count` in `AuditLog`. Alert when ratio < 10:1 (compaction is running too often or tables don't need it).  
**Where**: `cryo/db/repositories/run_finalizer.py`, `cryo/events/`.  
**Impact**: Detects wasted compute immediately. Industry target is 50:1. Low ratio is a signal to raise cooldown or increase `min_small_files`.

### Priority 2 — HIGH IMPACT, new subsystems

#### 3.4 Signal-Driven Maintenance Triggers

**What**: Beyond schedule + cooldown, add three new trigger signals:
- `file_degradation_rate`: trigger when `small_files / total_files` crosses a threshold
- `query_latency_spike`: trigger when P95 query latency on a table increases >30% (from telemetry)
- `cost_anomaly`: trigger when `projected_savings_usd_month` increases unexpectedly

**Where**: New `cryo/triggers/` module. Governor polls trigger conditions.  
**Why**: LakeOps's primary differentiator. This is what separates "autonomous control plane" from "cron-based maintenance."

#### 3.5 Flink Engine Type

**What**: A new `EngineType.FLINK` that generates Flink `TableMaintenance` job configurations (YAML / Java API config) rather than SQL. Cryo renders the config; the Flink job executes it.  
**Where**: `cryo/engines/flink/` (new module), `cryo/domain/enums.py`.  
**Target users**: Teams running CDC or streaming ingestion via Flink who want Cryo to manage their maintenance policy.

#### 3.6 Bloom Filter Column Recommendations

**What**: Using `QueryPattern` data, identify high-cardinality columns that appear frequently in equality filters (`WHERE user_id = ?`). Recommend enabling Parquet bloom filters on those columns via `ALTER TABLE SET PROPERTIES`.  
**Where**: Extend `LayoutRecommendation` with a `bloom_filter_columns` field.  
**Impact**: Parquet-level bloom filters are available today (no Puffin needed). For UUID/ID columns, can reduce file-level scanning by 99%+.

### Priority 3 — MEDIUM IMPACT, incremental

#### 3.7 Partition Evolution Recommendations

**What**: Analyze tables where a different partition spec would significantly improve pruning. For example: a table partitioned by `YEAR(created_at)` but always queried by day should be re-partitioned by `DAY(created_at)`.  
**Where**: `cryo/optimizer/layout_analyzer.py` extension.

#### 3.8 Multi-Engine Telemetry Breadth

**What**: Extend `TrinoQueryIngester` to also ingest from Spark History Server (JSON API), Athena query history (AWS API), and DuckDB query log (local file). This matches LakeOps's multi-engine telemetry claim.  
**Where**: `cryo/telemetry/ingestion.py` — add `SparkHistoryIngester`, `AthenaIngester`.

#### 3.9 Iceberg v3 / Deletion Vector Awareness

**What**: Detect Iceberg format version from table metadata. For v3 tables, skip the `eq-delete → pos-delete conversion` optimization (deletion vectors handle this differently). Adjust health scoring.  
**Where**: `cryo/scanner/signals.py`, `cryo/planner/planner.py`.

#### 3.10 Incremental Puffin Sketch Maintenance

**What**: Rather than full-table re-analyze after every compaction, maintain Theta sketches incrementally — merge sketches from newly compacted files with the existing table-level sketch. O(new data) instead of O(table).  
**Research basis**: LakeOps blog + Puffin spec extensibility.  
**Where**: Phase 2 of `COMPUTE_STATISTICS` operation.

---

## 4. Cryo Feature Matrix vs Competitors

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

---

## 5. Recommended Next Steps (ordered by ROI)

1. **§3.2 — Puffin statistics** as 5th maintenance op. Single PR, high impact, closes a visible gap vs LakeOps.
2. **§3.1 — AutoComp benefit score**. Improves governor prioritization directly from SIGMOD 2025 research.
3. **§3.3 — Effectiveness ratio tracking**. Cheap, prevents compute waste, shows up as a dashboard metric.
4. **§3.4 — Signal-driven triggers**. Required to claim "autonomous control plane" positioning vs LakeOps.
5. **§3.6 — Bloom filter column recommendations**. Unique feature — no competitor surfaces this recommendation.
6. **§3.5 — Flink engine**. Opens streaming-team adoption path; differentiates from Spark-only perception.
7. **Finish §4 Rust engine** — get `cryo-native` to a production-ready binpack. Compile-verified, benchmark-tested. LakeOps's entire performance claim rests on this layer.

---

## 6. Key Sources

- AutoComp paper: https://arxiv.org/abs/2504.04186 (SIGMOD 2025)
- LakeOps blog: https://lakeops.dev/blog/
- IOMETE competitive analysis: https://iomete.com/resources/blog/iceberg-maintenance-alternatives
- Iceberg Puffin Bloom PR: https://github.com/apache/iceberg/pull/15311
- Flink TableMaintenance API: https://iceberg.apache.org/docs/latest/flink-maintenance/
- Polaris 1.5 credential vending: https://polaris.apache.org/releases/1.5.0/
- LakeOps cost analysis: https://lakeops.dev/blog/7-iceberg-lakehouse-cost-reduction-strategies
- 2026 maintenance guide: https://dsndaily.com/apache-iceberg-table-maintenance/
