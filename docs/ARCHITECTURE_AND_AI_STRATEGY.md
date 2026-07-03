# Cryo: Architecture & AI Strategy — Decided

> Written 2026-07-03. This is a **decision** document, not an options survey — the point was to
> pick one architecture and one AI approach, grounded in real prior art, and commit to
> implementing them. It extends `docs/RECOMMENDATIONS.md` (what to build) with *how* to build it
> and *what the AI layer actually is*.

---

## Part 1 — The architecture decision

### 1.1 What we are explicitly NOT doing, and why

- **Not** rewriting the control plane as a Kubernetes-operator/CRD reconciliation system. No
  credible real-world Iceberg control plane — not Netflix, not LinkedIn OpenHouse, not Apache
  Amoro, not Polaris/Floe — uses this pattern for table maintenance. The operator pattern
  (Strimzi, the Flink Kubernetes Operator, Spark Operator) fits *cluster/runtime lifecycle*
  problems (start/stop/scale a long-running process against a spec) well. Table maintenance is a
  different shape: "desired state" has to be *computed* from a metadata scan, not diffed against a
  static spec, and execution already happens inside engines (Trino/Spark) outside Kubernetes'
  native reconciliation semantics. Adopting it would mean abandoning FastAPI/Postgres/Redis for
  Go/kubebuilder or Java, for a pattern with zero proven benefit in this specific domain and real
  precedent against it.
- **Not** copying Netflix's exact stack (a custom Iceberg REST catalog on CockroachDB). That solves
  a problem Cryo doesn't have — Cryo is deliberately catalog-agnostic (REST/HMS/Glue), it doesn't
  run its own catalog.
- **Not** rewriting the control plane in Java/Spring like LinkedIn OpenHouse. Its architecture is
  useful as validation, not as a template to copy language-for-language.

### 1.2 What we ARE doing: the current shape, plus one new event fast-path

Keep everything that exists today: FastAPI, Postgres (SQLAlchemy/Alembic), Redis, the
Scanner → Planner → Governor → Scheduler → Worker pipeline, the Trino and Spark/EMR engines, K8s +
Helm deployment. This is not a compromise — it is independently validated by the two most relevant
pieces of real prior art:

- **LinkedIn OpenHouse** (github.com/linkedin/openhouse) is REST microservices + Postgres-backed
  service state + a Kubernetes CronJob that periodically triggers per-table Spark maintenance
  jobs. That is, structurally, Cryo's Scanner/Planner/Scheduler/Worker shape, just written in
  Java/Spring instead of Python/FastAPI.
- **Apache Amoro** and the newer **Polaris + Floe** combination both run a continuous poll /
  priority-score loop (Floe computes a per-table "debt score" from health + staleness + failure
  rate and dispatches Spark/Trino jobs), not a CRD reconciliation loop.

The one genuine architectural gap, found only in Netflix's setup, is that **everyone else is
polling/cron-only** — Netflix's Autotune is the sole documented production system that reacts to
catalog **commit events** (via SQS) rather than waiting for the next scan cycle, with three
Janitor services (TTL/snapshot/orphan) running the same event-driven way and a soft-delete grace
window as a safety net. That reactive fast-path, running *alongside* — not instead of — a periodic
sweep, is the one addition worth making.

**Decision: add an event fast-path, keep the existing sweep as the reliability backstop.**

```
                     ┌─────────────────────────────────────────┐
                     │   Full-fleet sweep (slow path, unchanged) │
                     │   K8s CronJob → Scanner → full re-score   │
                     │   Catches: cold tables, missed events,    │
                     │            drift, correctness backstop    │
                     └─────────────────────────────────────────┘
                                        │
  ┌─────────────────────────────────────────────────────────────┐
  │                        cryo/events/  (new)                    │
  │                                                                │
  │  watcher.py — polls current-snapshot-id at a tight interval    │
  │  (seconds) *only* for tables already flagged "hot" by a prior   │
  │  scan (streaming/commit-velocity detection — see               │
  │  RECOMMENDATIONS.md Part 2) → publishes TableCommitted to a     │
  │  Redis Stream on change.                                        │
  │                                                                │
  │  consumer.py — Scanner + Governor subscribe as a Redis Streams  │
  │  consumer group; on TableCommitted, opportunistically           │
  │  re-score just that table and re-evaluate scheduling instead    │
  │  of waiting for the next sweep.                                 │
  └─────────────────────────────────────────────────────────────┘
```

