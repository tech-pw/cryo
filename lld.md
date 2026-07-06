# Cryo — Low-Level Design (LLD)

Concrete contracts: repo layout, configuration, Postgres schema, domain models, engine interfaces,
the partition-aware planner algorithm, rendered SQL per engine/operation, API surface, and the
Governor/Scheduler logic. This is the build spec; `hld.md` is the map.

---

## 1. Repository layout

```
iceberg_compactor/                  # repo root (codename: Cryo)
├── pyproject.toml                  # backend package + tooling (ruff, mypy, pytest)
├── docker-compose.yml              # Cryo platform (postgres, redis, cryo-* services)
├── docker-compose.dev-datalake.yml # LOCAL/CI ONLY: minio, iceberg-rest, hms, trino, spark
├── docker-compose.observability.yml # optional prometheus, grafana, loki, promtail
├── Makefile                        # dev shortcuts (up, scan, api, worker, migrate, test)
├── deploy/
│   └── helm/cryo/                  # Helm chart (api, web, scheduler, governor, worker, scanner, redis)
├── migrations/                     # Alembic migrations
├── web/                            # React + TS + Vite + AntD frontend
│   ├── src/
│   │   ├── api/                    # generated client + hooks (TanStack Query)
│   │   ├── pages/                  # DatalakeHealth, TableExplorer, TableDetail, Presets, Runs, Settings
│   │   ├── components/             # FilterBar, BulkAssignBar, HealthBadge, RunConsole, PlanPreview
│   │   └── lib/
│   └── package.json
└── cryo/                           # Python backend package
    ├── config.py                   # pydantic-settings: env-driven config
    ├── db/
    │   ├── base.py                 # SQLAlchemy engine/session
    │   ├── models.py               # ORM models (see §3)
    │   └── repositories/           # data-access per aggregate
    ├── domain/
    │   ├── models.py               # pydantic domain DTOs (Plan, Operation, Predicate, Stats…)
    │   └── enums.py                # OperationType, EngineType, Mode, Priority, Grade, JobStatus
    ├── sources/                    # READ side
    │   ├── base.py                 # SourceCatalog interface
    │   ├── rest.py / hive.py / glue.py
    │   └── factory.py
    ├── engines/                    # WRITE side (kept separate per engine)
    │   ├── base.py                 # MaintenanceEngine interface (probe + render + execute)
    │   ├── trino/
    │   │   ├── client.py           # connection per coordinator
    │   │   ├── load.py             # system.runtime.queries probe
    │   │   ├── sql.py              # Trino SQL renderer
    │   │   └── engine.py
    │   └── spark/
    │       ├── emr.py              # EMR Serverless submit/poll (boto3)
    │       ├── sql.py              # Spark SQL renderer
    │       └── engine.py
    ├── planner/
    │   ├── partitions.py           # transform detection, predicate building
    │   ├── chunking.py             # GB-budget, partition-cap packer
    │   └── planner.py              # build MaintenancePlan (mode-aware)
    ├── cost/
    │   └── model.py                # pure cost + savings-projection math (no I/O) — see §16
    ├── scanner/
    │   ├── analyze.py              # PyIceberg manifest scan (port of stats batch)
    │   ├── health.py               # score/grade + thresholds
    │   ├── cost.py                 # storage cost + metadata-precise byte accounting (Tier 0/1)
    │   ├── orphans.py              # opt-in bounded storage LIST -> orphan bytes (Tier 2)
    │   └── run.py                  # chunked/resumable entrypoint (persists table_cost)
    ├── scheduler/
    │   ├── rules.py                # selection rule evaluation + effective-preset resolution
    │   └── scheduler.py            # cron eval -> runs -> jobs
    ├── governor/
    │   ├── admission.py            # capacity math (caps, budget, off-peak, cooldown, deadline)
    │   └── governor.py             # wave loop, dispatch, finalize
    ├── worker/
    │   └── worker.py               # consume job -> plan -> engine execute -> audit
    ├── reporting/
    │   ├── report.py               # HTML report (port) + Slack
    │   └── alerts.py               # drift/cost alerts
    ├── api/
    │   ├── app.py                  # FastAPI app factory
    │   ├── deps.py / security.py
    │   └── routers/                # sources, destinations, presets, assignments, tables,
    │                               # maintenance, runs, dashboards, ws
    └── queue/
        ├── redis_queue.py          # enqueue/dequeue, SKIP-LOCKED-style claim
        └── locks.py                # leader election + per-table locks
```

> **Rule of thumb:** anything that produces *Trino SQL* lives in `engines/trino/sql.py`; anything that
> produces *Spark SQL* lives in `engines/spark/sql.py`. The Planner never emits SQL strings.

---

## 2. Configuration (`cryo/config.py`)

`pydantic-settings`, env-driven (12-factor). Connection details for sources/destinations are stored
in the DB (encrypted secrets), not env — env is only for platform infra.

