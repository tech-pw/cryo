# Cryo 6-Month Premortem Analysis — July 2026

## Executive Summary

**Scenario**: It's January 2027. Cryo has been deployed to production for 6 months and has experienced multiple critical failures, data loss incidents, and customer churn.

This premortem identifies **87 critical issues** across all 30 features that led to these failures, organized by severity and impact.

---

## 🔴 CRITICAL ISSUES (Production-Breaking)

### Database & Data Integrity

**Issue #1: Missing Database Migrations**
- **Feature**: Lineage tracking, multi-tenant isolation
- **Problem**: `LineageEdge` and `ABACPolicy` tables don't exist - no migrations created
- **Impact**: API crashes on startup when trying to import models
- **Evidence**: Only 1 migration created (`20260703_0009_approval_workflows.py`), but 10+ new models added
- **Fix Required**: Create migrations for ALL new tables

**Issue #2: No Foreign Key Constraints**
- **Feature**: All new features
- **Problem**: `LineageEdge.source_table_id`, `ABACPolicy.table_id` have no FK constraints
- **Impact**: Orphaned records, data corruption, cascading delete failures
- **Evidence**: Code comments say "Would add FK in production" but never added
- **Fix Required**: Add ALL foreign key constraints before production

**Issue #3: No Database Indexes**
- **Feature**: Query performance, SLA monitoring, anomaly detection
- **Problem**: Queries on `QueryLog.tables_accessed` (JSONB), `TableStatsHistory.scan_at` have no indexes
- **Impact**: Dashboard loads taking 30+ seconds with 1M+ records
- **Evidence**: No index creation in migrations
- **Fix Required**: Add indexes on all filter/sort columns

**Issue #4: JSONB Query Performance**
- **Feature**: Query performance dashboard, lineage tracking
- **Problem**: `jsonb_exists_any()` queries scan entire table
- **Impact**: API timeouts on tables endpoint after 100K queries
- **Evidence**: Line 13 in dashboard_routes.py uses unindexed JSONB search
- **Fix Required**: Create GIN indexes on JSONB columns

**Issue #5: No Data Validation in DB Models**
- **Feature**: Approval workflows, ABAC, lineage
- **Problem**: `impact_preview`, `column_masks`, `metadata` JSONBs accept any structure
- **Impact**: Type errors, crashes when accessing malformed data
- **Evidence**: No Pydantic validation on JSONB fields
- **Fix Required**: Add JSON schema validation or Pydantic models

---

### Integration & Orchestration

**Issue #6: Dashboard Endpoints Not Integrated with Core System**
- **Feature**: All 8 new dashboard endpoints
- **Problem**: Endpoints exist but never called by governor/scheduler/worker
- **Impact**: Features exist but don't DO anything - pure dead code
- **Evidence**: No imports of `dashboard_routes` in scheduler/governor/worker
- **Fix Required**: Wire endpoints into execution pipeline

