# Cryo — Master Execution Plan

> Written 2026-07-03. **This file is a handoff document.** It exists so that another AI agent (or
> a human) can pick up this repo with no other context and execute, in order, everything decided
> during the architecture/feature/AI strategy session that produced the three docs it references.
> It does not re-argue any decision — those are already made and cited below. It only sequences
> the work, points at exact files, and defines "done" for each step.

---

## 0. Read this first, in this order

Before writing any code, read these — in order — so decisions aren't re-litigated or contradicted:

1. [`../agents.md`](../agents.md) — hard requirements and repo conventions. **Do not violate
   these** (no Airflow; sources ≠ destinations; Planner emits no SQL strings — engine-specific
   *renderers* emit SQL or, for the new native engine, a structured payload; naming conventions).
2. [`../plan.md`](../plan.md), [`../hld.md`](../hld.md), [`../lld.md`](../lld.md) — original
   vision, architecture, and schemas. Still authoritative for anything not superseded below.
3. [`../todo.md`](../todo.md) — the phased P0–P7 checklist. **Keep it updated** as items in this
   plan overlap with or complete todo.md items.
4. [`current-state.md`](./current-state.md) — what's actually implemented today vs. planned.
   **Keep it updated** as you land work.
5. [`RECOMMENDATIONS.md`](./RECOMMENDATIONS.md) — the feature backlog, grounded in what's already
   built vs. real gaps, with a suggested phase sequencing.
6. [`PERFORMANCE_AND_OPENNESS.md`](./PERFORMANCE_AND_OPENNESS.md) — performance deep-dive
   (corrected/fact-checked) and open-source governance notes.
7. [`ARCHITECTURE_AND_AI_STRATEGY.md`](./ARCHITECTURE_AND_AI_STRATEGY.md) — the decided control-plane
   architecture (event fast-path), the decided execution-engine architecture (native Rust engine +
   Trino/Spark interoperability), and the decided AI layer.

**This plan does not duplicate the "why" from those docs — it only says "what, in what order, and
how do you know it's done."** If a task here seems to lack rationale, the rationale is in one of
the docs above; go read it rather than guessing or re-deciding.

---

## 1. Ground rules for whoever executes this plan

- **Don't skip Phase 1.** Every later phase assumes the core execution path actually works
  end-to-end. Building features or the native engine on top of a non-functional Trino/Worker/
  Governor loop just compounds the gap.
- **Follow existing conventions** (`agents.md` §4): ruff + mypy + pytest must pass; layering is
  `api → repositories → db`, `worker → planner + engines`, `governor → engines (probe) + queue`;
  domain/planner never import db/engine clients; naming mirrors `lld.md` (`MaintenancePlan`,
  `Operation`, `PartitionPredicate`, `OperationType`, `EngineType`, `Mode`) — don't invent parallel
  vocabularies.
- **Golden tests are not optional.** Trino and Spark renderers already have golden tests
  (`todo.md` P2/P6). Every new engine or renderer path added in this plan must extend the same
  pattern — same `MaintenancePlan` fixture in, equivalent result out — not a new, separate test
  style.
- **Update docs as you go, not at the end:** tick boxes in `todo.md` and this file, keep
  `current-state.md`'s "Implemented / Partial / Planned" sections accurate. A future agent (or
  this same one, later) should be able to trust these files without re-reading all the code.
- **No speculative scope.** Each phase below lists exactly what to build. Don't add
  configurability, abstractions, or "while I'm here" extras beyond what's specified — if a good
  idea shows up mid-implementation that isn't in this plan, add it as a new item at the bottom of
  the relevant doc's backlog, don't build it inline.
- **Definition of done, every phase:** `make lint format typecheck test` pass; any new/changed
  engine or renderer has golden-test coverage; `todo.md` and `current-state.md` reflect the change;
  this file's checkboxes for the phase are ticked.

---

## 2. Execution order

### Phase 1 — Close the core plumbing gap (prerequisite for everything else)

**Why:** per `RECOMMENDATIONS.md` Part 0, none of the performance or feature work matters if a
preset can't actually run against a real engine end-to-end yet.

- [ ] `cryo/engines/trino/client.py` — per-coordinator connection (HTTPS + BasicAuth).
- [ ] `cryo/engines/trino/load.py` — `system.runtime.queries` OPTIMIZE probe (parity with
      reference implementation per `agents.md` §2).
- [ ] `cryo/engines/trino/engine.py` — implement `MaintenanceEngine.execute`; capture query id +
      errors.