This mirrors Netflix's SQS-event-plus-Janitor-backstop coexistence and Floe's poll+signal
duality — both real, production-proven shapes, not a novel bet. It requires no new infrastructure
dependency (Redis is already in the stack; Streams + consumer groups is a feature of it, not a new
service) and no rewrite of anything that already works.

**Note for later, not now:** if Cryo ever operates against a Postgres-backed REST catalog it
controls directly (e.g., self-hosting Lakekeeper), prefer logical replication/CDC on the catalog's
own Postgres WAL over polling — Lakekeeper's Rust/async design is the reference point for why
low-latency catalog-adjacent coordination matters. This doesn't apply today because Cryo connects
to *external*, third-party-operated catalogs (REST/HMS/Glue) — polling hot tables is the only
practical option there, since the Iceberg REST Catalog spec has no standardized commit-webhook
mechanism yet.

### 1.3 Execution engines (recap — no change to earlier guidance)

Trino and Spark/EMR stay primary; finish P2 and P6 first (see `RECOMMENDATIONS.md` Part 0/1). A
scoped Rust/DataFusion compaction component is legitimate longer-term — Netflix itself moved this
direction once JVM overhead was empirically their bottleneck — but only once profiling on the
finished Python/Trino/Spark path actually justifies it, not speculatively.

### 1.4 Concrete stack decisions (committed, not optional)

| Layer | Decision | Rationale |
|---|---|---|
| API | FastAPI (unchanged) | Already correct; no prior art suggests otherwise. |
| State | Postgres + SQLAlchemy/Alembic (unchanged) | Same. |
| Event bus | **Redis Streams + consumer groups** (new use of an existing dependency) | Avoids adding Kafka/SQS-equivalent infra before there's proven need at Cryo's current scale. |
| Fast-path trigger | New `cryo/events/watcher.py` polling hot tables' `current-snapshot-id` | Matches Netflix's event-driven Autotune pattern within the constraints of an external, third-party catalog. |
| Slow-path trigger | Existing scanner CronJob (unchanged) | Reliability backstop, matches OpenHouse/Amoro/Floe's proven baseline. |
| Execution | Trino + Spark/EMR (unchanged); optional future scoped Rust/DataFusion component | See 1.3. |
| Deployment | K8s + Helm (unchanged) | Already correct. |
| AI/agent serving | New lightweight `cryo-mcp` process (Python, official `mcp` SDK), calling into the existing FastAPI/DB, not a new stack | See Part 2. |

### 1.5 The execution-engine layer: build a native Rust engine AND stay interoperable

**Decision: build a native Rust/DataFusion engine (`cryo-engine`) as a first-class peer to Trino
and Spark — not a replacement, and not a fallback.** All three sit behind the
`MaintenanceEngine` Protocol that already exists at `cryo/engines/base.py`
(`probe_capacity`/`render`/`execute`/`session_setup`), which the Planner is already written
against in an engine-agnostic way. This *is* the interoperability architecture — it already
exists in the codebase, it just needs a third implementation alongside `engines/trino/` and
`engines/spark/`.

**Two small, backward-compatible changes make the existing interface fit a non-SQL engine:**
- `RenderedStatement` currently carries `sql: str`, which is SQL-shaped. Add an optional
  `payload: dict[str, object]` alongside it — Trino/Spark keep using `sql`; the native engine
  renders a structured operation descriptor into `payload` instead of query text. Nothing about
  Trino/Spark changes.
- `probe_capacity()` for the native engine reports local worker slot/CPU/memory capacity instead
  of a cluster's query-queue depth — same method signature, different capacity signal.

