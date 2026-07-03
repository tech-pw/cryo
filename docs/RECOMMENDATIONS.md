# Cryo Feature Recommendations

> **Generated from competitive analysis, Iceberg ecosystem research, and community pain point analysis.**
> 
> This document prioritizes features that would differentiate Cryo and address real production problems
> faced by the Iceberg community.

---

## Priority Legend

| Priority | Meaning | Typical Effort |
|----------|---------|----------------|
| **P0** | Critical — must have for production readiness | 1-2 weeks |
| **P1** | High — significant competitive advantage | 1-3 weeks |
| **P2** | Medium — valuable but not blocking | 2-4 weeks |
| **P3** | Low — nice to have, future consideration | Variable |

---

## P0 — Critical Priority

### 1. Delete File Tracking & Optimization

**What:** Track position deletes vs. equality deletes separately. Surface delete-file-to-data-file ratio
per partition. Add `rewrite_position_delete_files` as a maintenance operation.

**Reason:**
- Merge-on-Read (MoR) tables accumulate delete files with every UPDATE/DELETE operation
- Equality deletes are O(D × R) — evaluating D delete entries against R rows per file
- Default `delete-file-threshold` in Iceberg is `Integer.MAX_VALUE` (effectively disabled)
- Position deletes compound: 100 deletes/hour → 16,800 delete files/week without compaction
- Teams don't realize reads are degrading until query latency doubles

**Example Impact:**
> A CDC table receiving 10,000 updates/hour accumulates delete files silently. After one week,
> query latency increased from 2s to 45s. With delete file tracking, Cryo would have alerted
> at 10% delete ratio and triggered targeted compaction, maintaining <5s query latency.

**Competitor Reference:** Amoro, LakeOps both track and optimize delete files specifically.

---

### 2. Streaming Table Detection & Aggressive Compaction

**What:** Detect tables with high commit velocity (>100 commits/day). Auto-classify as "streaming"
and apply aggressive compaction policies. Track if compaction is keeping pace with write rate.

**Reason:**
- Streaming pipelines committing every 10-60 seconds create thousands of tiny files
- A Flink job with 60-second checkpoints across 100 partitions creates 144,000 files/day
- Query planning balloons from 200ms to 45+ seconds with 100k+ small files
- Real case: 45 million data files, metadata reached 5TB (larger than actual data)

**Example Impact:**
> Streaming IoT pipeline writing to 50 partitions at 60-second intervals. Without detection,
> file count reached 500k in one week, causing coordinator OOM. With streaming detection,
> Cryo would trigger hourly compaction on hot partitions, keeping file count under 10k.

**Competitor Reference:** LakeOps and Ryft auto-detect streaming patterns and adjust maintenance frequency.

---

### 3. Hot/Cold Partition Tracking for Conflict Avoidance

**What:** Track partition write activity. Classify partitions as "hot" (recently written) or "cold"
(no writes in N hours). Only compact cold partitions to avoid conflicts with streaming writers.

**Reason:**
- Compaction on active partitions causes `CommitFailedException` with concurrent writers
- Streaming writers with aggressive retry logic can starve compaction jobs
- Partial compaction failures leave tables in inconsistent state requiring manual recovery
- Compaction jobs repeatedly failing on hot partitions waste compute

**Example Impact:**
> CDC pipeline writes continuously to today's partition while batch queries need fast reads.
> Compaction targeting today's partition fails 15 times due to conflicts, never completing.
> With hot/cold tracking, Cryo compacts only partitions with no writes in 2+ hours,
> achieving 95% compaction success rate.

**Competitor Reference:** LakeOps uses partition activity signals to avoid conflicts.

---

### 4. Maintenance Operation Sequencing

**What:** Enforce correct maintenance order in presets: Expire Snapshots → Remove Orphans →
Compact Data Files → Rewrite Manifests. Validate retention settings before orphan cleanup.

**Reason:**
- Running operations out of order can corrupt tables or leave orphans
- Orphan cleanup with retention < max write duration can delete in-progress files
- Compacting before expiring snapshots wastes compute on data about to be garbage collected
- Teams learn correct order only after production incidents

**Example Impact:**
> Team runs compaction first, then expire snapshots. 80% of compacted files were from
> expired snapshots — wasted 4 hours of Spark compute. With enforced sequencing,
> Cryo expires first, reducing compaction scope by 80% and completing in 45 minutes.

**Competitor Reference:** LakeOps documents and enforces operation sequencing.

---

### 5. Metadata-to-Data Ratio Monitoring