- [ ] Finish `cryo/governor/governor.py` — wave loop, round-robin replicas, per-table lock
      handoff, dispatch, reap, finalize run summary (currently `[~]` in `todo.md`).
- [ ] Finish `cryo/worker/worker.py` — claim job → load stats → plan → engine execute → audit →
      counters (currently `[~]`).
- [ ] Finish `cryo/scheduler/scheduler.py` — cron eval → create run + jobs → enqueue; cooldown
      pre-filter; priority order; leader-elected (currently `[~]`).
- [ ] `cryo_spark_runner.py` — PySpark entrypoint executing CALL statements with Iceberg
      SparkSession.
- [ ] `cryo/engines/spark/emr.py` — EMR Serverless submit/poll/logs (boto3); capacity = slot cap
      − in-flight.
- [ ] `cryo/engines/spark/engine.py` — implement `MaintenanceEngine`; wire into worker + governor.
- [ ] Governor EMR slot governance; preset rule `size_gb > 100 → spark-large`.
- [ ] CI pipeline: lint + typecheck + unit tests on push (currently missing entirely).

**Acceptance criteria:** a preset can be assigned to a table via the API, run end-to-end against
the local Trino stack *and* the local Spark stack, produce an audit row, and reduce file count on
a seeded fixture table (`make smoke-compact` and the Spark equivalent both pass). Routing test:
large table → Spark, small → Trino.

---

### Phase 2 — Finish the cost dashboard + cheap health-signal extensions

**Why:** per `RECOMMENDATIONS.md` Part 2, the hard data work for cost accounting is already done
and persisted (`table_cost`, `table_cost_history`) — only wiring remains, making this the highest
ROI-per-hour item in the backlog. The health-signal extensions listed alongside it reuse data the
scanner already collects.

- [ ] `/tables/{id}/cost` and `/dashboards/cost` API endpoints (plan-accurate `SavingsProjection`
      on `/maintenance/preview`; cost summary fields in the predicate grammar for filter/sort by
      savings; `/scans` accepts `cost_full_scan`).
- [ ] Cost & Savings dashboard UI per the existing spec: [`ui-cost-savings.md`](./ui-cost-savings.md)
      (current vs. projected waterfall, savings by op/schema, top opportunities → select-all → Run,
      precision badge; Table Detail cost breakdown; PlanPreview/RunConsole show projected +
      realized savings).
- [ ] Surface `metadata_bytes / data_bytes` ratio as an explicit health metric (data already
      computed by `account_metadata_cost`; just needs to reach `scanner/health.py` scoring and the
      dashboard).
- [ ] Streaming/commit-velocity detection: derive commits/day from snapshot-count deltas between
      consecutive `table_stats_history` rows; add an `is_streaming` flag; tighten scan/compaction
      cadence for flagged tables.
- [ ] V2/V3 format-version detection: surface `format-version` (already read in `analyze_table`)
      and flag V2 tables with high `delete_file_pct` as V3-upgrade candidates (cite the AWS EMR
      benchmark in the UI copy as directional, not guaranteed — see `RECOMMENDATIONS.md`
      verification notes).
- [ ] Standalone "rewrite manifests only" quick-fix action, separate from full optimize (cheap —
      `OPTIMIZE_MANIFESTS` is already a defined `OperationType`).

**Acceptance criteria:** cost dashboard shows real current-vs-projected numbers on the local
fixture stack; health score/grade reflects metadata ratio, streaming flag, and V2/V3 status;
manifest-only rewrite is triggerable independent of a full optimize run.

---

### Phase 3 — Operations & safety features

**Why:** these are the real gaps identified against the current planner/governor, per
`RECOMMENDATIONS.md` Part 2.

- [ ] Hot/cold partition tracking: extend partition selection in `cryo/planner/` with a "no writes
      in N hours" filter (derivable from partition-level snapshot data already scanned), so
      compaction skips actively-written partitions once real writers are active post-Phase 1.
- [ ] Delete-ratio-triggered compaction mode: trigger an optimize run when `delete_file_pct`
      crosses a threshold even if small-file count alone wouldn't justify it.
- [ ] User-facing sequencing guardrail: the planner already enforces correct internal ordering
      (expire → orphan → compact → manifests, in `planner/planner.py`) — add an API/UI-level
      check that blocks/warns if a user manually triggers ops out of order via ad-hoc runs.

**Acceptance criteria:** a table with writes in the last N hours is excluded from a compaction
plan; a table crossing the delete-ratio threshold gets a compaction recommendation independent of
file count; manually requesting orphan-removal before expire-snapshots via the API surfaces a
warning.

---