**Why native-first, not native-as-fallback:** This isn't speculative — it's what LakeOps built as
their entire commercial product, and what Netflix built internally specifically because JVM/Spark
overhead was their actual measured bottleneck for compaction. A single static binary running
DataFusion + iceberg-rust genuinely outperforms spinning up a JVM/Spark job for the common case —
most fleets' long tail is small-to-medium tables. It's also a real adoption differentiator: an org
with no existing Trino/Spark footprint gets working maintenance with zero cluster to stand up.

**Why Trino/Spark stay first-class, not "legacy support":** this is the actual interoperability
requirement — orgs with an existing Trino/Spark investment (sunk cost, ops muscle, an established
security/network perimeter) want maintenance routed through infrastructure they already run and
trust, not a new execution path forced on them. This is already the shape of Cryo's
preset/rule-based engine routing (e.g. "tables > 100GB → Spark"); the same rule engine just gains
a third target, `cryo-native`.

**The distribution answer — reuse Cryo's own chunking instead of fighting DataFusion's single-node
model.** The natural objection: "DataFusion is single-node — how does it handle petabyte tables
the way a Spark cluster does?" It doesn't need to, because Cryo already solves distribution one
layer up. `planner/chunking.py::greedy_pack` already breaks a table's maintenance into
independent, GB-budgeted, partition-aware chunks specifically so they parallelize across the
Worker pool under Governor admission control. Today those chunks become separate Trino/Spark
statements on separate workers; the same chunks give the native engine "distributed" scale for
free — each chunk is one `cryo-engine` subprocess invocation dispatched to a worker, running fully
in parallel with other chunks. DataFusion only ever needs to be fast *within one chunk*, which is
exactly its proven strength (vectorized, Arrow-native, no GC). Petabyte scale becomes "run more
chunks across more Worker replicas" — a problem the Governor already solves — not a problem the
query engine has to solve alone.

**Process boundary and protocol (v1: subprocess, not gRPC).** `cryo-engine` is a single static Rust
binary; the Worker invokes it per chunk with a JSON operation descriptor on stdin, gets a JSON
result on stdout/exit code, and — to feed the existing live-run console — tails JSONL progress
lines the binary writes as it works (files rewritten so far, bytes processed), republished over
the same WebSocket the Trino/Spark paths already use. No new infrastructure dependency, and a
native-engine crash can't take down the Worker process. Revisit gRPC only if `cryo-engine` ever
needs to run as a remote, shared service rather than a local subprocess next to each Worker (e.g.
a future multi-tenant deployment mode) — not needed now.

**Interoperability guarantee: golden-test parity across all three engines.** Trino and Spark
renderers already have golden tests per `todo.md`. Extend the same pattern: one `MaintenancePlan`
fixture, rendered/executed through Trino, Spark, and the native engine, asserted to produce
equivalent results (file counts, bytes reclaimed) on the same seeded fixtures. This is what
actually proves "swap in your own engine or use ours," not just a claim about the architecture.

**Scope of the native engine (v1) — same four operations Trino/Spark already support, no scope
creep:** `rewrite_data_files` (binpack + sort), `expire_snapshots`, `remove_orphan_files`,
`rewrite_manifests`, via `iceberg-rust` + DataFusion + `object_store` (S3/GCS/Azure), in a new
`cryo/engines/native/` adapter mirroring the shape of `engines/trino/` and `engines/spark/`.

**Sequencing:** build `cryo/engines/native/` in parallel with finishing Trino execution (P2),
using Trino's `render`/`execute` implementation as the reference for "done" against the
`MaintenanceEngine` Protocol — don't block one on the other. Both converge on the same golden-test
fixtures.

---

## Part 2 — The AI layer: grounded in proven precedent, not speculation

Every feature below is tied to a specific, verified real-world precedent. Where the precedent is
thin or unproven, it's explicitly called out in 2.3 as something we are *not* building yet.

### 2.1 Cryo Copilot — natural language over table/fleet metadata