```python
class Settings(BaseSettings):
    database_url: str            # postgresql+psycopg://...
    redis_url: str               # redis://...
    secret_key: str              # JWT + field encryption
    environment: str = "dev"
    # scanner
    scan_num_chunks: int = 4
    scan_small_file_mb: float = 89.6          # 128 * 0.70 (parity with today)
    scan_needs_compaction_threshold: int = 128
    # cost & savings model (see §16)
    cost_price_per_gb_month: float = 0.023    # S3 Standard (parity); override per source region
    cost_use_gib: bool = True                 # True=GiB (1024^3, parity) | False=GB (1e9, true billing)
    cost_scan_tier: str = "metadata"          # per-scan default: heuristic | metadata | storage_truth
    cost_scan_max_snapshots: int = 200        # bound Tier-1 all-snapshot accounting; else -> heuristic
    cost_scan_max_manifests: int = 5000       # bound distinct manifests read for accounting
    cost_full_scan_every_hours: int = 168     # Tier-2 (orphan) cadence; 0 = only on demand
    cost_orphan_min_age_hours: int = 72       # ignore recently-written files as potential orphans
    cost_scan_max_list_keys: int = 1_000_000  # Tier-2 LIST guard; above -> orphan reported "unknown"
    default_retention_days: int = 7           # datalake-baseline expiry projection (parity: 7d)
    default_retain_last: int = 1
    s3_get_usd_per_1k: float = 0.0004         # request-cost model for optimize query savings
    s3_list_usd_per_1k: float = 0.005
    query_gets_per_file: float = 1.0          # GETs per data file per query (calibratable)
    # governor defaults (overridable per engine/preset)
    offpeak_start_ist: int = 0
    offpeak_end_ist: int = 7
    default_per_replica_optimize_cap: int = 5
    wave_wait_sec: int = 300
```

Secrets for sources/destinations are encrypted at rest (Fernet/KMS) in their DB rows.

---

## 3. Data model (PostgreSQL)

All tables in schema `cryo`. Timestamps `timestamptz`. Flexible blobs as `jsonb`.

### 3.1 Connectivity

```sql
-- READ side: Iceberg catalogs
CREATE TABLE cryo.sources (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text UNIQUE NOT NULL,
    type          text NOT NULL,              -- 'rest' | 'hive' | 'glue' | 'polaris' | 'nessie'
    config        jsonb NOT NULL,             -- uri, warehouse, region, catalog name...
    secret_ref    text,                       -- encrypted credentials ref
    enabled       boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- WRITE side: engines where maintenance executes
CREATE TABLE cryo.destinations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text UNIQUE NOT NULL,
    engine          text NOT NULL,            -- 'trino' | 'spark_emr'
    config          jsonb NOT NULL,           -- trino: {coordinators:[...], port, user, catalog,
                                              --         session:{...}, per_replica_cap}
                                              -- spark_emr: {application_id, role_arn, s3_logs,
                                              --             catalog_conf:{...}, slot_cap}
    secret_ref      text,
    offpeak_start   int,                       -- optional per-engine override (IST hour)
    offpeak_end     int,
    enabled         boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
```

### 3.2 Datalake (read side, produced by Scanner)

```sql
CREATE TABLE cryo.tables (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        uuid NOT NULL REFERENCES cryo.sources(id),
    schema_name      text NOT NULL,
    table_name       text NOT NULL,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    is_active        boolean NOT NULL DEFAULT true,
    UNIQUE (source_id, schema_name, table_name)
);

-- latest stats (1 row per table; upserted each scan) — superset of iceberg_compaction_scan
CREATE TABLE cryo.table_stats (
    table_id                       uuid PRIMARY KEY REFERENCES cryo.tables(id) ON DELETE CASCADE,
    scan_at                        timestamptz NOT NULL,
    needs_compaction               boolean NOT NULL,
    compaction_priority            text NOT NULL,           -- HIGH | MEDIUM | LOW
    grade                          text NOT NULL,           -- A..F
    overall_score                  int  NOT NULL,
    total_files                    bigint NOT NULL,
    small_files_count              bigint NOT NULL,
    total_partitions               int,
    max_files_per_partition        int,
    avg_files_per_partition        double precision,
    partition_skew_ratio           double precision,
    total_records                  bigint,
    total_size_gb                  double precision NOT NULL,
    avg_file_size_mb               double precision,
    total_snapshots                int,
    total_delete_files             int,
    delete_file_pct                double precision,
    manifest_count                 int,
    partition_col                  text,
    partition_col_type             text,
    write_distribution_mode        text,
    has_sort_order                 boolean,
    target_file_size_bytes         bigint,
    estimated_monthly_s3_cost_usd  double precision,        -- Tier-0 heuristic (parity, always set)
    -- cost/savings summary (denormalized from table_cost for cheap explorer filter/sort)
    projected_monthly_s3_cost_usd  double precision,        -- after standard maintenance
    projected_savings_usd_month    double precision,        -- current - projected (storage)
    reclaimable_bytes              bigint,                  -- expire + orphan + optimize + manifests
    query_count_30d                bigint DEFAULT 0,        -- from query metrics (ranking)
    raw                            jsonb                    -- full stats dict for forward-compat
);

-- per-partition rewrite detail (drives the partition-aware planner)
CREATE TABLE cryo.table_partitions (
    id                              bigserial PRIMARY KEY,
    table_id                        uuid NOT NULL REFERENCES cryo.tables(id) ON DELETE CASCADE,
    scan_at                         timestamptz NOT NULL,
    partition_value                 text,
    files_per_partition             int,
    small_files_in_partition_count  int,
    partition_size_gb               double precision,
    compaction_status               text                    -- 'F' (needs) | 'T'
);
CREATE INDEX ON cryo.table_partitions (table_id, scan_at);

-- time series for drift/trend
CREATE TABLE cryo.table_stats_history (
    id                  bigserial PRIMARY KEY,
    table_id            uuid NOT NULL REFERENCES cryo.tables(id) ON DELETE CASCADE,
    scan_at             timestamptz NOT NULL,
    small_files_count   bigint,
    total_files         bigint,
    total_size_gb       double precision,
    grade               text,
    overall_score       int
);
CREATE INDEX ON cryo.table_stats_history (table_id, scan_at);

-- precise cost & savings breakdown (1 row per table, upserted by Scanner) — see §16
CREATE TABLE cryo.table_cost (
    table_id                    uuid PRIMARY KEY REFERENCES cryo.tables(id) ON DELETE CASCADE,
    scan_at                     timestamptz NOT NULL,
    precision                   text NOT NULL,             -- heuristic | metadata | storage_truth
    price_per_gb_month          double precision NOT NULL,
    -- storage decomposition (bytes)
    live_bytes                  bigint NOT NULL,           -- referenced by current snapshot
    dead_bytes                  bigint NOT NULL,           -- referenced only by expirable snapshots
    metadata_bytes              bigint NOT NULL,           -- manifests + lists + metadata.json + stats
    orphan_bytes                bigint,                    -- NULL unless storage_truth tier ran
    listed_bytes                bigint,                    -- actual LIST total; NULL unless Tier 2
    last_full_cost_scan_at      timestamptz,               -- last time the orphan LIST ran
    -- reclaim by operation (bytes) — disjoint by construction (no double counting)
    reclaim_expire_bytes        bigint NOT NULL DEFAULT 0,
    reclaim_orphan_bytes        bigint NOT NULL DEFAULT 0,
    reclaim_optimize_bytes      bigint NOT NULL DEFAULT 0, -- delete-file merge reclaim
    reclaim_manifests_bytes     bigint NOT NULL DEFAULT 0,
    -- money
    current_monthly_cost_usd        double precision NOT NULL,
    projected_monthly_cost_usd      double precision NOT NULL,
    projected_savings_usd_month     double precision NOT NULL,
    query_request_savings_usd_month double precision NOT NULL DEFAULT 0,  -- optimize, request-cost est.
    raw                         jsonb                      -- assumptions + per-op + per-snapshot detail
);

-- cost trend (savings realized/available over time)
CREATE TABLE cryo.table_cost_history (
    id                          bigserial PRIMARY KEY,
    table_id                    uuid NOT NULL REFERENCES cryo.tables(id) ON DELETE CASCADE,
    scan_at                     timestamptz NOT NULL,
    current_monthly_cost_usd    double precision,
    projected_savings_usd_month double precision,
    reclaimable_bytes           bigint,
    precision                   text
);
CREATE INDEX ON cryo.table_cost_history (table_id, scan_at);
```

