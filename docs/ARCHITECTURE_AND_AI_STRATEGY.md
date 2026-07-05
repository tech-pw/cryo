# Cryo: Architecture & AI Strategy — Decided

> Written 2026-07-03; extended 2026-07-05 with §2.5/2.6 (embedding-based intelligence and a
> research-paper-grounded "cutting edge" track). This is a **decision** document, not an options
> survey — the point was to pick one architecture and one AI approach, grounded in real prior art,
> and commit to implementing them. It extends `docs/RECOMMENDATIONS.md` (what to build) with *how*
> to build it and *what the AI layer actually is*.

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
                     │   Full-datalake sweep (slow path, unchanged) │
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
most datalakes' long tail is small-to-medium tables. It's also a real adoption differentiator: an org
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

### 2.1 Cryo Copilot — natural language over table/datalake metadata

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

### 2.5 Embedding-based fleet intelligence

**Precedent, split two ways** (checked 2026-07-05): storing embeddings *inside* Iceberg tables is
a dead end. A real proposal for a native vector type (`apache/iceberg#12636`) was closed as "not
planned," and the industry has converged on the opposite pattern instead — Iceberg holds governed
structured data, a separate vector-optimized layer (Lance format, or a synced external index) holds
embeddings for ANN search. This is benchmark-backed, not just caution: Parquet's page-level layout
is a documented poor fit for high-dimensional random-vector access. **Cryo should never try to
become a vector store, and shouldn't build vector-native maintenance features.**

Embedding-based intelligence *about* Cryo's own metadata is a different, validated story:
- Databricks Unity Catalog's Data Classification (GA ~May 2026) uses an LLM + few-shot embeddings
  over column name/type/comment to flag likely-PII columns — not regex. Real, shipped pattern.
- OpenMetadata ships real embedding-based natural-language search over catalog metadata (pluggable
  Bedrock/OpenAI/HuggingFace embeddings); DataHub has the same thing, earlier-stage.
- The one genuine, unclaimed whitespace across every source checked: **fleet-wide "find
  similar/related tables" via embeddings.** This remains research-stage everywhere (2025 academic
  work like Starmie/TabEmb/"Universal Tabular Embeddings") — nobody ships it as a production
  feature at real-fleet scale.

**What we build:**
1. **Embedding-based PII/sensitive-column classification** — extends the governance-gating already
   planned in 2.3/`RECOMMENDATIONS.md`: embed column name + type + sample values, compare against a
   small library of known-PII embeddings, flag likely-PII columns that aren't already tagged,
   feeding the same "don't compact/rewrite untagged sensitive columns" gate.
2. **Embedding fallback for Cryo Copilot** — when a question doesn't match the curated "verified
   questions" library (2.1), fall back to an embedding search over table/column descriptions
   instead of failing outright. Still never generates SQL against live Iceberg tables — same
   blast-radius guarantee as 2.1.
3. **Fleet-wide table/column similarity search (the real differentiator)** — embed table/column
   names, descriptions, and sample stats fleet-wide; surface "these N tables are >90% similar —
   likely the same entity, possible consolidation opportunity." Directly useful in large,
   inconsistently-named Iceberg fleets (thousands of tables, no naming convention), and — per the
   research — not productized by any competitor yet. Atlan's "Context Lakehouse" is the closest
   analog (Iceberg-native storage + vector-native embeddings per asset, real enterprise customers)
   but it's a broad governance platform, not a maintenance tool, and no independent technical
   teardown of their approach exists — this gets built from the published academic pattern, not by
   reverse-engineering a competitor.

### 2.6 Cutting-edge track: what recent research actually supports (and what it doesn't)

Checked 2026-07-05 against real papers, not vendor blogs — split into "ready to build" and "don't
bet production reliability on yet."