**Precedent:** Databricks Genie and Snowflake Cortex Analyst are the two proven production systems
for "ask your lakehouse a question in English." Neither is zero-shot LLM-over-raw-schema — both
depend on a **curated semantic layer**: Genie needs hand-written table/column descriptions,
example queries, and defined business metrics per "Genie Space"; Cortex Analyst grounds every
answer in a semantic-model YAML plus a **Verified Query Repository** (curated question→SQL pairs,
retrieved and reused for similar future questions — an explicit retrieval/few-shot loop, not
fine-tuning). Both vendors report that failures are almost always semantic-layer coverage gaps,
not model failures.

**What we build, mirroring that shape exactly:**
- A curated semantic layer over Cryo's *own* Postgres metadata store (`tables`, `table_stats`,
  `table_cost`, `audit_log`, `runs`/`jobs`) — not over raw Iceberg data. Descriptions and a small
  library of "verified questions" (e.g., "which tables need attention," "what's our reclaimable
  storage this month," "why did table X's grade drop") ship as defaults, extensible per
  deployment.
- The Copilot only ever queries Cryo's own metadata warehouse, never generates arbitrary SQL
  against production Iceberg tables. This keeps blast radius near zero and sidesteps the exact
  failure mode both Genie and Cortex Analyst are built to avoid.

### 2.2 cryo-mcp — an agent tool surface

**Precedent:** MCP adoption in data infra is real and already has a clear shape. `dbt-mcp`
(official, dbt Labs) ships a `get_model_health` tool (last run status, tests, freshness).
Snowflake Labs' official MCP server exposes Cortex Analyst/Search, read-only-flagged SQL
execution, and object management. Ryft's own `iceberg-mcp` (real repo, though stale since April
2025) exposes five read-only tools (`get_namespaces`, `get_iceberg_tables`, `get_table_schema`,
`get_table_properties`, `get_table_partitions`). LakeOps' "agentic AI" marketing, by contrast, has
**no public repo or GitHub org behind it at all** — treat it as unverifiable and don't use it as a
design reference.

**Concrete tool surface to build** (read-only first, exactly like dbt-mcp/Ryft):

| Tool | Maps to | Read/Write |
|---|---|---|
| `get_table_health(table)` | existing health score/grade | read |
| `list_stale_tables(threshold)` | health/grade query | read |
| `list_compaction_backlog()` | `needs_compaction` + priority | read |
| `get_table_schema(table)` / `get_snapshot_history(table)` | existing scanner data | read |
| `explain_health_change(table, since)` | see 2.3 | read |
| `simulate_preset(table, preset)` | existing `/maintenance/preview` endpoint | read |
| `run_preset(table, preset)` | existing `/maintenance/run` endpoint | **write, gated** behind explicit confirmation/role scope |

The last tool is the only write path, and it reuses the existing preview/run endpoints — the MCP
server adds no new execution logic, only a typed interface onto what already exists.

### 2.3 Explainable health-change narration

**Precedent:** Datadog's Bits AI SRE and Honeycomb's Query Assistant are the proven examples of
LLMs doing operational reasoning (not just chat) in production observability. The specific
engineering lesson from Datadog's own writeup: naive "dump all telemetry into the prompt" degrades
quality; causal, hypothesis-driven tool-calling over structured signals works better than
context-stuffing raw logs.

**Why this is lower-risk for Cryo than for a typical observability RCA tool:** our signals are
already exact and deterministic — `scanner/health.py` has explicit, documented thresholds
(`delete_file_pct_warn/crit`, `manifest_count_warn/crit`, etc.) and full history in
`table_stats_history`. The LLM's job is narrow: given a deterministic before/after diff (which
threshold crossed, what the values were), narrate it in plain English and point at the specific
operation/preset that addresses it. This is closer to "explain a diff" than "infer causation from
noisy telemetry," which is exactly the easier, lower-hallucination-risk end of what's already
proven to work.

### 2.4 Iceberg-native statistical anomaly detection

**Precedent check (important negative result):** Monte Carlo, Bigeye, and Anomalo are real,
proven ML/statistical anomaly detectors for data quality — but all three monitor *downstream* data
quality signals (row counts, freshness, null rates, schema drift), not Iceberg-internal
maintenance signals. None of them score commit velocity, delete-file growth rate, manifest bloat,
or compaction backlog. **This is confirmed, genuine whitespace** — a different signal category
from classic DQ observability, not something already crowded.

