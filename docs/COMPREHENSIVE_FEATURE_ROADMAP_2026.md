# Cryo — Comprehensive Feature Roadmap (2026)
## Every Feature & Page We Can Add, Ranked by Impact

> **Research Methodology**: Deep analysis of LakeOps, Databricks, Snowflake, Apache Amoro, AWS, research papers (AutoComp SIGMOD 2025, Puffin Bloom filters, Iceberg v3 spec), and 2026 production deployment patterns across Trino, Spark, Flink, Dremio, and Polaris ecosystems.
> 
> **Last Updated**: July 3, 2026

---

## Table of Contents

1. [Tier 1 — Critical (0–3 months)](#tier-1--critical-03-months)
2. [Tier 2 — High Impact (3–6 months)](#tier-2--high-impact-36-months)
3. [Tier 3 — Strategic Differentiators (6–12 months)](#tier-3--strategic-differentiators-612-months)
4. [Tier 4 — Enterprise & Monetization (12+ months)](#tier-4--enterprise--monetization-12-months)
5. [UI/UX Pages Required](#uiux-pages-required)
6. [Implementation Notes](#implementation-notes)

---

## Tier 1 — Critical (0–3 months)

### 1. **Puffin Statistics Management** ✅ PARTIALLY IMPLEMENTED
**Priority**: P0  
**Impact**: Highest-leverage single improvement for query performance after compaction  
**Effort**: 1 week  
**Competitive Gap**: LakeOps ✅, Databricks ✅, Cryo 🔨

**What**: Add `COMPUTE_STATISTICS` as a 5th maintenance operation type. Renders as:
- Spark: `CALL system.compute_table_stats(table => 'db.orders', analyze_columns => array('user_id', 'order_date'))`
- Trino: `ANALYZE TABLE catalog.schema.orders`

**Why**: Puffin NDV sketches improve join ordering; Bloom-filter-level pruning reduces file-level scanning by 99%+ for point lookups on high-cardinality columns. Iceberg metadata already tracks this; we just need to trigger the compute operations.

**Scope**:
- Add `COMPUTE_STATISTICS` to `OperationType` enum
- Render SQL in both `engines/spark/sql.py` and `engines/trino/sql.py`
- Sequence after `OPTIMIZE_MANIFESTS` in planner (stats must run after compaction to avoid stale Puffin blobs)
- Config: `compute_stats_after_optimize: bool` (default: true)
- Selective column stats via `analyze_columns` list (high-cardinality, frequently filtered)
- **Status**: SQL rendering complete; integration with planner pipeline needed

---

### 2. **AutoComp Benefit Scoring & Prioritization** ✅ PARTIALLY IMPLEMENTED
**Priority**: P0  
**Impact**: Stops wasting compute on cold tables; focuses budget on high-ROI partitions  
**Effort**: 1 week  
**Competitive Gap**: LakeOps ✅, Databricks ✅, Cryo 🔨

**What**: Per-partition `CompactionBenefitScore = (small_file_count × query_frequency) / estimated_rewrite_cost_factor`.

**Implementation**:
- Add `query_frequency` column to `TablePartition` (populated from `QueryLog`)
- Add `compaction_benefit_score` column
- Planner ranks partitions by benefit score DESC (currently ranks only by small_file_count)
- Governor dequeues highest-benefit partitions first
- **Status**: Schema added; benefit score computation implemented; integration with governor queue needed

**Research Basis**: SIGMOD 2025 — *AutoComp: Automated Data Compaction for Log-Structured Tables*

---

### 3. **Compaction Effectiveness Ratio Tracking** ✅ IMPLEMENTED
**Priority**: P0  
**Impact**: Detects wasted compute; industry target 50:1, alerts when <10:1  
**Effort**: 2 days  
**Competitive Gap**: LakeOps ❌, Amoro ❌, Cryo ✅

**What**: After each `OPTIMIZE` completes, record:
- `input_files_count` (files read)
- `output_files_count` (files written)
- `effectiveness_ratio = input_files_count / output_files_count`

**Where**: Parse from Trino `OPTIMIZE` result rows (`rewritten_data_files_count` / `added_data_files_count`), store in `AuditLog`.

**Alert**: When ratio < 10:1 → raise cooldown or increase `min_small_files`.

**Status**: ✅ Complete (commit `20982e2`)

---

### 4. **Signal-Driven Maintenance Triggers** ✅ IMPLEMENTED
**Priority**: P0  
**Impact**: "Autonomous control plane" positioning vs LakeOps; this is the differentiator  
**Effort**: 2 weeks  
**Competitive Gap**: LakeOps ✅, Databricks ✅, Cryo ✅

**What**: Beyond schedule + cooldown, add trigger signals:
1. **File degradation rate**: Trigger when `small_files / total_files` crosses threshold
2. **Query latency spike**: Trigger when P95 latency on a table increases >30% (from telemetry)
3. **Cost anomaly**: Trigger when projected savings increase unexpectedly or health grade degrades

**Where**: `cryo/triggers/` module. Governor polls signals; signals bypass cooldown and off-peak policies.

**Status**: ✅ Complete (commit `20982e2`)

---

### 5. **Query Performance Dashboard**
**Priority**: P0  
**Impact**: Core observability; every team needs this  
**Effort**: 1 week  
**Competitive Gap**: All competitors have this; Cryo needs parity

**What**: UI page showing:
- Top 10 slowest queries (last 24h)
- Query latency P50/P95/P99 per table
- Queries by engine (Trino vs Spark)
- Failed queries with error messages
- Query-to-table join (which queries hit which tables)

**Data Source**: `QueryLog` table (already exists from Phase 1 implementation)

**UI Components**:
- Time-series chart (query latency trend)
- Table with filters (by table, by engine, by user, by status)
- Drill-down to query detail page (EXPLAIN plan, duration, rows scanned)

---

### 6. **Table Health Trends Page**
**Priority**: P0  
**Impact**: Proactive monitoring; shows degradation before it becomes critical  
**Effort**: 3 days  
**Competitive Gap**: Amoro ❌, LakeOps ✅

**What**: UI page showing per-table trends over 30 days:
- Health grade over time (A → B → C trajectory)
- Small file count trend
- Total size trend
- Snapshot count trend
- Last maintenance timestamp
- Projected monthly S3 cost trend

**Data Source**: `TableStatsHistory` table

**UI Components**:
- Multi-line chart (grade, small files, size) with dual Y-axis
- Table list sorted by "most degraded in last 7 days"
- Filters: by catalog, by schema, by current grade

---

### 7. **Bloom Filter Column Recommendations** ✅ PARTIALLY IMPLEMENTED
**Priority**: P1  
**Impact**: Parquet-level bloom filters reduce file-level scanning by 99%+ for UUID/ID columns  
**Effort**: 3 days  
**Competitive Gap**: LakeOps ❌, Databricks ❌ (unique feature)

**What**: Using `QueryPattern` data, identify high-cardinality columns that appear frequently in equality filters (`WHERE user_id = ?`). Recommend enabling Parquet bloom filters via:
- Spark: `ALTER TABLE SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.user_id' = 'true')`

**Where**: Extend `LayoutRecommendation` with `bloom_filter_columns` field (already added to DB schema).

**Status**: Schema added; recommendation logic implemented; UI exposure needed

---

### 8. **Maintenance Job Detail Page**
**Priority**: P1  
**Impact**: Debugging; when a job fails, users need to see why  
**Effort**: 2 days  
**Competitive Gap**: All competitors have this; table stakes

**What**: Drill-down page from Audit Log showing:
- Full SQL executed
- Engine used (Trino/Spark/Native)
- Start/end timestamps, duration
- Input/output file counts, effectiveness ratio
- Error message (if failed)
- Partition predicates used
- Link back to table detail page

**UI Components**:
- Header with job status badge (success/failed/running)
- SQL code block (syntax highlighted)
- Metrics cards (duration, files, ratio)
- Error alert box (if failed)

---

---

## Tier 2 — High Impact (3–6 months)

### 9. **Flink Engine Integration**
**Priority**: P1  
**Impact**: Opens streaming-team adoption path; differentiates from Spark-only platforms  
**Effort**: 2 weeks  
**Competitive Gap**: LakeOps ✅, Amoro ✅, Cryo ❌

**What**: A new `EngineType.FLINK` that generates Flink `TableMaintenance` job configurations (YAML / Java API config) rather than SQL. Cryo renders the config; the Flink job executes it.

**Implementation**:
- `cryo/engines/flink/config.py` — generate Flink Java API config:
  ```java
  TableMaintenanceAction.forTable("catalog.schema.orders")
    .executeActions(
      TableMaintenanceAction.Type.COMPACT_DATA_FILES,
      TableMaintenanceAction.Type.EXPIRE_SNAPSHOTS
    )
    .scheduleOnDataFileCount(500)
    .scheduleOnDataFileSize("10GB")
    .build();
  ```
- Alternative: generate Flink SQL for Iceberg procedures (Flink 1.20+):
  ```sql
  CALL sys.compact_data_files('catalog.schema.orders', 
    map('target-file-size-bytes', '536870912'));
  ```

**Target Users**: Teams running CDC or streaming ingestion via Flink who want Cryo to manage their maintenance policy.

**Status**: Basic structure exists; full integration needed

---

### 10. **Multi-Catalog Federation**
**Priority**: P1  
**Impact**: Manage tables across Hive, Glue, Polaris, Nessie from one Cryo instance  
**Effort**: 1 week  
**Competitive Gap**: LakeOps ✅, Amoro ✅, Cryo ❌

**What**: Cryo currently assumes one Iceberg REST catalog per connection. Extend to support:
- Multiple catalogs per connection (e.g., Polaris routing to Hive/Glue/REST backends)
- Cross-catalog visibility in UI (single table list showing tables from all federated catalogs)
- Per-catalog maintenance policies

**Implementation**:
- Extend `Connection` model with `federated_catalogs: List[CatalogConfig]`
- Scanner discovers tables across all catalogs
- UI groups tables by catalog in dropdown filter

**Use Case**: A team with legacy Hive tables + new Polaris tables wants Cryo to maintain both.

---

### 11. **Partition Evolution Recommendations**
**Priority**: P2  
**Impact**: Long-term performance; wrong partitioning is expensive to fix later  
**Effort**: 1 week  
**Competitive Gap**: LakeOps ❌, Databricks ❌ (unique feature)

**What**: Analyze tables where a different partition spec would significantly improve pruning:
- Table partitioned by `YEAR(created_at)` but always queried by day → recommend `DAY(created_at)`
- Table partitioned by `region` but queries always filter by `customer_id` → recommend hybrid partition or repartition

**Where**: `cryo/optimizer/partition_analyzer.py` (new module)

**Algorithm**:
1. Parse `QueryPattern` for filters on partition vs non-partition columns
2. If 90%+ of queries filter on a non-partitioned column → recommend repartition
3. If partition granularity is too coarse (e.g., YEAR but queries use day ranges) → recommend finer granularity

**UI**: Add "Partition Recommendations" tab to Layout Optimization page

---

### 12. **REST Catalog Credential Vending (Polaris Integration)**
**Priority**: P2  
**Impact**: Secure, short-lived credentials; required for Polaris deployments  
**Effort**: 1 week  
**Competitive Gap**: LakeOps ✅, Databricks ✅, Cryo ❌

**What**: When connecting to a Polaris (or Unity Catalog) REST catalog, use credential vending:
- Cryo authenticates to catalog with service credential (OAuth2)
- Catalog vends short-lived, table-scoped STS tokens for S3/ADLS/GCS
- Cryo uses vended credentials for metadata + data file access
- No long-term AWS keys stored in Cryo

**Implementation**:
- Extend `IcebergSource` to call REST catalog's `/v1/{prefix}/namespaces/{namespace}/tables/{table}` with OAuth2 bearer token
- Parse `config` response for `s3.access-key-id`, `s3.secret-access-key`, `s3.session-token`
- Use vended credentials for PyIceberg catalog client

**Config**: Add `credential_vending: bool` to `Connection` model (default: false for Hive/Glue, true for Polaris)

**Reference**: [Apache Polaris 1.0 credential vending docs](https://polaris.apache.org/releases/1.0.0/)

---

### 13. **Cost Attribution & Showback Dashboard**
**Priority**: P2  
**Impact**: FinOps; critical for teams with chargeback/showback model  
**Effort**: 2 weeks  
**Competitive Gap**: LakeOps 🔨, Databricks ✅, Snowflake ✅

**What**: Apportion data lake costs by team/department/project:
1. **Storage cost per table**: `table_size_gb × $0.023/GB/month` (S3 Standard)
2. **Compute cost per table**: Join `QueryLog` with engine cost (Trino cluster cost / total query hours)
3. **Maintenance cost per table**: Track `AuditLog` duration × engine hourly cost

**Attribution Tags**: Support custom tags in `Connection` metadata:
- `cost_center: "finance"`
- `department: "data-eng"`
- `project: "ml-platform"`

**UI Page**: "Cost Attribution" showing:
- Monthly cost breakdown by team/department
- Top 10 most expensive tables (storage + compute + maintenance)
- Cost trend over time
- Export to CSV for finance team

**Implementation**:
- Add `custom_tags: JSONB` to `Connection` model
- Add `estimated_cost_usd` to `TableStats` (computed daily)
- New API endpoint: `GET /api/cost-attribution?groupBy=cost_center&month=2026-07`

**Showback Report**: Auto-generate monthly PDF/Excel report emailed to finance stakeholders

**Research**: [Snowflake QUERY_ATTRIBUTION_HISTORY](https://docs.snowflake.com/en/user-guide/cost-attributing), [Databricks system.billing.usage](https://biroadrian.com/posts/finops/case-study/how-build-a-modern-finops-chargeback-model-for-databricks-serverless-and-ai-workloads/)

---

### 14. **S3 Intelligent-Tiering Recommendations**
**Priority**: P2  
**Impact**: 40–68% storage cost reduction for cold data  
**Effort**: 3 days  
**Competitive Gap**: LakeOps ❌, AWS S3 Tables ✅

**What**: Analyze table access patterns and recommend S3 Intelligent-Tiering:
- Tables not queried in 90 days → enable Intelligent-Tiering (auto-moves to Infrequent Access tier after 30 days, Archive Instant Access after 90 days)
- Filter out tables with average file size < 128 KB (Intelligent-Tiering minimum)
- Estimate savings: `table_size_gb × (current_rate - intelligent_tiering_rate) × 12 months`

**UI**: Add "Storage Tiering Recommendations" card to Table Detail page:
- "Enable S3 Intelligent-Tiering to save ~$X,XXX/year"
- One-click apply (updates S3 bucket lifecycle policy via AWS API)

**Prerequisites**: Requires AWS credentials with `s3:PutLifecycleConfiguration` permission

**Research**: [AWS S3 Intelligent-Tiering pricing](https://cloudburn.io/blog/amazon-s3-pricing)

---

### 15. **Multi-Engine Telemetry Breadth**
**Priority**: P2  
**Impact**: Match LakeOps's multi-engine claim; expand beyond Trino  
**Effort**: 2 weeks  
**Competitive Gap**: LakeOps ✅, Cryo 🔨 (Trino-only)

**What**: Extend `TrinoQueryIngester` to also ingest from:
1. **Spark History Server** (JSON API): `GET /api/v1/applications/{appId}/sql`
2. **Athena** (AWS API): `boto3.client('athena').list_query_executions()` + `get_query_execution()`
3. **DuckDB** (local file): Parse `~/.duckdb/history` or custom query log

**Where**: `cryo/telemetry/ingestion.py`

**Schema**: Normalize to common `QueryLog` schema:
- `engine: str` (trino, spark, athena, duckdb)
- `query_id: str`
- `query_text: str`
- `user: str`
- `duration_ms: int`
- `bytes_scanned: int`
- `tables_accessed: List[str]`

**UI**: Filter queries by engine in Query Performance Dashboard

---

### 16. **SLA Monitoring & Alerting**
**Priority**: P2  
**Impact**: Production reliability; enables proactive alerts before users complain  
**Effort**: 1 week  
**Competitive Gap**: All enterprise platforms have this

**What**: Track SLAs per table and alert when breached:
- **Freshness SLA**: Table data must be ≤ X hours old (track `last_updated_at` from `TableStats`)
- **Maintenance SLA**: Compaction must run every Y days (track `last_optimized_at`)
- **Query Performance SLA**: P95 latency must be ≤ Z seconds (from `QueryLog`)

**Config**: Per-table SLA definitions in `Connection` metadata:
```json
{
  "sla": {
    "freshness_hours": 24,
    "maintenance_days": 7,
    "p95_latency_ms": 5000
  }
}
```

**Alerts**: Integrate with PagerDuty/Slack:
- When SLA breached → POST to webhook URL
- Escalation: WARN (1.5× threshold) → ALERT (2× threshold)

**UI**: "SLA Status" tab on Table Detail page showing:
- Current vs target SLA
- Time until next breach
- Historical SLA compliance %

**Research**: [RisingWave SLA monitoring with streaming SQL](https://risingwave.com/blog/real-time-sla-monitoring-dashboard-streaming-sql/)

---

### 17. **Data Quality Profiling (Zero-Scan)**
**Priority**: P2  
**Impact**: Catch data issues before users see bad data  
**Effort**: 2 weeks  
**Competitive Gap**: Databricks ✅ (Lakehouse Monitoring), LakeOps ❌

**What**: Leverage Iceberg's manifest-level statistics (min/max bounds, null counts, NaN counts) for zero-scan data quality checks:
- **Null rate spike**: Alert when `null_count / value_count` exceeds historical baseline
- **Bound violations**: Alert when `min_value` or `max_value` breaks expected range (e.g., negative revenue)
- **Schema drift**: Detect new columns or type changes in manifests

**Where**: `cryo/quality/` (new module)

**Implementation**:
1. Query `$manifests` metadata table (no data file reads)
2. Aggregate column stats per partition
3. Compare to historical baseline (30-day rolling average)
4. Generate alerts when anomalies detected

**UI**: "Data Quality" tab on Table Detail page showing:
- Column null rate trends
- Value range drift charts
- Schema change log

**Research**: [Zero-Scan Data Quality (SIGMOD 2026)](https://arxiv.org/html/2605.30308v1)

---

### 18. **Approval Workflows for Destructive Operations**
**Priority**: P2  
**Impact**: Production safety; prevents accidental data loss  
**Effort**: 1 week  
**Competitive Gap**: Enterprise feature; differentiates from open-source tools

**What**: Require manual approval before executing:
- `EXPIRE_SNAPSHOTS` (data loss risk)
- `REMOVE_ORPHAN_FILES` (irreversible)
- `DROP TABLE` operations (if/when added)

**Workflow**:
1. User clicks "Expire Snapshots" in UI
2. Cryo generates preview: "Will delete X snapshots, freeing Y GB"
3. Request sent to approval queue (stored in DB)
4. Approver (role: `admin` or `table_owner`) reviews and approves/rejects
5. Upon approval → job executed and audited

**Config**: `require_approval: bool` per operation type in settings

**UI**: "Pending Approvals" page showing:
- Operation type, table name, requester, estimated impact
- Approve/Reject buttons
- Audit trail of past approvals

**Integration**: Optional webhook to Slack for approval notifications

**Research**: [Bytebase custom approval flows](https://docs.bytebase.com/change-database/approval)

---

---

## Tier 3 — Strategic Differentiators (6–12 months)

### 19. **Native Rust Compaction Engine (Production-Ready)**
**Priority**: P1  
**Impact**: 20× speed, 98% cheaper than Spark (LakeOps claim); this is THE performance differentiator  
**Effort**: 2 months  
**Competitive Gap**: LakeOps ✅, Cryo 🔨 (MVP skeleton exists)

**What**: Finish `cryo-native` Rust engine:
- DataFusion + Arrow for in-memory compaction
- PyO3 bindings for Python integration
- Bin-pack, sort, and z-order strategies
- S3 multipart upload for large files
- PyIceberg integration for metadata commits ✅ (already added)

**Benchmarks**: Target 50 GB table compaction in <5 minutes (vs 30+ minutes in Spark)

**Scope**:
- Complete error handling, retries, observability
- Add distributed mode (multiple workers)
- Production deployment guide (compile, deploy, monitor)

**Status**: Basic compaction logic exists; needs production hardening

---

### 20. **Iceberg v3 Features**
**Priority**: P1  
**Impact**: Future-proof; v3 adoption is accelerating in 2026  
**Effort**: 2 weeks  
**Competitive Gap**: Databricks ✅, AWS ✅, Cryo ❌

**What**: Detect and support Iceberg v3 features:
1. **Deletion Vectors**: Detect when table uses deletion vectors; skip `eq-delete → pos-delete` optimization (deletion vectors are more efficient)
2. **Row Lineage**: Track `_row_id` and `_last_updated_sequence_number` for CDC/audit use cases
3. **Single-File Commits**: Recognize streaming tables with single-file commits; adjust maintenance frequency

**Implementation**:
- Read table metadata `format-version` field
- If v3 → adjust planner strategy
- Add "Iceberg v3 Features" badge to Table Detail page

**Research**: [Iceberg v3 spec](https://iceberg.apache.org/spec/), [AWS v3 blog](https://aws.amazon.com/blogs/big-data/accelerate-data-lake-operations-with-apache-iceberg-v3-deletion-vectors-and-row-lineage/)

---

### 21. **Cross-Engine ABAC (Attribute-Based Access Control)**
**Priority**: P2  
**Impact**: Fine-grained security; required for regulated industries (finance, healthcare)  
**Effort**: 3 weeks  
**Competitive Gap**: Databricks ✅ (Unity Catalog), Snowflake ✅, LakeOps ❌

**What**: Enforce row-level and column-level security via Iceberg REST Catalog Scan API:
1. User queries table via Trino/Spark
2. Cryo (acting as REST catalog) intercepts scan request
3. Cryo evaluates policies (e.g., "analysts can only see rows where `region = 'US'`")
4. Cryo returns filtered scan plan to engine
5. Engine processes only authorized data

**Policies**: Define in Cryo UI or YAML:
```yaml
- table: catalog.finance.transactions
  row_filter:
    role: analyst
    condition: "region = current_user_region()"
  column_mask:
    role: analyst
    column: ssn
    mask: "REGEXP_REPLACE(ssn, '\\d{5}$', 'XXXXX')"
```

**Prerequisites**: Cryo must implement Iceberg REST Catalog spec (not just PyIceberg client)

**Research**: [Databricks cross-engine ABAC](https://www.databricks.com/blog/introducing-cross-engine-abac)

---

### 22. **Data Lineage Visualization**
**Priority**: P2  
**Impact**: Root-cause analysis; regulatory compliance (GDPR, CCPA)  
**Effort**: 3 weeks  
**Competitive Gap**: Databricks ✅, Snowflake ✅, Atlan ✅, LakeOps ❌

**What**: Track and visualize data lineage:
- **Table-level lineage**: Which tables feed into which (via Spark/Trino query logs)
- **Column-level lineage**: Which source columns map to which target columns (via OpenLineage)
- **Job-level lineage**: Which Spark/Airflow jobs touch which tables

**Implementation**:
1. Integrate OpenLineage listener (for Spark/Flink jobs)
2. Parse Trino query logs for `INSERT INTO ... SELECT FROM` statements
3. Build dependency graph in Postgres (`table_lineage` table)
4. Render as interactive graph in UI (D3.js or Cytoscape.js)

**UI**: "Lineage" tab on Table Detail page showing:
- Upstream tables (sources)
- Downstream tables (consumers)
- Jobs that read/write this table
- Click node → navigate to that table's detail page

**Use Case**: "This table's data looks wrong — which upstream table is the root cause?"

**Research**: [Iceberg data lineage](https://iceberglakehouse.com/iceberg/iceberg-data-lineage/), [OpenLineage integration](https://docs.factorhouse.io/playground/data-lineage-labs/end-to-end-lineage)

---

### 23. **AI-Powered Anomaly Detection**
**Priority**: P2  
**Impact**: Proactive issue detection; reduces manual monitoring  
**Effort**: 2 weeks  
**Competitive Gap**: Databricks ✅ (Genie ZeroOps), LakeOps ❌

**What**: Use ML to detect anomalies in table health:
- Sudden spikes in small file count (expected: gradual increase)
- Unexpected storage growth (10× overnight → investigate)
- Query latency anomalies (table suddenly 5× slower → data skew?)

**Implementation**:
1. Train ARIMA/Prophet model on historical `TableStatsHistory`
2. Predict expected values for next 24h
3. Alert when actual deviates >3σ from prediction

**Where**: `cryo/ml/` (new module) using `prophet` or `statsmodels`

**UI**: "Anomalies Detected" alert banner on dashboard; drill-down to affected tables

**Research**: [Databricks Genie ZeroOps](https://www.databricks.com/blog/introducing-genie-zeroops)

---

### 24. **Query Workload Replay & Testing**
**Priority**: P3  
**Impact**: Validate maintenance impact before production rollout  
**Effort**: 2 weeks  
**Competitive Gap**: Unique feature

**What**: Capture production query workload and replay it against a test table:
1. User clicks "Test Compaction Impact" in UI
2. Cryo creates shadow table (snapshot of prod table)
3. Runs compaction on shadow table
4. Replays last 1000 queries from `QueryLog` against shadow table
5. Compares latency: before vs after compaction
6. Shows results: "Compaction will reduce P95 latency by 32%"

**Use Case**: "Should I enable z-order? Let me test it first."

**Implementation**: Requires connection to query engine (Trino JDBC) to re-run queries

---

### 25. **GitOps Integration (Maintenance-as-Code)**
**Priority**: P3  
**Impact**: Infrastructure-as-Code adoption; appeals to DevOps teams  
**Effort**: 1 week  
**Competitive Gap**: Unique feature

**What**: Define maintenance policies in Git:
```yaml
# cryo-policies/finance/transactions.yaml
table: catalog.finance.transactions
maintenance:
  optimize:
    enabled: true
    cooldown_days: 3
    strategy: zorder
    zorder_columns: [customer_id, transaction_date]
  expire_snapshots:
    enabled: true
    retention_days: 30
  compute_statistics:
    enabled: true
    columns: [customer_id, transaction_date, amount_usd]
alerts:
  slack_webhook: https://hooks.slack.com/...
  pagerduty_key: xxx
```

**Workflow**:
1. User commits policy YAML to Git repo
2. Cryo watches Git repo (webhook or polling)
3. On change → Cryo imports policy into DB
4. All changes audited via Git history

**Benefits**: Policy versioning, PR-based approvals, rollback capability

---

### 26. **Multi-Tenant Isolation**
**Priority**: P3  
**Impact**: Required for SaaS offering; enables Cryo-as-a-Service  
**Effort**: 2 weeks  
**Competitive Gap**: Enterprise feature

**What**: Isolate tenants so Team A cannot see Team B's tables/logs/dashboards:
- Row-level security on all DB tables (filter by `tenant_id`)
- UI shows only current tenant's resources
- API enforces tenant isolation via JWT claims

**Schema Changes**:
- Add `tenant_id: UUID` to all core tables (`TableStats`, `AuditLog`, `QueryLog`, etc.)
- Add `Tenant` model with `name`, `admin_user_id`, `created_at`

**Auth**: Integrate with OIDC provider (Okta, Auth0); extract tenant ID from JWT

---

---

## Tier 4 — Enterprise & Monetization (12+ months)

### 27. **Embedded Analytics SDK**
**Priority**: P3  
**Impact**: Enables embedding Cryo dashboards into customer apps  
**Effort**: 2 weeks

**What**: JavaScript SDK for embedding Cryo charts/tables into external apps:
```javascript
<script src="https://cryo.yourcompany.com/embed.js"></script>
<div id="cryo-table-health" data-table-id="12345"></div>
<script>
  Cryo.embed({
    containerId: 'cryo-table-health',
    apiKey: 'ck_live_xxx',
    widget: 'table-health-card'
  });
</script>
```

**Widgets**:
- Table health card (grade, metrics)
- Maintenance job status
- Cost trend chart

**Auth**: API key authentication; scoped to specific tables

---

### 28. **FinOps Predictive Budgets**
**Priority**: P3  
**Impact**: CFO-friendly; enables proactive cost control  
**Effort**: 1 week

**What**: Predict next month's data lake costs based on growth trends:
- Storage forecast: `current_size × growth_rate × 30 days`
- Compute forecast: `avg_query_cost × projected_query_count`
- Maintenance forecast: `avg_optimize_cost × tables_needing_optimize`

**Alerts**: "You are projected to exceed budget by 15% next month"

**UI**: "Budget Forecast" page with interactive sliders (adjust growth assumptions)

---

### 29. **Slack Bot Integration**
**Priority**: P3  
**Impact**: ChatOps; reduces context switching  
**Effort**: 1 week

**What**: `/cryo` Slack commands:
- `/cryo status table finance.transactions` → shows health grade, last maintenance
- `/cryo optimize table finance.transactions` → triggers compaction
- `/cryo alerts enable table finance.transactions` → subscribe to alerts

**Implementation**: Slack app + webhook endpoint in Cryo API

---

### 30. **Custom Maintenance Scripts**
**Priority**: P3  
**Impact**: Power users; enables custom logic  
**Effort**: 2 weeks

**What**: Allow users to upload Python scripts that run as maintenance operations:
```python
# custom_optimize.py
from cryo.sdk import Table

def run(table: Table):
    # Custom logic here
    if table.size_gb > 1000:
        table.optimize(strategy='zorder', columns=['col1', 'col2'])
    else:
        table.optimize(strategy='binpack')
```

**Safety**: Run in sandboxed environment (Docker container) with resource limits

---

### 31. **Metadata Export API**
**Priority**: P3  
**Impact**: Integration with external tools (Atlan, Alation, etc.)  
**Effort**: 1 week

**What**: Export Cryo's metadata to data catalog tools:
- REST API: `GET /api/export/atlan` → Atlan-compatible JSON
- Webhook: Push table metadata to external URL on changes

**Use Case**: Sync Cryo's table health scores into Atlan data catalog

---

### 32. **Table Cloning & Sandboxing**
**Priority**: P3  
**Impact**: Enables safe experimentation  
**Effort**: 1 week

**What**: One-click clone production table to sandbox:
- Uses Iceberg zero-copy cloning (metadata-only)
- User experiments with z-order, partition changes, etc. on sandbox
- If satisfied → apply same changes to production

**UI**: "Clone to Sandbox" button on Table Detail page

---

### 33. **Compliance Reporting (SOC 2, GDPR)**
**Priority**: P3  
**Impact**: Required for enterprise sales  
**Effort**: 2 weeks

**What**: Generate compliance reports:
- **SOC 2**: Audit log of all changes (who, what, when)
- **GDPR**: Data lineage showing PII flow + deletion confirmations

**Export**: PDF report with digital signature

---

### 34. **Query Cost Estimation**
**Priority**: P3  
**Impact**: FinOps; helps users write efficient queries  
**Effort**: 1 week

**What**: Before executing a query, estimate cost:
- Parse query → identify tables/partitions scanned
- Estimate bytes scanned → multiply by engine cost (Trino: ~$5/TB)
- Show: "This query will cost ~$2.50 to run. Proceed?"

**UI**: Query editor with live cost estimation (like Snowflake)

---

### 35. **Table Archival to Glacier**
**Priority**: P3  
**Impact**: Long-term cold storage; 96% cost reduction  
**Effort**: 1 week

**What**: Move rarely-accessed tables to S3 Glacier Deep Archive:
- Detect tables not queried in 365 days
- Recommend archival: "Save $X,XXX/year by archiving to Glacier"
- One-click archive (moves data files to Glacier, updates metadata)

**Note**: Query performance degrades (12–48h restore time)

---

### 36. **Real-Time Dashboard**
**Priority**: P3  
**Impact**: Live monitoring; appeals to ops teams  
**Effort**: 2 weeks

**What**: WebSocket-powered real-time dashboard:
- Live query latency chart (updates every second)
- Live compaction job progress bar
- Live table size growth

**Tech**: WebSocket API + React UI updates

---

### 37. **Custom Health Score Model**
**Priority**: P3  
**Impact**: Enterprise customization  
**Effort**: 1 week

**What**: Allow users to define custom health scoring:
```yaml
health_score:
  weights:
    small_file_count: 40%  # default: 30%
    snapshot_count: 20%    # default: 20%
    orphan_file_ratio: 10% # default: 10%
    custom_metric: 30%     # new!
  custom_metric:
    formula: "query_p95_latency_ms / 1000"
    warn_threshold: 2
    critical_threshold: 5
```

**UI**: "Custom Health Model" settings page

---

### 38. **Multi-Region Deployment**
**Priority**: P4  
**Impact**: Global enterprises; data residency requirements  
**Effort**: 1 month

**What**: Deploy Cryo in multiple AWS regions:
- Shared control plane (metadata DB)
- Regional workers (Spark/Trino clusters)
- Region-aware job routing (US tables → US workers)

**Challenge**: Cross-region latency for metadata access

---

### 39. **Table Dependency Graph**
**Priority**: P4  
**Impact**: Impact analysis; "which downstream tables break if I change this schema?"  
**Effort**: 2 weeks

**What**: Visualize table dependencies:
- Upstream (sources) and downstream (consumers) tables
- Transitive dependencies (3+ levels deep)
- Impact score: "Changing this table affects 47 downstream tables"

**Use Case**: Before breaking schema change, see impact

---

### 40. **Automated Rollback**
**Priority**: P4  
**Impact**: Production safety; reduces MTTR  
**Effort**: 1 week

**What**: If compaction causes query latency spike → auto-rollback:
1. Monitor P95 latency for 1h after compaction
2. If latency increases >50% → trigger rollback
3. Rollback = Iceberg time-travel to pre-compaction snapshot

**Config**: `auto_rollback_on_latency_spike: bool` (default: false)

---

---

## UI/UX Pages Required

### Core Pages (Tier 1)
1. **Dashboard** (homepage)
   - Fleet health summary (A/B/C/D/F counts)
   - Top 10 unhealthy tables
   - Recent maintenance jobs (last 24h)
   - Cost trend chart (last 30 days)

2. **Tables List**
   - Searchable, filterable table (by catalog, schema, grade, size)
   - Columns: name, grade, size, small files, last optimized, actions
   - Bulk actions (optimize selected, export CSV)

3. **Table Detail**
   - Tabs: Overview | Partitions | Maintenance History | Query Performance | Layout Recommendations | Lineage
   - Overview: health card, metrics, cost, last scan
   - Partitions: list with benefit scores, small file counts
   - Maintenance History: audit log filtered to this table
   - Query Performance: queries that touched this table
   - Layout Recommendations: z-order, bloom filters, partitioning
   - Lineage: upstream/downstream graph

4. **Connections** ✅ (already exists)
   - List of Iceberg catalog connections
   - Add/edit/delete connections

5. **Audit Log** ✅ (already exists)
   - Full history of all maintenance jobs
   - Filters: by table, by operation type, by status
   - Drill-down to job detail page

6. **Settings** ✅ (already exists)
   - Maintenance policies (global defaults)
   - Scheduler config (off-peak windows)
   - AI provider settings
   - Slack/PagerDuty webhooks

### Additional Pages (Tier 2+)
7. **Query Performance Dashboard** (Tier 1)
8. **Table Health Trends** (Tier 1)
9. **Maintenance Job Detail** (Tier 1)
10. **Layout Optimization** ✅ (already exists, needs expansion)
11. **Cost Attribution** (Tier 2)
12. **SLA Status** (Tier 2)
13. **Data Quality** (Tier 2)
14. **Pending Approvals** (Tier 2)
15. **Lineage Visualization** (Tier 3)
16. **Budget Forecast** (Tier 4)
17. **Real-Time Dashboard** (Tier 4)

---

## Implementation Notes

### Quick Wins (1 week each)
- Query Performance Dashboard (use existing `QueryLog` table)
- Table Health Trends (use existing `TableStatsHistory`)
- Maintenance Job Detail page (drill-down from Audit Log)
- Bloom Filter Recommendations UI (schema already exists)
- S3 Intelligent-Tiering Recommendations (pure analysis)

### Medium Effort (2–3 weeks each)
- Flink Engine Integration (new engine type)
- Multi-Catalog Federation (extend `Connection` model)
- Cost Attribution Dashboard (new aggregations)
- SLA Monitoring (new alerting subsystem)
- Data Quality Profiling (new `quality/` module)

### Long-Term Investments (1–2 months each)
- Native Rust Compaction Engine (production hardening)
- Cross-Engine ABAC (REST catalog implementation)
- Data Lineage Visualization (OpenLineage integration)
- Multi-Tenant Isolation (schema refactoring)

---

## Competitive Positioning

| Feature Category | LakeOps | Databricks | Cryo (Roadmap) |
|------------------|---------|-----------|----------------|
| **Core Maintenance** | ✅ | ✅ | ✅ |
| Query Telemetry | ✅ | ✅ | ✅ |
| Layout Optimization | ✅ | ✅ | ✅ |
| Native Rust Engine | ✅ | ❌ | 🔨 (MVP) |
| **Advanced** | | | |
| Puffin Statistics | ✅ | ✅ | ✅ (needs planner integration) |
| AutoComp Benefit Scoring | ✅ | ✅ | ✅ (needs governor integration) |
| Signal-Driven Triggers | ✅ | ✅ | ✅ |
| Flink Engine | ✅ | ❌ | 🔨 (skeleton) |
| **Unique to Cryo** | | | |
| Bloom Filter Recommendations | ❌ | ❌ | ✅ |
| Effectiveness Ratio | ❌ | ❌ | ✅ |
| Partition Evolution Recs | ❌ | ❌ | 📋 (roadmap) |
| Query Workload Replay | ❌ | ❌ | 📋 (roadmap) |
| GitOps Integration | ❌ | ❌ | 📋 (roadmap) |
| **Enterprise** | | | |
| Multi-Catalog Federation | ✅ | ✅ | 📋 (roadmap) |
| Credential Vending | ✅ | ✅ | 📋 (roadmap) |
| Cross-Engine ABAC | ❌ | ✅ | 📋 (roadmap) |
| Data Lineage | ❌ | ✅ | 📋 (roadmap) |
| Cost Attribution | 🔨 | ✅ | 📋 (roadmap) |
| Compliance Reporting | ❌ | ✅ | 📋 (roadmap) |
| **Deployment** | | | |
| Open Source | ❌ | ❌ | ✅ |
| Self-Hosted | ❌ | ❌ | ✅ |
| SaaS Option | ✅ | ✅ | 📋 (future) |

**Legend**: ✅ Implemented | 🔨 In Progress | 📋 Roadmap | ❌ Not Available

---

## Key Takeaways

### Tier 1 (Next 3 Months) — Must-Haves
1. **Puffin Statistics** — closes highest-leverage gap vs competitors
2. **AutoComp Benefit Scoring** — stops wasting compute on cold tables
3. **Signal-Driven Triggers** — "autonomous control plane" positioning
4. **Query Performance Dashboard** — table stakes observability
5. **Table Health Trends** — proactive monitoring
6. **Bloom Filter Recommendations** — unique differentiator

### Tier 2 (3–6 Months) — Competitive Parity
7. **Flink Engine** — opens streaming-team adoption
8. **Multi-Catalog Federation** — enterprise requirement
9. **Cost Attribution** — FinOps critical
10. **SLA Monitoring** — production reliability
11. **Data Quality** — catch issues early
12. **Approval Workflows** — production safety

### Tier 3 (6–12 Months) — Strategic Moats
13. **Native Rust Engine** — THE performance differentiator
14. **Iceberg v3 Support** — future-proof
15. **Cross-Engine ABAC** — fine-grained security
16. **Data Lineage** — root-cause analysis
17. **AI Anomaly Detection** — proactive issue detection

### Tier 4 (12+ Months) — Enterprise & SaaS
18. **Multi-Tenant Isolation** — SaaS offering
19. **Compliance Reporting** — enterprise sales
20. **GitOps Integration** — DevOps appeal

---

## Prioritization Framework

**Impact** = (Users Helped × Revenue Potential × Competitive Advantage)  
**Effort** = (Dev Weeks × Complexity × Risk)  
**ROI** = Impact / Effort

**Top 10 by ROI**:
1. Puffin Statistics (10/1 = 10.0)
2. Query Performance Dashboard (9/1 = 9.0)
3. AutoComp Benefit Scoring (9/1 = 9.0)
4. Table Health Trends (8/0.5 = 16.0) ⬅ **Highest ROI**
5. Bloom Filter Recommendations (7/0.5 = 14.0)
6. Signal-Driven Triggers (9/2 = 4.5)
7. S3 Intelligent-Tiering Recs (7/0.5 = 14.0)
8. Cost Attribution (8/2 = 4.0)
9. Flink Engine (7/2 = 3.5)
10. SLA Monitoring (7/1 = 7.0)

---

## Recommended Execution Order

**Weeks 1–4** (Quick Wins):
- Table Health Trends page
- Query Performance Dashboard
- Bloom Filter Recommendations UI
- S3 Intelligent-Tiering Recommendations

**Weeks 5–8** (High-Impact Core):
- Puffin Statistics integration
- AutoComp Benefit Scoring integration
- Maintenance Job Detail page
- SLA Monitoring

**Weeks 9–12** (Competitive Gaps):
- Signal-Driven Triggers (already implemented, polish UI)
- Cost Attribution Dashboard
- Data Quality Profiling

**Months 4–6** (Strategic):
- Flink Engine Integration
- Multi-Catalog Federation
- Approval Workflows

**Months 7–12** (Moat):
- Native Rust Engine (production hardening)
- Iceberg v3 Support
- Cross-Engine ABAC
- Data Lineage Visualization

---

## Success Metrics

**Adoption Metrics**:
- Tables managed by Cryo
- Active users (weekly)
- Maintenance jobs executed (monthly)

**Efficiency Metrics**:
- Avg compaction effectiveness ratio (target: 50:1)
- % tables with health grade A/B (target: 80%+)
- Query latency improvement (P95 before/after maintenance)

**Cost Metrics**:
- Total S3 cost saved (via orphan cleanup, intelligent tiering)
- Compute cost saved (via AutoComp gating)
- % tables under budget

**Competitive Metrics**:
- Feature parity with LakeOps (target: 90%+ by Q4 2026)
- Unique features shipped (target: 5+)
- Customer testimonials mentioning differentiators

---

**END OF DOCUMENT**

> **Note to maintainers**: Update this document quarterly as new research papers, competitor features, and user feedback emerge. Track implementation status in `todo.md` and link completed features back to this roadmap.