> **Realized savings**: after a maintenance job, the Worker records `actual_bytes_freed` per
> operation on `cryo.audit_log` (new nullable column) so the UI can show *predicted vs realized*
> and the model can self-calibrate. This is additive to §3.4's `audit_log`.

### 3.3 Presets, rules, assignments (binding layer)

```sql
CREATE TABLE cryo.presets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text UNIQUE NOT NULL,           -- e.g. 'spark-large', 'trino-standard'
    description     text,
    destination_id  uuid NOT NULL REFERENCES cryo.destinations(id),
    mode            text NOT NULL DEFAULT 'standard', -- light | standard | deep
    operations      jsonb NOT NULL,                 -- ordered: ["expire_snapshots","optimize",...]
    params          jsonb NOT NULL DEFAULT '{}',    -- thresholds: file_size_mb, retention_days,
                                                    -- min_small_files, chunk_size_gb, max_chunks,
                                                    -- max_partitions_per_chunk, retain_last
    schedule_cron   text,                            -- null = manual only
    priority        int NOT NULL DEFAULT 100,        -- higher wins when multiple rules match
    enabled         boolean NOT NULL DEFAULT true,
    guardrails      jsonb NOT NULL DEFAULT '{}',     -- max_table_files, max_table_gb, cooldown_hours
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- rule-based auto-matching (e.g. size_gb > 100 -> spark-large)
CREATE TABLE cryo.preset_rules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    preset_id   uuid NOT NULL REFERENCES cryo.presets(id) ON DELETE CASCADE,
    predicate   jsonb NOT NULL,   -- structured filter (see §6.2): schema glob, size, small files...
    enabled     boolean NOT NULL DEFAULT true
);

-- explicit assignment (UI "select all matching -> assign"); overrides rules
CREATE TABLE cryo.table_assignments (
    table_id    uuid PRIMARY KEY REFERENCES cryo.tables(id) ON DELETE CASCADE,
    preset_id   uuid NOT NULL REFERENCES cryo.presets(id) ON DELETE CASCADE,
    assigned_by text,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    pinned      boolean NOT NULL DEFAULT true   -- pinned beats rules
);
```

**Effective preset** for a table = `pinned assignment` if present, else the **highest-priority
enabled rule** that matches its latest stats, else none.

### 3.4 Runs, jobs, audit (write side)

```sql
CREATE TABLE cryo.runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          text NOT NULL,                 -- 'scheduled' | 'adhoc'
    preset_id     uuid REFERENCES cryo.presets(id),
    trigger       text,                          -- user / cron
    status        text NOT NULL DEFAULT 'running',
    deadline_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    summary       jsonb                          -- {ok, failed, deferred, skipped}
);

CREATE TABLE cryo.jobs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         uuid NOT NULL REFERENCES cryo.runs(id) ON DELETE CASCADE,
    table_id       uuid NOT NULL REFERENCES cryo.tables(id),
    destination_id uuid NOT NULL REFERENCES cryo.destinations(id),
    mode           text NOT NULL,
    plan           jsonb,                         -- the MaintenancePlan (operations + predicates)
    status         text NOT NULL DEFAULT 'queued',-- queued|admitted|running|succeeded|failed|skipped|deferred
    engine_ref     text,                          -- trino query id / EMR jobRunId
    replica        text,                          -- which Trino coordinator
    started_at     timestamptz,
    finished_at    timestamptz,
    error          text
);
CREATE INDEX ON cryo.jobs (run_id, status);

-- supersedes iceberg_maintenance_log
CREATE TABLE cryo.audit_log (
    id           bigserial PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    table_id     uuid REFERENCES cryo.tables(id),
    job_id       uuid REFERENCES cryo.jobs(id),
    action       text NOT NULL,                   -- expire_snapshots|optimize|remove_orphan_files|...
    status       text NOT NULL,                   -- OK | SKIPPED | FAILED
    engine       text,                            -- trino | spark_emr
    detail       text,
    duration_ms  bigint
);
CREATE INDEX ON cryo.audit_log (table_id, ts);
```