**What we build, deliberately not an LLM:** classical time-series anomaly detection (EWMA/z-score)
over `table_stats_history` — commit velocity, delete-file growth, manifest count growth, cost
trajectory. Cheap, explainable, no hallucination risk. Use ML only where it earns the complexity;
a z-score suffices here and is more trustworthy than reaching for an LLM.

### 2.5 What we explicitly do NOT build (avoid hype)

- **No zero-shot NL-to-SQL against live Iceberg tables.** This is exactly the failure mode Genie
  and Cortex Analyst are engineered to avoid via curation — skipping the semantic-layer step to
  ship faster would reproduce their known failure mode, not their success.
- **No "LLM auto-approves schema/DDL changes."** No credible production precedent exists yet
  (academic-stage only, e.g. the VLDB "Horizon" line of work) — human sign-off stays required.
- **No "AI-powered black-box autopilot" positioning.** This directly contradicts the transparency
  differentiator already established for Cryo's health scoring, and research confirms it's exactly
  the marketing pattern that erodes practitioner trust across every competitor that uses it.
- **Do not use LakeOps as an AI/agentic precedent for anything** — verified no public code or
  GitHub org exists behind their marketing claims.

---

## Part 3 — Sequencing addendum

This slots into the phases already laid out in `docs/RECOMMENDATIONS.md` Part 3:

- **Alongside Phase A/B** (finishing P2/P3/P6, cost dashboard): stand up `cryo/events/` as a small,
  isolated addition — it doesn't block or get blocked by the engine-completion work, and the
  hot-table flag it needs is the same streaming/commit-velocity detection already planned for
  Phase B.
- **After Phase B**, once the metadata/health signals it narrates are stable: build
  `explain_health_change` (2.3) and the anomaly detector (2.4) — both are read-only additions on
  top of data that already exists by that point.
- **After the MCP tool surface's read paths are proven useful internally**: add `cryo-mcp` (2.2)
  and Cryo Copilot (2.1) as a pair, since Copilot's "verified questions" library and MCP's
  read-only tools are largely the same underlying queries exposed two different ways.
- **Long-term/optional**: the write-gated `run_preset` MCP tool, once there's enough operational
  trust in the read-only surface to justify agent-triggered execution.

---

## References (checked 2026-07-03)

- [Netflix Autotune/Polaris/Janitors](https://vutr.substack.com/p/i-spent-4-hours-learning-how-netflix), [dev.to writeup](https://dev.to/jonisar/netflix-intelligent-lakehouse-solves-iceberg-maintenance-you-can-easily-too-5a84)
- [LinkedIn OpenHouse architecture](https://github.com/linkedin/openhouse/blob/main/ARCHITECTURE.md)
- [Apache Amoro self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)
- [Lakekeeper](https://github.com/lakekeeper/lakekeeper), [Apache Polaris](https://polaris.apache.org/), [Floe + Polaris policy-driven maintenance](https://polaris.apache.org/blog/2026/02/04/floe-and-apache-polaris-policy-driven-table-maintenance-for-apache-iceberg/)
- [Databricks Genie docs](https://docs.databricks.com/aws/en/genie/), [best practices](https://docs.databricks.com/aws/en/genie/best-practices)
- [Snowflake Cortex Analyst — Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
- [Monte Carlo — Iceberg best practices](https://www.montecarlodata.com/blog-apache-iceberg-best-practices/)
- [dbt-mcp](https://github.com/dbt-labs/dbt-mcp), [Snowflake Labs MCP](https://github.com/Snowflake-Labs/mcp), [Ryft iceberg-mcp](https://github.com/ryft-io/iceberg-mcp)
- [Datadog Bits AI SRE](https://www.datadoghq.com/blog/building-bits-ai-sre/), [Honeycomb AI agents](https://www.honeycomb.io/technologies/ai-agents)
- Internal: [`docs/RECOMMENDATIONS.md`](./RECOMMENDATIONS.md), [`docs/PERFORMANCE_AND_OPENNESS.md`](./PERFORMANCE_AND_OPENNESS.md), [`todo.md`](../todo.md)