**What:** Track and alert when metadata size exceeds X% of data size. Include manifest count,
manifest total bytes, and snapshot count in health scoring.

**Reason:**
- Every commit creates a snapshot; without expiration, metadata grows unbounded
- Table with 10,000 commits can have 1 million manifest files
- Query planning time increases linearly with snapshot count, even when data hasn't grown
- Real case: Metadata larger than actual data, causing coordinator OOM

**Example Impact:**
> Table has 500GB of data but 2TB of metadata across 50,000 snapshots. Query planning
> takes 30 seconds. With metadata ratio alerting at 10%, Cryo would have flagged
> this at 50GB metadata and recommended snapshot expiration.

**Competitor Reference:** LakeOps and Ryft track metadata health as a primary metric.

---

## P1 — High Priority

### 6. V2/V3 Format Version Detection

**What:** Detect table format version (V1/V2/V3). Flag V1 tables for upgrade. Identify V2 tables
that would benefit from V3 deletion vectors. Adjust health scoring based on version.

**Reason:**
- V3 deletion vectors are 55% faster for deletes and 28.5% faster for reads vs V2 position deletes
- V2 position deletes accumulate as separate files; V3 stores as compact Roaring Bitmaps
- V1 tables have partition ID tracking issues that cause corruption on evolution
- V3 reduces compaction urgency for CDC tables significantly

**Example Impact:**
> CDC table on V2 requires compaction every 2 hours to keep delete files manageable.
> After upgrading to V3, the same workload tolerates 8-hour compaction intervals
> because deletion vectors have minimal read-time overhead. Compute costs reduced 75%.

**Competitor Reference:** Modern platforms assume V3 for new tables.

---

### 7. Query-Pattern-Aware Sort Optimization

**What:** Analyze query patterns (WHERE, JOIN, GROUP BY columns) to recommend optimal sort orders.
Detect sort drift when files are written with different sort orders.

**Reason:**
- Without sorting, min/max statistics span the entire value range, making file pruning useless
- Sort order chosen at table creation goes stale as access patterns shift
- Changing sort order rewrites every data file — expensive if the new layout is worse
- Competitors like LakeOps achieve 50%+ I/O reduction with query-aware sorting

**Example Impact:**
> Table sorted by `user_id` but 90% of queries filter by `event_date`. File pruning
> effectiveness: 5%. After analyzing query patterns and re-sorting by `event_date`,
> file pruning effectiveness: 85%, reducing scan volume by 10x.

**Competitor Reference:** LakeOps collects telemetry across all engines and applies optimal sort orders.

---

### 8. Manifest Fragmentation Alerting

**What:** Track manifest count and average files per manifest. Alert when manifest count exceeds
threshold (e.g., 500). Recommend `rewrite_manifests` proactively.

**Reason:**
- A well-compacted table with 2,000 tiny manifests still has slow scan planning
- Query planning requires opening every manifest to evaluate statistics
- Manifest rewriting is cheap (metadata only) but delivers outsized planning improvements
- Teams that master compaction often overlook manifests

**Example Impact:**
> Table has 2,500 manifest files after 6 months of streaming writes. Query planning
> takes 45 seconds. After `rewrite_manifests`, manifest count drops to 50,
> planning time drops to 2 seconds. 95% improvement with no data rewrite.

**Competitor Reference:** Amoro automatically triggers manifest optimization.

---

### 9. Continuous/Event-Driven Maintenance Mode

**What:** Move from cron-based scheduling to event-driven triggers based on table health signals
(file count, delete ratio, manifest count, snapshot velocity).

**Reason:**
- Naive nightly compaction: streaming tables accumulate 50k files in 6 hours, wait until midnight
- Tables with no new data get compacted anyway, wasting compute
- Event-driven triggers compact only when needed, react to workload changes in real-time
- Amoro's continuous feedback loop checks table state every few seconds

**Example Impact:**
> Streaming table crosses 1,000 files at 2 PM. Cron-based compaction waits until
> midnight — by then, 50,000 files accumulated. Event-driven trigger at 1,000 files
> fires compaction within 30 minutes, keeping file count under 2,000 consistently.

**Competitor Reference:** Amoro runs continuous feedback loop; LakeOps uses event-driven triggers.

---

### 10. Partition Cardinality & Skew Warnings

**What:** Track partition count, files per partition distribution, and partition skew ratio.
Alert on high cardinality (>10k partitions) or extreme skew (max/median > 10x).

**Reason:**
- High-cardinality partitioning (50k sensors) creates millions of tiny partitions
- Compaction can't help across partition boundaries
- Skewed partitions mean some partitions have 10,000 files while others have 10
- No partitioning on large tables forces full table scans