---

## 4. Domain enums & DTOs (`cryo/domain`)

```python
class OperationType(str, Enum):
    EXPIRE_SNAPSHOTS = "expire_snapshots"
    REMOVE_ORPHAN_FILES = "remove_orphan_files"
    OPTIMIZE = "optimize"                 # = rewrite_data_files
    OPTIMIZE_MANIFESTS = "optimize_manifests"  # = rewrite_manifests

class EngineType(str, Enum):
    TRINO = "trino"
    SPARK_EMR = "spark_emr"

class Mode(str, Enum):
    LIGHT = "light"; STANDARD = "standard"; DEEP = "deep"

class PartitionTransform(str, Enum):
    IDENTITY="identity"; DAY="day"; MONTH="month"; YEAR="year"; UNPARTITIONED="unpartitioned"

class CostPrecision(str, Enum):
    HEURISTIC = "heuristic"          # Tier 0: current-snapshot bytes + snapshot-overhead heuristic
    METADATA = "metadata"            # Tier 1: exact expiry/compaction/manifest reclaim from metadata
    STORAGE_TRUTH = "storage_truth"  # Tier 2: + orphan bytes from a bounded storage LIST

@dataclass
class PartitionPredicate:           # engine-agnostic; renderers turn this into a WHERE fragment
    base_column: str
    transform: PartitionTransform
    value: str                       # raw scan value (epoch-day int / 'YYYY-MM-DD' / identity str)
    col_type: str = ""

@dataclass
class Operation:
    type: OperationType
    params: dict                     # file_size_mb, retention_days, retain_last, ...
    predicates: list[PartitionPredicate] | None = None   # for chunked optimize (one chunk)

@dataclass
class MaintenancePlan:
    table_ref: str                   # catalog.schema.table
    mode: Mode
    operations: list[Operation]      # ordered, possibly multiple OPTIMIZE chunks
    notes: list[str]

@dataclass(frozen=True)
class StorageBreakdown:               # produced by the Scanner (bytes); input to the cost model
    live_bytes: int                   # referenced by current snapshot
    dead_bytes: int                   # referenced only by expirable snapshots
    metadata_bytes: int               # manifests + manifest lists + metadata.json + stats/puffin
    delete_bytes: int                 # delete files in current snapshot (merged by optimize)
    orphan_bytes: int | None = None   # None until the storage-truth tier runs
    listed_bytes: int | None = None   # actual LIST total (storage-truth tier only)

@dataclass(frozen=True)
class SavingsProjection:              # produced by cost/model.py (pure)
    precision: CostPrecision
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    reclaimable_bytes: int
    by_operation: dict[OperationType, float]      # usd/month saved, per op (disjoint)
    query_request_savings_usd_month: float = 0.0  # optimize only; request-cost estimate, shown apart
    assumptions: dict = field(default_factory=dict)
```

---

## 5. Sources (read side) — `cryo/sources`

```python
class SourceCatalog(Protocol):
    def list_tables(self) -> list[tuple[str, str]]: ...          # (schema, table)
    def load_table(self, schema: str, table: str): ...           # PyIceberg Table
```

- `rest.py`, `hive.py`, `glue.py` wrap `pyiceberg.catalog.load_catalog(...)` with the Source config.
- Table enumeration may also use Trino `information_schema` when a Trino destination is co-located
  (parity with `get_all_tables()` today), but the canonical path is PyIceberg from the Source.

---

## 6. Planner (`cryo/planner`) — the crown jewel, engine-agnostic

### 6.1 Inputs/outputs
- **Input:** table latest `table_stats` + `table_partitions` rows + `Mode` + resolved `params`.
- **Output:** a `MaintenancePlan` (ordered `Operation`s with structured `PartitionPredicate`s).
- **No engine I/O, no SQL strings.** Pure function → unit-testable.

### 6.2 Filter / predicate spec (shared by rules, explorer, planner)
A single JSON predicate grammar powers UI filters, preset rules, and bulk selection:
```json
{ "all": [
    {"field": "schema_name", "op": "in", "value": ["mview"]},
    {"field": "total_size_gb", "op": ">", "value": 100},
    {"field": "small_files_count", "op": ">=", "value": 1000},
    {"field": "grade", "op": "in", "value": ["D","F"]}
] }
```
Compiled to SQLAlchemy filters for the explorer/selection, and evaluated in-memory for rule matching.
Fields = columns of `table_stats` (+ `schema_name`/`table_name`), including the denormalized cost
summary fields `projected_savings_usd_month`, `projected_monthly_s3_cost_usd`, and
`reclaimable_bytes` — so "select all tables with projected savings > $50/mo → Run now" is one
selection spec. Ops: `=,!=,<,<=,>,>=,in,like,glob`.