**Ready to build:**
- **Bayesian-optimization / contextual-bandit tuning of Governor thresholds** — not full
  "self-driving" config tuning. OtterTune (CMU's commercialized self-driving-DB config tuner)
  **shut down in June 2024**, a real signal that full autonomous tuning over hundreds of knobs
  doesn't hold up commercially. But Cryo's Governor only has ~3–5 well-understood knobs
  (concurrency caps, cooldown, adaptive budget) — a much narrower, tractable problem, structurally
  the same as what OtterTune did at a fraction of the scope. A constrained BO or contextual-bandit
  loop learning per-table-class thresholds from realized outcomes, with a heuristic fallback under
  low confidence, is a credible upgrade to `governor/admission.py`'s current static constants.
- **A learned cost model, replacing the heuristic $/GB savings projection** — Amazon's **Stage**
  (SIGMOD 2024, deployed in Redshift) is the reference architecture: a lightweight per-instance
  model (XGBoost-class) with Bayesian uncertainty, plus a transferable global model, trained on
  realized outcomes (predicted vs. actual). Cryo already has the missing piece this needs —
  `RECOMMENDATIONS.md`'s planned "realized savings" capture (predicted vs. `actual_bytes_freed`) is
  exactly the training signal. Add conformal-prediction-style confidence bands so Cryo falls back
  to the current heuristic whenever the model isn't confident, rather than trusting a black box.
- **Adaptive compaction scheduling as a genuine research-backed differentiator** — LinkedIn's
  **AutoComp** (SIGMOD 2025, deployed via OpenHouse — an Iceberg-based control plane) is the single
  most directly relevant paper found: automated compaction scheduling for exactly this table-format
  family, reporting a real 44% reduction in sub-128MB file counts in production. This isn't an
  established subfield yet — AutoComp is a lone recent entry — meaning genuine whitespace to build
  the second real implementation of this idea, informed by a published, production-validated
  precedent instead of starting from scratch.
- **TabPFN for few-shot table health classification** (Nature, 2025) — a tabular foundation model
  pretrained once on synthetic data, doing in-context Bayesian inference with no per-deployment
  training. Feed it a fleet's table metadata signatures (file count, delete ratio, manifest count,
  schema complexity) with a handful of labeled examples ("needed emergency compaction" / "didn't")
  and get few-shot classification — a low-effort way to predict which tables need attention before
  thresholds are crossed, without training a bespoke model per Cryo deployment.

**Interesting, but don't bet production reliability on yet:**
- **Full RL-based scheduling** (Decima/Park-style) for Governor admission control. Real and
  well-published (Decima beat heuristic Spark schedulers by 21%+), but a 2026 survey confirms
  sim-to-real transfer remains an open, unsolved problem for RL broadly, and RL policies are
  notoriously hard to audit when they misbehave — the wrong tradeoff for something gating
  compaction jobs on customer data. A **contextual bandit** (simpler, safer, degrades gracefully to
  the heuristic under uncertainty) is the credible middle ground — not full RL.
- **Time-series foundation models (Chronos-2, TimesFM, Moirai) for forecasting per-table health
  trajectories.** Mature for dense, regular series (retail/energy demand) — but a 2025 paper
  specifically stress-testing them on observability-style metrics found they degrade badly on
  exactly Cryo's data shape: sparse, per-entity, heterogeneous, low-frequency series across
  thousands of distinct tables. Datadog built a bespoke model (Toto) for this exact gap and hasn't
  even open-sourced or deployed it internally yet. Worth a research spike, not a production bet.
- **GNN-based automatic join/lineage discovery** (OmniMatch, VLDB 2025; AutoG; Magneto) — real,
  active research that could plausibly power a "these tables are probably joined" feature without
  relying on Iceberg's often-absent declared foreign keys. Promising, but needs real engineering
  investment (sampling strategy at fleet scale, model serving) before it's shippable — a future
  phase, not now.
- **Causal inference for realized-savings rigor** (Sawmill, SIGMOD 2024) — a real, tractable
  technique for proving compaction *caused* a latency improvement rather than merely coinciding
  with one. Worth adopting once realized-savings tracking has enough data for a diff-in-diff or
  synthetic-control design — premature before that.

### 2.7 What we explicitly do NOT build (avoid hype)

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
- **No vector-native storage or search built into Iceberg tables or Cryo itself** — a real Iceberg
  spec proposal for this was explicitly rejected; the whole ecosystem keeps vectors in a separate
  layer. See 2.5.
- **No full RL-based scheduling and no full "self-driving" whole-system autotuning.** OtterTune's
  2024 shutdown and RL's documented sim-to-real fragility are the cautionary tales — only the
  narrow, well-scoped BO/bandit tuning and learned cost model in 2.6 are credible bets today.

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
- **Alongside Copilot**: the embedding-based PII classifier and Copilot fallback (2.5, items 1–2) —
  both are natural extensions of the semantic layer Copilot already needs.
- **Once realized-savings tracking has real data flowing** (predicted vs. `actual_bytes_freed`):
  the learned cost model (2.6) and, once that's stable, causal-inference-based ROI validation
  (2.6). These depend on having enough outcome history to train/validate against.
- **Once the Governor is running real jobs at some volume**: the BO/bandit threshold tuning (2.6) —
  needs realized admission-control outcomes to learn from, same dependency shape as the cost model.