**Issue #7: AutoComp Benefit Score Never Calculated**
- **Feature**: AutoComp benefit scoring (#2)
- **Problem**: `benefit_score` column exists but no code populates it
- **Impact**: Planner still uses dumb small_file_count ranking, wastes compute
- **Evidence**: Code says "integration pending" - still pending
- **Fix Required**: Add benefit score computation to scanner, integrate with planner

**Issue #8: Signal-Driven Triggers Never Fire**
- **Feature**: Signal-driven maintenance triggers (#4)
- **Problem**: `triggers/evaluator.py` module exists but never called
- **Impact**: Reactive maintenance never happens, tables degrade until scheduled maintenance
- **Evidence**: No background task/cron job to evaluate triggers
- **Fix Required**: Add trigger evaluation loop to governor or scheduler service

**Issue #9: Approval Workflows Block Execution Forever**
- **Feature**: Approval workflows (#18)
- **Problem**: Governor checks if approval required but never actually enforces it
- **Impact**: Destructive ops execute without approval, defeating entire purpose
- **Evidence**: No integration between `approvals.py` and `governor/governor.py`
- **Fix Required**: Add approval gating to maintenance execution path

**Issue #10: Puffin Statistics Never Computed**
- **Feature**: Puffin statistics management (#1)
- **Problem**: Config exists, SQL rendering exists, but no execution path
- **Impact**: Query performance never improves after compaction
- **Evidence**: `COMPUTE_STATISTICS` operation type not in planner operation list
- **Fix Required**: Add COMPUTE_STATISTICS to planner, execute after OPTIMIZE

---

### API & Authentication

**Issue #11: No Authentication on New Endpoints**
- **Feature**: All dashboard endpoints
- **Problem**: `/api/query-performance`, `/api/sla-status` etc. have no auth
- **Impact**: Anyone can access sensitive business metrics
- **Evidence**: No `Depends(require_viewer)` on new endpoints
- **Fix Required**: Add authentication to ALL endpoints

**Issue #12: API Returns Unserializable Data**
- **Feature**: Anomaly detection, lineage, workload replay
- **Problem**: Returns Python dataclasses directly, not Pydantic models
- **Impact**: API responses fail with "Object of type 'dataclass' is not JSON serializable"
- **Evidence**: Functions return `@dataclass` instead of Pydantic `BaseModel`
- **Fix Required**: Convert all dataclasses to Pydantic models or add serialization

**Issue #13: No Rate Limiting**
- **Feature**: Query performance, workload replay
- **Problem**: Replay can trigger 1000s of Trino queries, no rate limit
- **Impact**: DDoS your own Trino cluster, bring down production
- **Evidence**: No rate limiting middleware in `api/app.py`
- **Fix Required**: Add rate limiting, especially on expensive operations

**Issue #14: No Input Validation**
- **Feature**: S3 tiering, partition evolution, cost attribution
- **Problem**: `lookback_days`, `min_size_gb` accept negative values
- **Impact**: Crashes, infinite loops, or nonsense results
- **Evidence**: No Pydantic Field validators or constraints
- **Fix Required**: Add min/max constraints on all numeric inputs

---

### Frontend & UI

**Issue #15: Frontend Pages Not Registered in Router**
- **Feature**: 6 new React pages
- **Problem**: Pages created but routes commented out or missing
- **Impact**: Users can't access new features via navigation
- **Evidence**: Need to verify App.tsx routing for ALL new pages
- **Fix Required**: Uncomment/add ALL routes in App.tsx

**Issue #16: No Error Handling in UI**
- **Feature**: All new dashboards
- **Problem**: No try/catch, no loading states, no error boundaries
- **Impact**: White screen of death on API errors
- **Evidence**: UI code has no error handling patterns
- **Fix Required**: Add error boundaries, loading states, error messages

**Issue #17: API Client Types Out of Sync**
- **Feature**: Query performance, health trends
- **Problem**: `web/src/api/client.ts` types manually written, not generated
- **Impact**: Type mismatches cause runtime errors
- **Evidence**: No OpenAPI type generation step
- **Fix Required**: Generate types from OpenAPI schema automatically

**Issue #18: No Pagination on Large Lists**
- **Feature**: Query performance (top 1000 queries)
- **Problem**: Trying to render 1000 rows in table without virtualization
- **Impact**: Browser freezes, tab crashes
- **Evidence**: No pagination or virtual scrolling in QueryPerformance.tsx
- **Fix Required**: Add pagination or virtual scrolling to all large tables

---

### Multi-Engine Support

**Issue #19: Spark Ingester Crashes on Auth Failure**
- **Feature**: Multi-engine telemetry (#15)
- **Problem**: Spark History Server often requires auth, code assumes no auth
- **Impact**: Ingestion fails silently, no Spark queries ingested
- **Evidence**: `SparkHistoryIngester` has no auth configuration
- **Fix Required**: Add auth support (Kerberos, basic auth, etc.)

**Issue #20: Athena Pagination Not Implemented**
- **Feature**: Multi-engine telemetry (#15)
- **Problem**: Athena paginator hardcoded to first page only
- **Impact**: Only fetches 50 queries, misses 95% of workload
- **Evidence**: `AthenaIngester.fetch_recent_queries()` doesn't follow pagination
- **Fix Required**: Implement proper pagination for ALL pages

**Issue #21: DuckDB History File Doesn't Exist**
- **Feature**: Multi-engine telemetry (#15)
- **Problem**: `~/.duckdb/history` is not a standard DuckDB feature
- **Impact**: Never ingests any DuckDB queries
- **Evidence**: DuckDB doesn't write history to this path by default
- **Fix Required**: Use DuckDB's actual query logging mechanism

**Issue #22: Flink Engine Never Executes Jobs**
- **Feature**: Flink engine integration (#9)
- **Problem**: Generates Java/SQL but doesn't submit to Flink cluster
- **Impact**: Flink "support" is completely fake, just generates unused code
- **Evidence**: `FlinkEngine.execute()` just logs, doesn't call Flink APIs
- **Fix Required**: Integrate with Flink REST API or JobManager to submit jobs

---

### Cost & Billing

**Issue #23: Cost Calculations Completely Wrong**
- **Feature**: Cost attribution, FinOps forecasting
- **Problem**: Hardcoded $0.023/GB assumes S3 Standard only
- **Impact**: Cost estimates 3-10× wrong for Glacier, IA, etc.
- **Evidence**: No S3 storage class detection
- **Fix Required**: Query actual storage class from S3, use correct pricing

**Issue #24: Compute Cost Estimation Based on Fantasy**
- **Feature**: FinOps forecasting (#28)
- **Problem**: "1 query = 0.01 hours" is completely made up
- **Impact**: Forecasts off by orders of magnitude
- **Evidence**: No actual engine metrics, just wild guess
- **Fix Required**: Use real query duration from engine metrics

**Issue #25: Cost Attribution Requires Manual Tags**
- **Feature**: Cost attribution (#13)
- **Problem**: Requires `cost_center`, `department` tags that don't exist
- **Impact**: Shows "unattributed: 100%" for all costs
- **Evidence**: Alert in UI says "requires custom tags" but no way to add them
- **Fix Required**: Add tag management UI or auto-detect from AWS Cost Explorer tags

**Issue #26: S3 Tiering Recommendations Ignore Access Patterns**
- **Feature**: S3 Intelligent-Tiering (#14)
- **Problem**: Only checks query log, ignores actual S3 access logs
- **Impact**: Recommends tiering for tables with direct S3 access, causes slowdowns
- **Evidence**: No S3 access log integration
- **Fix Required**: Integrate CloudTrail or S3 server access logs

---

### Security & Access Control

**Issue #27: ABAC Policies Have No Enforcement**
- **Feature**: Cross-engine ABAC (#21)
- **Problem**: Policy engine exists but Cryo doesn't act as REST catalog proxy
- **Impact**: ABAC is 100% theater, zero actual security
- **Evidence**: Code comments say "requires implementing full REST catalog spec"
- **Fix Required**: Either implement REST catalog proxy or remove ABAC entirely

**Issue #28: Multi-Tenant Isolation Not Applied**
- **Feature**: Multi-tenant isolation (#26)
- **Problem**: No migration adds tenant_id columns, no queries filter by tenant
- **Impact**: Tenant A can see Tenant B's data
- **Evidence**: Code is just helper functions, no actual enforcement
- **Fix Required**: Add tenant_id to ALL tables, add row-level security

**Issue #29: Custom Scripts Execute Untrusted Code**
- **Feature**: Custom maintenance scripts (#30)
- **Problem**: Docker sandbox has vulnerabilities (--network none can be bypassed)
- **Impact**: Users can escape sandbox, access host filesystem/network
- **Evidence**: Basic string validation, no AST analysis, no seccomp
- **Fix Required**: Use gVisor or Firecracker, not plain Docker

**Issue #30: Approval Requests Never Expire**
- **Feature**: Approval workflows (#18)
- **Problem**: `expires_at` checked but no cleanup job deletes expired requests
- **Impact**: Database fills with expired approvals, UI shows stale requests
- **Evidence**: No background job for cleanup
- **Fix Required**: Add cron job to mark expired requests as EXPIRED

---

### Data Quality & Accuracy

**Issue #31: Anomaly Detection False Positives**
- **Feature**: AI anomaly detection (#23)
- **Problem**: Z-score assumes normal distribution, breaks on skewed data
- **Impact**: 90% false positive rate, users ignore all alerts
- **Evidence**: No distribution type detection, always uses Z-score
- **Fix Required**: Use robust methods (MAD, IQR) for skewed distributions

**Issue #32: Partition Evolution Recommendations Always Wrong**
- **Feature**: Partition evolution (#11)
- **Problem**: Simple regex parsing misses 80% of table references
- **Impact**: Recommends partition changes based on 20% of queries only
- **Evidence**: `parse_query_for_lineage()` uses basic regex, not SQL parser
- **Fix Required**: Use sqlglot or sqlparse for accurate table extraction

**Issue #33: Data Quality Profiler Reads Fake Metadata**
- **Feature**: Data quality profiling (#17)
- **Problem**: Reads `manifest_stats` from `raw` JSONB that's never populated
- **Impact**: All quality checks return "no data"
- **Evidence**: Scanner doesn't populate `manifest_stats` in `raw` field
- **Fix Required**: Actually query $manifests table or populate stats during scan

**Issue #34: Lineage Graph Is Always Empty**
- **Feature**: Data lineage (#22)
- **Problem**: `record_lineage_from_query()` never called by any service
- **Impact**: Lineage UI shows empty graph always
- **Evidence**: No integration with query log ingestion
- **Fix Required**: Call lineage recording during query log ingestion

**Issue #35: Health Trends Chart Shows Same Value**
- **Feature**: Table health trends (#6)
- **Problem**: No historical data in `TableStatsHistory` on fresh install
- **Impact**: Chart shows single point, no trend
- **Evidence**: No seed data or historical backfill
- **Fix Required**: Add historical backfill job or seed data generator

---

### Performance & Scalability

**Issue #36: Anomaly Detection Scans All History**
- **Feature**: AI anomaly detection (#23)
- **Problem**: Loads entire 30-day history into memory for every table
- **Impact**: OOM kills API server with 10K+ tables
- **Evidence**: No pagination or windowing in `detect_all_anomalies()`
- **Fix Required**: Process tables in batches, use windowing

**Issue #37: Workload Replay Hammers Trino**
- **Feature**: Workload replay (#24)
- **Problem**: Executes 1000 queries serially, no rate limiting
- **Impact**: Brings down Trino cluster during testing
- **Evidence**: No concurrency control, no backpressure
- **Fix Required**: Add rate limiting, max concurrent queries

**Issue #38: GitOps Polling Creates Git Storm**
- **Feature**: GitOps integration (#25)
- **Problem**: Polls Git every 60 seconds, creates 1440 requests/day
- **Impact**: GitHub rate limits, blocks entire org
- **Evidence**: `watch_git_repository()` polls instead of using webhooks
- **Fix Required**: Use GitHub webhooks, not polling

**Issue #39: Embedded SDK Has No Caching**
- **Feature**: Embedded SDK (#27)
- **Problem**: Every widget reload fetches data from API
- **Impact**: 100 embedded widgets = 100 API requests/second
- **Evidence**: No caching in `embed.js` or API responses
- **Fix Required**: Add HTTP caching headers, client-side cache

**Issue #40: FinOps Forecast Recalculates Everything**
- **Feature**: FinOps forecasting (#28)
- **Problem**: Aggregates entire history on every request
- **Impact**: 10+ second response time for forecast endpoint
- **Evidence**: No pre-aggregation or materialized views
- **Fix Required**: Pre-compute daily/monthly aggregates

---

### Operational Complexity

**Issue #41: No Monitoring or Metrics**
- **Feature**: All features
- **Problem**: No Prometheus metrics, no structured logging
- **Impact**: Can't detect issues, can't measure performance
- **Evidence**: No metrics exports in any new modules
- **Fix Required**: Add metrics to ALL critical paths

**Issue #42: No Health Checks**
- **Feature**: All background services
- **Problem**: Scanner, governor, scheduler have no health endpoints
- **Impact**: K8s can't tell if service is stuck, never restarts
- **Evidence**: Only API has `/healthz` endpoint
- **Fix Required**: Add health checks to all services

**Issue #43: No Graceful Shutdown**
- **Feature**: Workload replay, custom scripts, GitOps watcher
- **Problem**: SIGTERM kills immediately, loses in-progress work
- **Impact**: Pod evictions cause data loss
- **Evidence**: No signal handlers, no cleanup logic
- **Fix Required**: Handle SIGTERM, finish current work, then exit

**Issue #44: No Rollback Mechanism**
- **Feature**: Approval workflows, GitOps
- **Problem**: Approved changes execute immediately, can't undo
- **Impact**: Accidental approval of bad policy takes down production
- **Evidence**: No rollback in approval execution
- **Fix Required**: Add rollback capability or dry-run mode

**Issue #45: GitOps Conflicts Not Handled**
- **Feature**: GitOps integration (#25)
- **Problem**: Multiple policy files for same table = undefined behavior
- **Impact**: Last-write-wins, policies fight each other
- **Evidence**: No conflict detection in `load_policies_from_directory()`
- **Fix Required**: Detect conflicts, error or merge deterministically

---

### Testing & Quality

**Issue #46: Only 1 Test File Exists**
- **Feature**: All features
- **Problem**: Only `test_flink_engine.py` tests new features
- **Impact**: 95% of new code is untested, bugs in production
- **Evidence**: No tests for anomaly detection, lineage, cost attribution, etc.
- **Fix Required**: 80%+ test coverage before production

**Issue #47: No Integration Tests**
- **Feature**: All features
- **Problem**: No end-to-end tests with real database
- **Impact**: Features work in isolation but break when integrated
- **Evidence**: No pytest fixtures for DB, no integration test suite
- **Fix Required**: Add integration tests with test database

**Issue #48: No Load Testing**
- **Feature**: All dashboards
- **Problem**: Never tested with 1M+ tables, 100M+ query logs
- **Impact**: API crashes at scale
- **Evidence**: No load test suite, no performance benchmarks
- **Fix Required**: Load test all new endpoints with realistic data volumes

**Issue #49: No Error Injection Testing**
- **Feature**: Multi-engine telemetry, REST catalog
- **Problem**: Never tested with network failures, timeouts, auth errors
- **Impact**: Crashes instead of graceful degradation
- **Evidence**: No chaos engineering, no fault injection
- **Fix Required**: Test ALL failure modes

**Issue #50: No Schema Validation Tests**
- **Feature**: All JSONB fields
- **Problem**: No tests verify JSONB structure matches expectations
- **Impact**: Code crashes when accessing undefined keys
- **Evidence**: No JSON schema validation
- **Fix Required**: Add JSON schema tests for all JSONB fields

---

## 🟡 HIGH SEVERITY (Data Loss / Incorrect Behavior)

**Issue #51: Credential Vending Tokens Expire Mid-Job**
- **Feature**: REST catalog credential vending (#12)
- **Problem**: STS tokens valid 1 hour, compaction takes 2+ hours
- **Impact**: Job fails halfway, leaves table in inconsistent state
- **Fix Required**: Refresh credentials before expiration

**Issue #52: Slack Bot Exposes Sensitive Data**
- **Feature**: Slack bot (#29)
- **Problem**: `/cryo status` shows table details in public channels
- **Impact**: Leaks confidential table names, sizes to entire company
- **Fix Required**: Send sensitive data as ephemeral messages

**Issue #53: Embedded SDK Leaks API Keys**
- **Feature**: Embedded SDK (#27)
- **Problem**: API key in client-side JavaScript visible to everyone
- **Impact**: Anyone can scrape and reuse API keys
- **Fix Required**: Use server-side proxy or JWT with CORS restrictions

**Issue #54: Custom Scripts Can Delete Data**
- **Feature**: Custom scripts (#30)
- **Problem**: Script says `table.optimize()` but actually calls Trino DROP
- **Impact**: Malicious users can drop tables via "maintenance" scripts
- **Fix Required**: Whitelist allowed operations, parse and validate calls

**Issue #55: Partition Evolution Breaks Time Travel**
- **Feature**: Partition evolution (#11)
- **Problem**: Recommends repartitioning without mentioning data rewrite cost
- **Impact**: Users follow recommendation, rewrite entire table, lose snapshots
- **Fix Required**: Calculate rewrite cost, warn about snapshot loss

**Issue #56: SLA Breach Alerts Spam**
- **Feature**: SLA monitoring (#16)
- **Problem**: Checks every minute, sends alert every minute
- **Impact**: Alert fatigue, PagerDuty storms
- **Fix Required**: Debounce alerts, send once per breach

**Issue #57: Cost Attribution Double-Counts**
- **Feature**: Cost attribution (#13)
- **Problem**: Table in multiple teams' tags = counted twice
- **Impact**: Total cost shows 150% of actual
- **Fix Required**: Detect multi-tag tables, either split or flag

**Issue #58: Workload Replay Corrupts Shadow Table**
- **Feature**: Workload replay (#24)
- **Problem**: Replays INSERT queries against shadow table
- **Impact**: Shadow table grows during test, results invalid
- **Fix Required**: Replace INSERT/UPDATE with SELECT-only queries

**Issue #59: Anomaly Detection Ignores Weekends**
- **Feature**: AI anomaly detection (#23)
- **Problem**: Baseline includes weekends, Monday spikes always "anomalous"
- **Impact**: Every Monday shows false anomalies
- **Fix Required**: Separate weekday/weekend baselines

**Issue #60: Iceberg v3 Detection False Positives**
- **Feature**: Iceberg v3 features (#20)
- **Problem**: Detects v3 from raw metadata that's not actually parsed from table
- **Impact**: Recommends deletion vector strategy for v2 tables, breaks
- **Fix Required**: Actually read table metadata from PyIceberg

---

## 🟠 MEDIUM SEVERITY (Degraded Experience)

**Issue #61-70: UI/UX Issues**
- No dark mode support
- Chart legends overlap on mobile
- Tables not responsive
- No keyboard navigation
- No export to CSV/Excel
- Time zones always UTC, should be user-local
- No column sorting on new tables
- No bulk selection
- No table search/filter memory
- Loading spinners missing

**Issue #71-80: Missing Features**
- No email notifications (Slack only)
- No webhook support (can't integrate with Jira, etc.)
- No audit log for UI actions
- No role-based access control (RBAC)
- No API versioning (breaking changes will break clients)
- No GraphQL endpoint (REST only)
- No batch operations
- No undo/redo
- No saved views/bookmarks
- No team sharing/collaboration

**Issue #81-87: Documentation Gaps**
- No deployment guide
- No API documentation
- No troubleshooting runbook
- No disaster recovery procedure
- No backup/restore instructions
- No capacity planning guide
- No cost optimization best practices

---

## ROOT CAUSE ANALYSIS

### Why Did All These Issues Happen?

1. **Speed Over Quality**: Implemented 30 features in 1 day, no time for testing/integration
2. **No Code Review**: Single developer, no peer review, no second pair of eyes
3. **Feature Factory**: Built features in isolation, no integration testing
4. **No Production Deployment**: Never actually deployed, never faced real-world issues
5. **Optimistic Engineering**: Assumed everything would "just work" when integrated
6. **No Staging Environment**: No place to test integrations before production
7. **Documentation Debt**: Wrote code faster than docs, docs never caught up
8. **No Operations Team**: Dev built it, no ops to review for operational concerns

---

## HOW WILL IT WORK? (Honest Answer)

### Month 1-2: Discovery Phase
- Users report endpoints return 500 errors
- Dashboard shows "loading..." forever
- No data in any new features
- Team discovers migrations not run
- Team discovers features not integrated

### Month 3-4: Fire Fighting Phase
- Emergency patches every day
- Rollback half the features
- Database performance degrades
- Users lose trust
- Management questions value

### Month 5-6: Stabilization Phase
- Feature freeze
- Pay down technical debt
- Add tests retroactively
- Rewrite core integration logic
- Document everything

### Reality Check
**Only ~40% of features will actually work in production by Month 6.**

The rest will be:
- Disabled (20%)
- Rewritten (30%)
- Abandoned (10%)

---

## RECOMMENDATIONS

### Before Production (Critical)

1. **Create ALL missing migrations** (2 weeks)
2. **Add authentication to ALL endpoints** (1 week)
3. **Write integration tests** (3 weeks)
4. **Wire features into core system** (2 weeks)
5. **Add indexes, FK constraints** (3 days)

### Month 1 (Stabilization)

6. **Deploy to staging, load test** (2 weeks)
7. **Fix top 20 critical bugs** (3 weeks)
8. **Add monitoring & alerts** (1 week)

### Month 2-3 (Production Readiness)

9. **Security audit & fixes** (2 weeks)
10. **Performance optimization** (2 weeks)
11. **Documentation sprint** (2 weeks)
12. **Production deployment plan** (1 week)

### Estimated Timeline to Production-Ready

**6 months minimum** to make the hastily-implemented features production-grade.

---

## CONCLUSION

**Current State**: Impressive demo, terrible product.

**Problem**: Built for breadth, not depth. 30 features that look good in a roadmap but don't actually work together.

**Solution**: Pick 10 most valuable features, do them right. Ship something that works, not something that looks complete.

**Hard Truth**: Better to have 10 solid features than 30 broken ones.