### 6.3 OPTIMIZE planning algorithm (ported from `_partition_aware_optimize`)
```
load partition_col, transform, table_small_files, table_total_size_gb, partitions
detect transform: suffix (_day/_month/_year) or $partitions introspection for date/timestamp
if unpartitioned:
    if table_small_files >= min_small_files -> 1 full-table OPTIMIZE (no predicates)
    else -> SKIP
elif table_total_size_gb <= chunk_size_gb:        # small-table bypass
    if table_small_files >= min_small_files -> 1 full-table OPTIMIZE
    else -> SKIP
else:
    candidates = partitions where small_files_in_partition_count >= min_small_files
    sort by (small_files DESC, size_gb ASC, value DESC)
    chunks = greedy_pack(candidates,
                         gb_budget=chunk_size_gb,
                         max_parts=max_partitions_per_chunk,
                         oversize_alone=True,
                         max_chunks=max_chunks)
    for chunk in chunks: emit OPTIMIZE Operation with chunk's PartitionPredicates
```
`chunking.py::greedy_pack` and `partitions.py::build_predicate` are direct ports of the Jinja logic,
now in testable Python. Transform → value mapping is preserved exactly (epoch-day math for `day`,
`(val//12, val%12)` for `month`, `val+1970` for `year`, identity equality otherwise).

### 6.4 Mode → operations (ported)
| Mode | Operations (in order) |
|------|-----------------------|
| light | `expire_snapshots` |
| standard | `expire_snapshots`, `optimize` (partition-aware) |
| deep | `expire_snapshots`, `remove_orphan_files`, `optimize` (partition-aware), `optimize_manifests` |

Operation order is enforced for correctness (expire before orphan removal, manifests last).

---

## 7. Engines (write side) — `cryo/engines`

### 7.1 Interface (`engines/base.py`)
```python
class MaintenanceEngine(Protocol):
    type: EngineType
    def probe_capacity(self) -> CapacitySnapshot: ...   # live load -> free slots
    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...  # dialect SQL
    def execute(self, stmt: RenderedStatement, ctx: ExecCtx) -> ExecResult: ...
    def session_setup(self) -> list[str]: ...           # SET SESSION ... (Trino) / conf (Spark)
```
`render()` is the ONLY place dialect SQL is produced; Trino and Spark have separate `sql.py`.

### 7.2 Trino renderer (`engines/trino/sql.py`) — parity with today
```python
# OPTIMIZE (chunked, partition-filtered)
ALTER TABLE {catalog}.{schema}.{table}
EXECUTE optimize(file_size_threshold => '{file_size_mb}MB')
[ WHERE {predicates joined by OR} ]

# expire
ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => '{retention}d', retain_last => {n})
# orphan
ALTER TABLE ... EXECUTE remove_orphan_files(retention_threshold => '{retention}d')
# manifests
ALTER TABLE ... EXECUTE optimize_manifests
```
Predicate rendering (transform-aware), examples:
- `month` → `(col >= DATE 'YYYY-MM-01' AND col < DATE 'next')`
- `day` (int epoch) → `(col >= DATE '1970-01-01' + INTERVAL 'N' DAY AND col < ... 'N+1' DAY)`
- `day` (date) → `(col >= DATE 'YYYY-MM-DD' AND col < DATE 'YYYY-MM-DD' + INTERVAL '1' DAY)`
- `year` → `(col >= DATE 'YYYY-01-01' AND col < DATE 'YYYY+1-01-01')`
- `identity` → `col = '<value>'`

Session setup before OPTIMIZE (ported): run `SET SESSION task_min_writer_count = 8` and
`SET SESSION writer_scaling_min_data_processed = '32MB'`. Trino does not expose
`<catalog>.target_max_file_size` as a session property in the local Trino 482 deployment; set
`iceberg.target-max-file-size=256MB` in the Iceberg catalog properties to control the writer target.
`optimize(file_size_threshold => ...)` remains the procedure threshold that selects candidate files
for rewrite.

Load probe (`engines/trino/load.py`): per-coordinator
```sql
SELECT count(*) FROM system.runtime.queries
WHERE state IN ('RUNNING','QUEUED') AND query LIKE '%EXECUTE optimize%'
  AND query NOT LIKE '%system.runtime.queries%'
```

### 7.3 Spark renderer (`engines/spark/sql.py`) — Iceberg stored procedures
Same plan → Spark SQL `CALL`s against the Iceberg catalog (`spark_catalog`/configured name):
```sql
-- OPTIMIZE == rewrite_data_files (partition filter via `where`, binpack target size via options)
CALL {cat}.system.rewrite_data_files(
  table => '{schema}.{table}',
  where => '{predicate sql}',
  options => map('target-file-size-bytes','{bytes}', 'min-input-files','{min}')
)
-- expire
CALL {cat}.system.expire_snapshots(table=>'{schema}.{table}', older_than => TIMESTAMP '{cutoff}', retain_last => {n})
-- orphan
CALL {cat}.system.remove_orphan_files(table=>'{schema}.{table}', older_than => TIMESTAMP '{cutoff}')
-- manifests
CALL {cat}.system.rewrite_manifests(table=>'{schema}.{table}')
```
The **same** `PartitionPredicate`s render to Spark SQL `where` strings (Spark accepts plain SQL date
literals: `col >= DATE '2025-05-18' AND col < DATE '2025-05-19'`, etc.). Chunking still applies
(one `rewrite_data_files` CALL per chunk) so big tables stay bounded on Spark too.

EMR Serverless adapter (`engines/spark/emr.py`): `boto3.client('emr-serverless')` →
`start_job_run(applicationId, executionRoleArn, jobDriver={sparkSubmit:{entryPoint: cryo_spark_runner.py,
sparkSubmitParameters: "--conf spark.sql.catalog... "}, ...})`; poll `get_job_run` until terminal;
fetch logs from the configured S3 location. Capacity = configured slot cap minus in-flight runs.

A tiny PySpark entrypoint (`cryo_spark_runner.py`, shipped to S3) receives the list of `CALL`
statements (as args/JSON) and executes them with the Iceberg-configured `SparkSession`.