### Phase 4 — Event fast-path (control-plane architecture)

**Why:** per `ARCHITECTURE_AND_AI_STRATEGY.md` §1.2 — adds Netflix-style event-driven reaction
alongside the existing cron sweep, without replacing it.

- [ ] New module `cryo/events/watcher.py` — polls `current-snapshot-id` at a tight interval for
      tables flagged "hot" (reuses the `is_streaming` flag from Phase 2); publishes a
      `TableCommitted` event to a Redis Stream on change.
- [ ] New module `cryo/events/consumer.py` — Scanner and Governor subscribe as a Redis Streams
      consumer group; on `TableCommitted`, opportunistically re-score just that table and
      re-evaluate scheduling instead of waiting for the next full sweep.
- [ ] The existing CronJob full-fleet scan is left unchanged as the slow-path/reliability
      backstop.

**Acceptance criteria:** a commit to a flagged "hot" table triggers a re-score within the watcher's
poll interval, visible in the table's health/audit timeline, without waiting for the next
scheduled CronJob run; disabling the watcher doesn't break anything — the cron sweep still
catches everything within its normal cadence.

---

### Phase 5 — Native Rust execution engine (`cryo-engine`)

**Why:** per `ARCHITECTURE_AND_AI_STRATEGY.md` §1.5 — a first-class peer engine, not a
replacement for Trino/Spark. The Rust crate development can proceed in parallel with Phase 1's
Trino work, but integration testing requires Phase 1's orchestration (Worker/Governor/Scheduler)
to be complete. Use Trino's `render`/`execute` implementation as the reference for what "done"
means against the `MaintenanceEngine` Protocol.

- [ ] Add optional `payload: dict[str, object]` field to `RenderedStatement`
      (`cryo/engines/base.py`) alongside the existing `sql: str` — backward compatible, Trino/Spark
      keep using `sql` unchanged.
- [ ] Add `EngineType.NATIVE` (or `CRYO_NATIVE`) to `cryo/domain/enums.py`.
- [ ] New Rust crate `cryo-engine`: `iceberg-rust` + `DataFusion` + `object_store` (S3/GCS/Azure).
      Implements exactly the four operations Trino/Spark already support — no scope creep:
  - [ ] `rewrite_data_files` (binpack + sort)
  - [ ] `expire_snapshots`
  - [ ] `remove_orphan_files`
  - [ ] `rewrite_manifests`
- [ ] Protocol: subprocess, JSON operation descriptor on stdin, JSON result on stdout/exit code,
      JSONL progress lines tailed by the Worker and republished over the existing run-console
      WebSocket. No gRPC in v1.
- [ ] New Python adapter `cryo/engines/native/engine.py` implementing `MaintenanceEngine`,
      mirroring the shape of `engines/trino/` and `engines/spark/`.
- [ ] Preset/rule routing: extend the existing engine-selection rules (`size_gb > 100 → spark-large`
      style) to also allow routing to `cryo-native`.
- [ ] Golden-test parity: the same `MaintenancePlan` fixtures used for Trino/Spark golden tests,
      run through the native engine, asserted to produce equivalent file-count/bytes-reclaimed
      results on the seeded fixture tables.

**Acceptance criteria:** a preset can be routed to `cryo-native` end-to-end on the local fixture
stack with no Trino/Spark cluster running at all; golden tests show equivalent results across all
three engines on the same fixtures; a deliberate crash inside the Rust binary does not crash the
Worker process.

---

### Phase 6 — Compliance & governance features

**Why:** per `RECOMMENDATIONS.md` Part 2 — real gaps, genuine differentiators, verified against
real competitor features (Ryft).

- [ ] Compliance/GDPR-CCPA deletion tracking: track tables with soft-deletes pending a snapshot-
      expiry cutoff (reuses `expire_snapshots` as a first-class operation and existing
      `audit_log`); surface "N pending hard deletions, blocked until snapshot expiry after
      &lt;date&gt;."
- [ ] Catalog latency monitoring: add timing around existing catalog calls in `cryo/sources/`;
      surface P50/P99 per catalog in dashboards; make the alert threshold configurable (do not
      hardcode an unsourced "500ms" as an industry standard — see verification notes in
      `RECOMMENDATIONS.md`).
- [ ] Disaster recovery / named backup snapshots: allow tagging specific snapshots as protected so
      `expire_snapshots` skips them; small addition to the runs/audit schema.

**Acceptance criteria:** a table with pending soft-deletes shows a compliance status; catalog P50/
P99 is visible per configured catalog; a tagged "protected" snapshot survives an expire-snapshots
run that would otherwise have deleted it.