**Example Impact:**
> Table partitioned by `sensor_id` with 50,000 sensors. Each partition has 2-5 files,
> too small to compact. Total: 200k files that can't be consolidated. With cardinality
> warning at table creation, recommendation to use `bucket(sensor_id, 100)` would
> create 100 balanced partitions with ~2,000 files each, enabling effective compaction.

**Competitor Reference:** IOMETE surfaces partition recommendations in table health dashboard.

---

### 11. Per-Table Storage Cost Breakdown

**What:** Break down storage into: live data, dead data (expired snapshots not yet GC'd),
orphan files, metadata, delete files. Show cost in $/month per category.

**Reason:**
- Storage costs hidden: teams don't know where money is going
- Orphan files can accumulate to 200TB before anyone notices
- Snapshot overhead can exceed actual data size
- No attribution of costs to specific tables or operations

**Example Impact:**
> Monthly S3 bill: $15,000. After cost breakdown, discovery: $8,000 is orphan files
> across 50 tables that were never cleaned up. Running orphan cleanup recovers
> 200TB and reduces monthly bill by 53%.

**Competitor Reference:** LakeOps, Ryft provide detailed cost visibility and FinOps features.

---

### 12. Compliance Deletion Tracking (GDPR/CCPA)

**What:** Track tables with soft deletes awaiting hard deletion. Calculate when snapshots can
be expired to truly delete data. Generate compliance audit reports.

**Reason:**
- Iceberg `DELETE` is a soft delete; time travel can bring back "deleted" rows
- True deletion requires `expire_snapshots` after DELETE
- Regulated industries need to prove data is actually deleted
- Manual tracking of "when can we expire" is error-prone

**Example Impact:**
> GDPR deletion request for user data. Team runs DELETE but forgets to expire snapshots.
> 6 months later, audit reveals deleted data is still accessible via time travel.
> With compliance tracking, Cryo alerts: "Table X has 500 pending hard deletions
> that require snapshot expiry older than 2024-01-15."

**Competitor Reference:** Ryft provides automated GDPR/CCPA compliance cleanup.

---

## P2 — Medium Priority

### 13. Layout Simulation Before Commit

**What:** Test proposed sort/partition changes on a real Iceberg branch. Replay production
queries and compare cost/performance impact before applying to production.

**Reason:**
- Changing sort order rewrites every data file — expensive if wrong
- No way to predict impact without actually doing the rewrite
- Teams make sort decisions based on guesses, not data
- LakeOps reports this feature prevents costly mistakes

**Example Impact:**
> Team considers changing sort order from `user_id` to `timestamp`. Without simulation,
> they rewrite 10TB of data, discover queries are 20% slower, then rewrite again.
> With simulation, they test on a branch first, discover the regression, and avoid
> 20TB of wasted rewrites.

**Competitor Reference:** LakeOps offers Layout Simulations with real query replay.

---

### 14. Column-Level Statistics (NDV via Puffin)

**What:** Generate and surface Number of Distinct Values (NDV) statistics via Puffin files.
Use for join cardinality estimation and partition recommendations.

**Reason:**
- NDV enables query optimizers to choose better join orders
- Helpful for recommending partition strategies (high NDV = bad partition key)
- AWS Glue and Impala already use Puffin NDV stats
- Can identify columns with unexpected cardinality (data quality signal)

**Example Impact:**
> Join between two large tables defaults to hash join, takes 30 minutes. With NDV
> statistics showing one side has 100 distinct values, optimizer chooses broadcast
> join, completing in 3 minutes. 10x improvement.

**Competitor Reference:** AWS Glue Data Catalog generates Puffin NDV stats automatically.

---

### 15. Schema Evolution Audit Trail

**What:** Track all DDL changes (ADD COLUMN, DROP COLUMN, RENAME, partition evolution) with
timestamps, users, and before/after state. Warn before dangerous operations.

**Reason:**
- Dropping columns referenced by old partition specs corrupts table metadata
- `CREATE OR REPLACE TABLE` can silently overwrite concurrent changes
- Metadata corruption is often unrecoverable
- Users discover corruption only when queries fail

**Example Impact:**
> Developer drops column `event_hour` that was used in a historical partition spec.
> Table becomes unqueryable. With audit trail and pre-check, Cryo blocks the drop:
> "Cannot drop column — referenced by partition spec #2 in manifest history."

**Competitor Reference:** None specifically, but this addresses a known Iceberg GitHub issue (#16217).

---

### 16. Compaction Backlog & ROI Tracking

**What:** Track compaction backlog (files awaiting compaction), compaction throughput,
and ROI ($ saved by reduced GET requests, storage freed).

**Reason:**
- No visibility into whether compaction is keeping pace with writes
- Teams don't know if compaction is worth the compute cost
- Backlog can grow silently until death spiral
- Need to justify maintenance compute spend to leadership

**Example Impact:**
> Compaction backlog: 500k files. Compaction throughput: 10k files/hour.
> Time to clear backlog: 50 hours. Alert: "Compaction falling behind write rate."
> After scaling compaction workers 3x, backlog clears in 17 hours.
> ROI report: "Compaction saved $3,200/month in S3 GET request costs."

**Competitor Reference:** LakeOps shows real-time compaction activity and cost savings.

---

### 17. Catalog Latency Monitoring

**What:** Monitor catalog response times per operation (list, get, commit). Alert when
P99 exceeds threshold (e.g., 500ms). Compare performance across connected catalogs.

**Reason:**
- Catalog latency directly impacts query planning time
- Different catalogs (REST, Glue, Unity, Polaris) have different performance characteristics
- P99 catalog spikes cascade into query timeouts
- Teams don't realize catalog is the bottleneck

**Example Impact:**
> Queries suddenly take 10 seconds to start. Investigation reveals Glue catalog
> P99 latency spiked to 8 seconds due to throttling. With catalog monitoring,
> alert fires immediately, team switches to REST catalog, latency drops to 200ms.

**Competitor Reference:** RisingWave recommends alerting on catalog P99 > 500ms.

---

### 18. Disaster Recovery / Backup Snapshots

**What:** Support structured recovery points (daily/weekly snapshots preserved separately).
Enable easy restore to recovery points with a single action.

**Reason:**
- Standard snapshot expiration deletes ALL old snapshots
- No way to keep "known good" snapshots for disaster recovery
- Accidental data corruption may not be discovered for days
- Ryft's Snapshot Lifecycle feature is a key differentiator

**Example Impact:**
> Bad data pipeline corrupts 30 days of data. Standard time travel only goes back
> 7 days. Data is unrecoverable. With weekly recovery snapshots, restore to
> last week's known-good state, losing only 7 days instead of 30.

**Competitor Reference:** Ryft provides Snapshot Lifecycle with daily/weekly recovery points.

---

## P3 — Lower Priority

### 19. Multi-Engine Query Routing

**What:** Route queries across engines (Trino, Spark, Athena, DuckDB) based on workload
characteristics — optimize for cost, latency, or throughput.

**Reason:**
- Different engines excel at different workloads
- Teams manually choose engines, often suboptimally
- Cost varies significantly between engines for same query
- LakeOps reports significant savings from intelligent routing

**Example Impact:**
> Point lookup query routed to Spark cluster, takes 30 seconds and costs $0.50.
> With query routing, same query goes to DuckDB, takes 2 seconds and costs $0.02.
> 96% cost reduction for point lookups.

**Competitor Reference:** LakeOps provides multi-engine query routing.

---

### 20. AI/Agent-Native Interface (MCP)

**What:** Expose Cryo functionality via Model Context Protocol (MCP) for AI agent integration.
Enable natural language queries about table health and maintenance recommendations.

**Reason:**
- AI agents are becoming primary interface for data platform operations
- LakeOps and Ryft market themselves as "AI-ready" platforms
- MCP enables integration with Claude, GPT, and other AI assistants
- Natural language reduces learning curve for operators

**Example Impact:**
> Operator asks AI agent: "Which tables need immediate attention?"
> Agent queries Cryo MCP, responds: "3 tables have critical health grades:
> orders (500k small files), events (90% delete ratio), users (10k snapshots).
> Recommend running preset 'aggressive-cleanup' on all three."

**Competitor Reference:** LakeOps markets "Agentic AI" support; Ryft positions as "Data Lake for AI Agents."

---

### 21. Rust/DataFusion Execution Engine

**What:** Replace JVM-based execution (Spark/Trino) with Rust-based engine using Apache
DataFusion for compaction operations.

**Reason:**
- LakeOps claims 95% faster compaction with Rust vs Spark
- No JVM startup, GC pauses, or idle cluster overhead
- Bounded memory, lock-free parallelism
- Significant cost reduction for maintenance operations

**Example Impact:**
> Spark compaction of 1TB table: 27 minutes, costs $15 in cluster time.
> Rust/DataFusion compaction: 3 minutes, costs $1.50. 90% cost reduction.

**Competitor Reference:** LakeOps built custom Rust engine with DataFusion, benchmarks 95% faster.

**Note:** This is a major architectural change — consider as a long-term roadmap item.

---

### 22. Data Tiering / Lifecycle Policies

**What:** Automatically tier data to cheaper storage classes based on access patterns and age.
Move cold data to S3 Glacier, keep hot data on S3 Standard.

**Reason:**
- Old partitions rarely accessed but stored at same cost as hot data
- Manual tiering is error-prone and often forgotten
- Can reduce storage costs by 50%+ for historical data
- Ryft provides this as a key cost optimization feature

**Example Impact:**
> 2 years of data, 80% older than 90 days and rarely accessed. All on S3 Standard
> at $0.023/GB. Moving cold data to Glacier at $0.004/GB reduces storage cost by 70%.

**Competitor Reference:** Ryft provides automatic data tiering based on usage patterns.

---

## Summary Table

| Priority | Feature | Category | Key Benefit |
|----------|---------|----------|-------------|
| P0 | Delete file tracking & optimization | Health | Prevent MoR read degradation |
| P0 | Streaming table detection | Health | Prevent small file death spiral |
| P0 | Hot/cold partition tracking | Operations | Avoid compaction conflicts |
| P0 | Maintenance operation sequencing | Operations | Prevent corruption, save compute |
| P0 | Metadata-to-data ratio monitoring | Health | Prevent metadata bloat |
| P1 | V2/V3 format version detection | Health | Enable deletion vectors |
| P1 | Query-pattern-aware sort optimization | Performance | 10x scan reduction |
| P1 | Manifest fragmentation alerting | Health | 95% planning time improvement |
| P1 | Continuous/event-driven maintenance | Operations | React to workload changes |
| P1 | Partition cardinality & skew warnings | Health | Prevent partition mistakes |
| P1 | Per-table storage cost breakdown | Cost | 50%+ cost recovery |
| P1 | Compliance deletion tracking | Compliance | GDPR/CCPA audit readiness |
| P2 | Layout simulation before commit | Operations | Prevent costly mistakes |
| P2 | Column-level statistics (NDV) | Performance | Better query plans |
| P2 | Schema evolution audit trail | Safety | Prevent metadata corruption |
| P2 | Compaction backlog & ROI tracking | Operations | Justify maintenance spend |
| P2 | Catalog latency monitoring | Health | Identify bottlenecks |
| P2 | Disaster recovery / backup snapshots | Safety | Data protection |
| P3 | Multi-engine query routing | Performance | 96% cost reduction |
| P3 | AI/Agent-native interface (MCP) | UX | Natural language operations |
| P3 | Rust/DataFusion execution engine | Performance | 95% faster compaction |
| P3 | Data tiering / lifecycle policies | Cost | 70% storage savings |

---

## Implementation Roadmap Suggestion

### Phase 1: Health & Detection (4-6 weeks)
- P0: Delete file tracking
- P0: Streaming table detection  
- P0: Metadata ratio monitoring
- P1: V2/V3 format detection
- P1: Manifest fragmentation alerting

### Phase 2: Operations & Safety (4-6 weeks)
- P0: Hot/cold partition tracking
- P0: Maintenance operation sequencing
- P1: Partition cardinality warnings
- P2: Schema evolution audit trail

### Phase 3: Cost & Compliance (4-6 weeks)
- P1: Per-table storage cost breakdown
- P1: Compliance deletion tracking
- P2: Compaction backlog & ROI tracking
- P2: Catalog latency monitoring

### Phase 4: Advanced Features (8+ weeks)
- P1: Continuous/event-driven maintenance
- P1: Query-pattern-aware sort optimization
- P2: Layout simulation
- P2: NDV statistics
- P2: Disaster recovery snapshots

### Phase 5: Differentiators (Long-term)
- P3: Multi-engine query routing
- P3: AI/MCP interface
- P3: Rust execution engine
- P3: Data tiering

---

## References

- [LakeOps Documentation](https://lakeops.dev/docs)
- [Apache Amoro](https://amoro.apache.org/)
- [Ryft Platform](https://www.ryft.io/)
- [Iceberg GitHub Issues](https://github.com/apache/iceberg/issues)
- [Iceberg Production Anti-Patterns (IOMETE)](https://iomete.com/resources/blog/apache-iceberg-production-antipatterns-2026)
- [Iceberg Maintenance Guide (LakeOps)](https://lakeops.dev/blog/automating-iceberg-table-maintenance)