---

## 8. Scanner (`cryo/scanner`) — port of `iceberg_table_stats_batch.py`

- `analyze.py`: per-table PyIceberg manifest stream (one pass, O(1) memory). Preserves: small-file
  thresholds (`128MB` compaction / `8MB` health), per-partition accumulation, delete-file metrics,
  manifest stats, properties, transform detection, `needs_compaction` + `priority` formulas.
- `health.py`: the exact `_compute_health_score` deductions (WARN −8 / CRIT −20) → score + A–F grade.
- `cost.py`: Tier-0 snapshot-overhead heuristic + `$0.023/GB-month` S3 estimate (parity, always set),
  **plus Tier-1 metadata byte accounting** — during the manifest stream it also walks retained
  snapshots' manifest lists (deduped, bounded by `cost_scan_max_snapshots/_manifests`) to split bytes
  into live / dead(expirable) / metadata and compute exact per-operation reclaim. No data reads, no
  S3 LIST. See §16.
- `orphans.py`: Tier-2 opt-in orphan estimation — a bounded, paginated storage LIST of the table's
  data prefix; `orphan_bytes = listed − referenced_by_any_retained_snapshot` (age-guarded by
  `cost_orphan_min_age_hours`). Runs on the `cost_full_scan_every_hours` cadence or on demand; the
  only step that spends S3 request cost. See §16.
- `run.py`: deterministic chunking (`i % num_chunks == chunk_id`), skip-already-scanned-today unless
  `full_scan`, batched upserts to Postgres (`tables`, `table_stats`, `table_partitions`, `table_cost`,
  history). Idempotent; retry-safe. `--cost-full-scan` opts a run into the Tier-2 orphan pass.