- **Longer-term, as dedicated research spikes, not committed features yet**: fleet-wide table
  similarity search (2.5, item 3), AutoComp-style adaptive compaction scheduling (2.6), and TabPFN
  health classification (2.6) — each is credible enough to prototype but needs its own validation
  pass against Cryo's real fleet data before being treated as shipped.
- **Long-term/optional**: the write-gated `run_preset` MCP tool, once there's enough operational
  trust in the read-only surface to justify agent-triggered execution.

---

## References (checked 2026-07-03, AI/embeddings + research track added 2026-07-05)

- [Netflix Autotune/Polaris/Janitors](https://vutr.substack.com/p/i-spent-4-hours-learning-how-netflix), [dev.to writeup](https://dev.to/jonisar/netflix-intelligent-lakehouse-solves-iceberg-maintenance-you-can-easily-too-5a84)
- [LinkedIn OpenHouse architecture](https://github.com/linkedin/openhouse/blob/main/ARCHITECTURE.md)
- [Apache Amoro self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)
- [Lakekeeper](https://github.com/lakekeeper/lakekeeper), [Apache Polaris](https://polaris.apache.org/), [Floe + Polaris policy-driven maintenance](https://polaris.apache.org/blog/2026/02/04/floe-and-apache-polaris-policy-driven-table-maintenance-for-apache-iceberg/)
- [Databricks Genie docs](https://docs.databricks.com/aws/en/genie/), [best practices](https://docs.databricks.com/aws/en/genie/best-practices)
- [Snowflake Cortex Analyst — Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
- [Monte Carlo — Iceberg best practices](https://www.montecarlodata.com/blog-apache-iceberg-best-practices/)
- [dbt-mcp](https://github.com/dbt-labs/dbt-mcp), [Snowflake Labs MCP](https://github.com/Snowflake-Labs/mcp), [Ryft iceberg-mcp](https://github.com/ryft-io/iceberg-mcp)
- [Datadog Bits AI SRE](https://www.datadoghq.com/blog/building-bits-ai-sre/), [Honeycomb AI agents](https://www.honeycomb.io/technologies/ai-agents)

**Embeddings/vector storage (2.5):**
- [apache/iceberg#12636 — vector/full-text index proposal, closed "not planned"](https://github.com/apache/iceberg/issues/12636)
- [Databricks Unity Catalog Data Classification](https://www.databricks.com/blog/find-sensitive-data-scale-data-classification-unity-catalog)
- [OpenMetadata semantic search docs](https://docs.open-metadata.org/v1.12.x/deployment/semantic-search), [DataHub semantic search architecture](https://github.com/datahub-project/datahub/blob/master/docs/dev-guides/semantic-search/ARCHITECTURE.md)
- [Atlan Context Lakehouse](https://atlan.com/context-lakehouse/)
- [apache/parquet-format#553 — no native vector logical type](https://github.com/apache/parquet-format/issues/553)

**Cutting-edge research track (2.6):**
- [OtterTune shutdown, June 2024](https://ottertune.com/) — commercialized self-driving-DB tuning; cautionary tale for full-system autotuning
- [Stage — learned cost model deployed in Redshift, SIGMOD 2024](https://arxiv.org/abs/2403.02286)
- [AutoComp — automated compaction for log-structured tables, LinkedIn/OpenHouse, SIGMOD 2025](https://arxiv.org/abs/2504.04186)
- [TabPFN — tabular foundation model, Nature 2025](https://www.nature.com/articles/s41586-024-08328-6), [TabPFN-TS for zero-shot time-series forecasting](https://github.com/PriorLabs/tabpfn-time-series)
- [Decima — RL for cluster scheduling, SIGCOMM 2019](https://dl.acm.org/doi/10.1145/3341302.3342080); [2026 survey on RL sim-to-real transfer](https://arxiv.org/abs/2502.13187)
- ["This Time is Different: An Observability Perspective on Time Series Foundation Models"](https://arxiv.org/pdf/2505.14766); [Datadog Toto](https://www.datadoghq.com/blog/datadog-time-series-foundation-model/)
- [OmniMatch — GNN-based join discovery, VLDB 2025](https://arxiv.org/abs/2403.07653)
- [Sawmill — causal diagnosis from logs, SIGMOD 2024](https://dl.acm.org/doi/10.14778/3705829.3705836)

- Internal: [`docs/RECOMMENDATIONS.md`](./RECOMMENDATIONS.md), [`docs/PERFORMANCE_AND_OPENNESS.md`](./PERFORMANCE_AND_OPENNESS.md), [`todo.md`](../todo.md)