---

### Phase 7 — AI layer

**Why:** per `ARCHITECTURE_AND_AI_STRATEGY.md` Part 2 and its own sequencing note — build in this
order specifically because each step depends on the data/signals the previous step establishes.

- [ ] **Anomaly detection first, no LLM dependency:** classical EWMA/z-score time-series detection
      over `table_stats_history` — commit velocity, delete-file growth, manifest count growth,
      cost trajectory. Confirmed genuine whitespace vs. Monte Carlo/Bigeye/Anomalo (none of them
      watch Iceberg-internal signals) — see verification notes.
- [ ] **`explain_health_change(table, since)`:** given a deterministic before/after diff from
      `scanner/health.py` thresholds and `table_stats_history`, narrate it in plain English and
      point at the specific operation/preset that addresses it. Scope is narrating an exact diff,
      not inferring causation from noisy telemetry.
- [ ] **`cryo-mcp` server**, read-only tools first: `get_table_health`, `list_stale_tables`,
      `list_compaction_backlog`, `get_table_schema`, `get_snapshot_history`,
      `explain_health_change`, `simulate_preset` (wraps the existing `/maintenance/preview`
      endpoint). All of these wrap existing endpoints/data — no new execution logic.
- [ ] **Cryo Copilot:** curated semantic layer + a small "verified questions" library over Cryo's
      own Postgres metadata store (never freeform SQL against live Iceberg tables) — mirrors the
      Databricks Genie / Snowflake Cortex Analyst pattern exactly.
- [ ] **Gated `run_preset` MCP tool** (write path), last, once there's operational trust in the
      read-only surface — reuses the existing `/maintenance/run` endpoint, requires explicit
      confirmation/role scope, no new execution logic.

**Explicitly do not build** (see `ARCHITECTURE_AND_AI_STRATEGY.md` §2.5): zero-shot NL-to-SQL
against live Iceberg tables; LLM-auto-approves-DDL/schema-changes; "AI-powered black-box autopilot"
positioning of any kind.

**Acceptance criteria:** anomaly detector flags a synthetic spike in a fixture table's delete-file
growth rate; `cryo-mcp`'s read-only tools are callable by an MCP client and return real data from
the local stack; Copilot correctly answers at least the "verified questions" shipped by default
without hitting live Iceberg tables.

---

### Phase 8 — Long-term / optional

Only pursue these once Phases 1–7 are stable and there's a concrete driver (a real user request,
external contributor interest) — don't build ahead of need.

- [ ] Multi-engine query routing (cost/latency-aware routing across Trino/Spark/native).
- [ ] Data tiering / lifecycle policies (move cold partitions to cheaper storage classes).
- [ ] Apache-style open governance formalization — Cryo is already open source under
      `shelf-project/Cryo` (Apache 2.0). Current GitHub-first governance is sufficient for the
      pre-v1 phase; formalize contributor agreements, PMC structure, etc. only when external
      contributor volume justifies the overhead. See `PERFORMANCE_AND_OPENNESS.md` Part 4.

---

## 3. Quick reference — doc-to-phase map

| Phase | Primary doc(s) |
|---|---|
| 1 (core plumbing) | `../todo.md` P2/P3/P6, `RECOMMENDATIONS.md` Part 0 |
| 2 (cost + cheap health signals) | `RECOMMENDATIONS.md` Part 2, `ui-cost-savings.md` |
| 3 (ops & safety) | `RECOMMENDATIONS.md` Part 2 |
| 4 (event fast-path) | `ARCHITECTURE_AND_AI_STRATEGY.md` §1.1–1.2 |
| 5 (native engine) | `ARCHITECTURE_AND_AI_STRATEGY.md` §1.5, `PERFORMANCE_AND_OPENNESS.md` Part 1 |
| 6 (compliance/governance features) | `RECOMMENDATIONS.md` Part 2 |
| 7 (AI layer) | `ARCHITECTURE_AND_AI_STRATEGY.md` Part 2 |
| 8 (long-term) | `RECOMMENDATIONS.md` Part 2 (⏳ Later), `PERFORMANCE_AND_OPENNESS.md` Part 4 |

---

## 4. If something in this plan conflicts with the code you find

The codebase is the ground truth for *what currently exists*; this plan and its referenced docs
are the ground truth for *what to build next and why*. If you find the code has moved on since
2026-07-03 (new modules, renamed files, completed items this plan assumes are pending), trust the
code, update `current-state.md` and this file to match reality, and proceed with the intent of the
plan rather than its exact file-path assumptions.