- Runs as K8s `CronJob` → parallel `Job` with `num_chunks` pods (mirrors today's 4 chunks).

---

## 9. Scheduler (`cryo/scheduler`)

```
on cron tick:
  for preset in enabled presets with due schedule_cron:
     candidates = resolve_tables(preset)            # rules + explicit assignments (effective preset == this preset)
     candidates = drop_recently_maintained(candidates, preset.guardrails.cooldown_hours)
     candidates = apply_priority_order(candidates)  # HIGH->MEDIUM->LOW, small_files DESC
     run = create_run(kind='scheduled', preset, deadline=window_end)
     for table in candidates[:budget_hint]:
         create_job(run, table, preset.destination, preset.mode, plan=None)  # plan built by worker
         enqueue(job)
```
`rules.py` provides `effective_preset(table)` and `tables_for_preset(preset)`. Budgeting is a *hint*;
the **Governor** enforces real limits at admission time. Leader-elected (Redis lock) so only one
scheduler is active.

---

## 10. Governor (`cryo/governor`) — wave scheduler / admission control

Ported from the Airflow `_maintain_tables` wave loop, generalized to multiple engines.
```
loop while pending or in_flight:
  reap finished jobs -> audit + counters
  if deadline_near(): defer remaining; break
  for each destination with pending jobs:
     cap        = destination.per_replica_cap (Trino, per coordinator) or slot_cap (EMR)
     live_used  = engine.probe_capacity()                    # runtime.queries / EMR runs
     local_used = redis in-flight for that destination
     free       = max(0, cap - max(live_used, local_used))
     free       = adapt_for_offpeak_and_load(free)           # adaptive budget
     dispatch min(free, pending) jobs (round-robin replicas for Trino) -> Worker
  sleep(wave_wait_sec)
finalize run summary
```
- **Per-table lock** (Redis) acquired at admission, released on completion → no double maintenance.
- **Off-peak**: heavy modes (standard/deep `optimize`,`remove_orphan_files`) blocked outside the
  destination/preset off-peak window; `light` allowed anytime.
- **Cooldown / deadline / adaptive budget / round-robin**: all preserved from today.

---

## 11. Worker (`cryo/worker`)

```
job = claim_admitted_job()
acquire per-table lock (already held by governor handoff)
stats, partitions = load_latest(job.table_id)
plan = planner.build_plan(stats, partitions, job.mode, preset.params)
persist plan to jobs.plan
engine = engine_factory(job.destination)
for stmt in engine.render(plan):
    res = engine.execute(stmt, ctx)
    audit_log(table, action=stmt.action, status=OK/FAILED, engine, duration)
    stream_progress_to_redis(job, stmt)
mark job succeeded/failed; release lock; update live counters
```
Failure on one statement/table is caught, audited, and isolated (run continues). Engine ref (Trino
query id / EMR jobRunId) is recorded for traceability.

---

## 12. API surface (FastAPI) — `cryo/api/routers`

Auth: JWT bearer; roles admin/operator/viewer. All list endpoints support server-side
filter/sort/paginate using the §6.2 predicate grammar.

```
# Connectivity
GET/POST/PATCH/DELETE  /api/v1/sources
GET/POST/PATCH/DELETE  /api/v1/destinations
POST                   /api/v1/destinations/{id}/test         # health check / probe

# Datalake & health
GET   /api/v1/tables                                          # ?filter=<predicate>&sort=&page=
GET   /api/v1/tables/{id}                                     # detail: stats, partitions, audit, cost
GET   /api/v1/tables/{id}/partitions
GET   /api/v1/tables/{id}/audit
GET   /api/v1/tables/{id}/history
GET   /api/v1/tables/{id}/cost                               # breakdown + projected savings by op
GET   /api/v1/dashboards/datalake                             # grade dist, cost, drift, totals
GET   /api/v1/dashboards/schema-cost
GET   /api/v1/dashboards/cost                                 # datalake current vs projected, savings
        # by operation + by schema, top savings opportunities, precision coverage
POST  /api/v1/scans                                           # trigger ad-hoc scan (optional scope)
        # body: {selection?, full_scan?, cost_full_scan?}  (cost_full_scan -> Tier-2 orphan pass)

# Presets, rules, assignments
GET/POST/PATCH/DELETE  /api/v1/presets
POST  /api/v1/presets/{id}/simulate                           # -> {matched_count, est_load, sample}
GET/POST/DELETE        /api/v1/presets/{id}/rules
POST  /api/v1/assignments                                     # body: {selection, preset_id}
        # selection = {mode:"ids", ids:[...]} OR {mode:"filter", filter:<predicate>}
DELETE /api/v1/assignments                                    # same selection grammar (unassign)
GET   /api/v1/tables/{id}/effective-preset                    # provenance: pinned vs rule vs none

# Maintenance (ad-hoc) + preview
POST  /api/v1/maintenance/preview                             # dry-run -> per-table rendered SQL +
        # plan-accurate SavingsProjection (current/projected cost, savings by op) for the payload
POST  /api/v1/maintenance/run                                 # create adhoc run + jobs
        # body accepts either table_ids:[...] OR selection:{mode:"filter", filter:<predicate>}

# Runs & live monitoring
GET   /api/v1/runs                  GET /api/v1/runs/{id}      GET /api/v1/runs/{id}/jobs
GET   /api/v1/jobs/{id}
POST  /api/v1/runs/{id}/cancel
WS    /api/v1/ws/runs/{id}                                    # live job state / logs
```

**Bulk assignment is a selection spec, not a list of IDs**, so "filter → select all 3,000 matches →
assign" is one transactional call that stays correct even as the datalake changes.

---

## 13. Frontend specifics (`web/`)

- **Table Explorer**: AntD `<Table>` with `rowSelection` (`selectedRowKeys` + "select all matching
  filter" custom action that sends the *filter*, not page IDs), server-side `onChange` →
  `/api/v1/tables`. Column filters map to the predicate grammar. A sticky **BulkAssignBar** shows
  "N tables match → [Assign preset ▼] [Run now]".
- **PlanPreview**: modal showing per-table rendered SQL (Trino/Spark) + chunk/partition/load estimate
  **and the plan-accurate projected savings by operation** before any execution.
- **RunConsole**: WebSocket-driven; per-engine concurrency gauges (Trino active OPTIMIZEs by replica,
  EMR slots), live job rows (running/queued/deferred/failed), log drawer; **realized-vs-projected
  savings** as jobs finish.
- **DatalakeHealth**: Recharts — grade distribution, cost by schema, drift list, top offenders, totals.
- **CostSavings**: Recharts — datalake current vs projected monthly cost, savings by operation, savings
  by schema, ranked "biggest savings opportunities" (→ select all → Run), precision-coverage badge.
- **TableDetail**: health card + partition table + snapshot/manifest stats + audit timeline +
  **cost breakdown & projected savings by operation** + "Run".
- Data layer: TanStack Query; types generated from the OpenAPI schema.

---

## 14. Testing strategy
- **Unit (pure)**: planner (chunking, predicate building, transform math), health scoring, cost,
  rule evaluation, predicate→SQL renderers (golden tests for Trino *and* Spark).
- **Contract**: engine adapters against the local docker-compose (Trino + Spark) on seeded tables.
- **Integration**: scanner against MinIO+REST; scheduler→governor→worker happy path on local stack.
- **API**: FastAPI TestClient; selection/assignment transactional correctness.
- **e2e (smoke)**: docker-compose up → scan → assign preset → run → assert audit + file-count drop.
- Golden parity tests assert the Trino SQL matches today's dbt-rendered SQL for representative tables.
- **Cost & savings** (see §16): pure-model golden tests (crafted `StorageBreakdown` → expected
  `SavingsProjection`; heuristic tier == existing `estimate_storage_cost`); metadata-accounting tests
  on multi-snapshot fixtures assert exact expire reclaim (predicted == Iceberg's own deletion set);
  orphan tests seed unreferenced files in MinIO and assert detection + bounding; smoke asserts
  predicted savings reconcile with realized `actual_bytes_freed` within tolerance.

---

## 15. Security & ops
- Source/destination secrets encrypted at rest (Fernet/KMS); never returned by the API in clear.
- Trino over HTTPS + BasicAuth (as today); EMR via IAM role (`executionRoleArn`).
- Least-privilege: viewers read-only; operators can run/assign; admins manage connections/presets.
- Migrations via Alembic; blue/green-safe (additive first). Prometheus metrics + structured logs.
- All destructive Iceberg ops are guarded by Planner/preset guardrails (max files/GB, cooldown) and
  require an explicit `force` to bypass (audited).

---

## 16. Cost & savings projection (precise, cost-efficient)

Goal: show each table's **current monthly storage cost** and the **projected cost + savings once all
maintenance is done** (compaction/`optimize`, snapshot **expiry**, **orphan-file** removal, manifest
rewrite), rolled up by schema and datalake. Numbers must be **precise**; the scan must stay **cheap**.

The design resolves that tension with a **tiered scan** (cost grows only when precision demands it)
plus a **pure projection model** (`cost/model.py`) shared by the Scanner (datalake baseline) and the
API/Worker (plan-accurate). Reclaim is computed the way the engines actually compute deletions, so
projections match reality.

### 16.1 Byte accounting (what the Scanner produces)

A table's storage decomposes into disjoint buckets so per-op savings never double count:

| Bucket | Meaning | Reclaimed by |
|--------|---------|--------------|
| `live_bytes` | data files referenced by the **current** snapshot | (kept) |
| `delete_bytes` | delete files in the current snapshot | `optimize` (merged), then expiry |
| `dead_bytes` | data/delete/manifest files referenced **only** by snapshots the retention policy would expire | `expire_snapshots` |
| `metadata_bytes` | manifests + manifest lists + `metadata.json` versions + stats/puffin | `optimize_manifests` (partial) + expiry |
| `orphan_bytes` | files under the table prefix referenced by **no** snapshot (Tier 2 only) | `remove_orphan_files` |

`current_storage_bytes = live + delete + dead + metadata + orphan`.

### 16.2 Tiered scan (precise but cost-efficient)

**Tier 0 — heuristic (always, ~free).** The existing `estimate_storage_cost`: current-snapshot bytes
+ snapshot-overhead heuristic. Zero extra reads. Kept as `estimated_monthly_s3_cost_usd` (parity) and
the fallback when higher tiers are bounded out. `precision = heuristic`.

**Tier 1 — metadata-precise (default, cheap).** During the manifest stream the Scanner already does,
it *also* walks the manifest lists of retained snapshots (deduped by manifest path; bounded by
`cost_scan_max_snapshots` / `cost_scan_max_manifests`) to attribute every file to `live` vs `dead`
vs `metadata`. **No data reads, no S3 LIST.** Yields exact reclaim for expiry, compaction
(delete-file merge), and manifests. `precision = metadata`. If a table exceeds the bounds, that
table falls back to Tier 0 for `dead_bytes` and is flagged `precision = heuristic`.

**Tier 2 — storage-truth / orphans (opt-in, bounded, slow cadence).** `scanner/orphans.py` does a
paginated, prefix-scoped S3 LIST of the table's data location and computes
`orphan_bytes = listed_bytes − referenced_by_any_retained_snapshot_bytes`, ignoring files younger
than `cost_orphan_min_age_hours`. This is the **only** step that spends S3 request cost, so it is
gated by `cost_full_scan_every_hours` (or `--cost-full-scan` / API `cost_full_scan:true`) and skipped
for tables above `cost_scan_max_list_keys` (orphan reported as `unknown`). `precision = storage_truth`.
Between Tier-2 runs, the last known `orphan_bytes` is carried forward (flagged stale via
`last_full_cost_scan_at`) so dashboards stay complete without re-listing.

> Default daily scans = **Tier 0+1** → precise expiry/compaction/manifest savings with no LIST.
> Orphan precision is layered in periodically by Tier 2. That is the precise-yet-cheap balance.

### 16.3 Exact expiry reclaim (metadata only)

Replicates Iceberg's own expiry set from metadata — no engine call:
```
surviving = { s in snapshots : s is current
                              OR s.timestamp_ms >= now - retention_days
                              OR s in newest `retain_last` snapshots }
for each file f seen across all retained snapshots' manifests:
    referencing = { snapshots whose manifests reference f }
    if referencing ∩ surviving == ∅:  f is reclaimed by expire_snapshots
reclaim_expire_bytes = Σ size(f) over reclaimed files (data + delete + manifest + list)
```
`retention_days` / `retain_last` come from the effective preset params for plan-accurate runs, or
`default_retention_days` / `default_retain_last` (parity: 7d) for the datalake baseline.

### 16.4 Per-operation savings (`cost/model.py`, pure)

`bytes_to_usd(b) = b / (1024³ if cost_use_gib else 1e9) * price_per_gb_month`.

- `expire` → `bytes_to_usd(reclaim_expire_bytes)`.
- `remove_orphan_files` → `bytes_to_usd(orphan_bytes)` (0 / unknown until Tier 2).
- `optimize` (storage) → `bytes_to_usd(delete_bytes_merged)` — compaction rewrites small files into
  fewer large ones (data-byte-neutral) but drops applied delete files; the small-file bytes are
  reclaimed via the follow-on `expire`. Modeled conservatively so we never over-promise.
- `optimize` (request) → `query_request_savings_usd_month` = `Δfiles × query_gets_per_file ×
  query_count_30d × s3_get_usd_per_1k/1000`, where `Δfiles = small_files − ceil(small_bytes /
  target_file_size)`. Reported **separately** (it is an estimate, not storage).
- `optimize_manifests` → `bytes_to_usd(reclaim_manifests_bytes)` (small manifests consolidated).

`projected_savings_usd_month = Σ storage savings` (disjoint buckets → simple sum);
`projected_monthly_cost_usd = current_monthly_cost_usd − projected_savings_usd_month`.

### 16.5 Two entry points + calibration

- **Datalake baseline** (Scanner): standard-mode ops + default params → upsert `table_cost` +
  `table_cost_history` and the denormalized summary columns on `table_stats`. Powers dashboards and
  explorer filters ("if we ran standard maintenance").
- **Plan-accurate** (`/maintenance/preview`, Worker): the *actual* effective-preset params → exact
  projection for that plan, shown in PlanPreview.
- **Realized savings**: after a job the Worker records `actual_bytes_freed` per op (audit_log) —
  from the engine result or a cheap snapshot-summary re-read — for predicted-vs-realized display and
  to calibrate `query_gets_per_file` / the compaction heuristic over time.

### 16.6 Parity & precision notes
- `0.023/GB-month` and the snapshot-overhead heuristic are preserved as **Tier 0** (matches the ported
  stats batch). Expiry/orphan retention defaults `7d` match the reference macro
  (`retention_threshold => '7d'`).
- Billing precision knob: `cost_use_gib` switches GiB (parity with today's estimate) vs GB (10⁹, how
  S3 actually bills). Region price overridable per source.
- The cost model is **engine-agnostic and pure** (no SQL, no I/O) — same layering rule as the Planner.
