# Apache Amoro evaluation

> **Status:** completed (2-day time-box, July 2026)
> **Author:** Move 25, `docs/strategy-next-moves-2026-07.md`
> **Verdict up front:** **diverge in the medium term, with one narrow place to *borrow* (the table-level optimizer quota model) and an *integration option* to revisit when streaming-primary workloads show up on the roadmap. Don't integrate Amoro wholesale.**
>
> The native-engine decisions already made in `docs/native-engine-scoping-research.md` §6 still hold; this evaluation sharpens what the §6.8 "an evaluation, not a commitment" caveat was leaving room for. The window for "commit to a from-scratch scheduler Amoro already has" is the only thing that could meaningfully change the execution-layer roadmap.

---

## 1. Why this evaluation exists

Move 25 was time-boxed because Moves 3–4 (K8s-Job dispatch + per-chunk checkpointing) are about to ship well-trodden K8s patterns — *not* a from-scratch scheduler. That makes them safe to build first, but only if we know whether the *long-term* target (decoupled brain / elastic optimizer) is something we should still build or whether Amoro already gives it to us for free.

`docs/native-engine-scoping-research.md` §4.2 puts the structural comparison this way:

> Amoro's brain/elastic-optimizer split is structurally identical to what §5 recommends Cryo build, with a claimed 10× over Spark's `rewriteDataFiles`.

If that's true, integrating or borrowing Amoro would short-circuit ~2–4 sprints of scheduler work. If it isn't, we proceed with the from-scratch plan. This document is the read that decides.

---

## 2. What Amoro actually ships

Source: `https://amoro.apache.org/docs/latest/` and the `self-optimizing` page (cached 2026-07-08). Where I cite a number or behaviour I haven't reproduced myself, I flag it.

### 2.1 Components

| Component | What it does | Notes for us |
|---|---|---|
| **AMS (Amoro Management Service)** | Central brain. Detects/plans optimizing tasks, schedules them to Optimizers, *and* serves as the unified catalog service (REST-compatible). Host a single JVM or a leader-elected cluster. | Equivalent in scope to Cryo's `cryo/governor` + `cryo/scheduler` + Iceberg REST catalog. |
| **Optimizers** | Stateless worker processes (Flink jobs, Spark processes, or local JVM). Each grabs tasks off the queue, compacts, commits. | Comparable to Cryo's eventual K8s-Job pool from Move 3. |
| **Optimizer Groups** | Tagged pools for resource isolation: "prod-tables", "staging-tables", "high-priority". Per-table quotas live in groups. | This is the model worth borrowing; see §3. |
| **LogStore** | Kafka/Pulsar-backed streaming buffer for the Mixed-Iceberg/Mixed-Paimon formats. | Out of scope — streaming is "Who this isn't for (yet)" in the README. |

### 2.2 The minor/major/full split

Amoro borrowed the JVM GC generational metaphor for Iceberg MOR compaction:

* **Minor optimizing** — Fast, frequent. Combines fragment files (< 16 MB by default) up to the target size, and converts `eq-deletes` to `pos-deletes`. The amortised cost is low and the read amplification relief is high, so it's safe to run often.
* **Major optimizing** — Slow, infrequent. Combines segment files (≥ 16 MB) and merges accumulated delete data. The point is to control the *rate* of major runs, otherwise read amplification trade-off tilts into write amplification.
* **Full optimizing** — Special case: merge everything into one file. Held in reserve.

This is the exact tension our Move 4 (per-chunk checkpointing) and Move 12 (commit-velocity / streaming-detection) are scoping around. Amoro resolves it with the generational model; we're scoping it differently because our users are batch-first and don't need continuous streaming compaction.

### 2.3 Scheduling policies

Two production-stable options:

* **Quota** — Per-table "quota" (a percentage of group slots OR a fixed slot count). The scheduler ranks tables by their *occupation ratio* (actual thread-time used ÷ quota-time, over a look-back window). Lower occupation ⇒ higher priority. The intent: tables under-served relative to their configured slice get caught up.
* **Balanced** — Time-progression based. Tables with the longest *time since last optimization* get priority. The intent: keep all tables within an optimizer group on a roughly equal cadence.

Both run against per-table resource claims, which isolates noisy-neighbour problems at the table level — which is exactly where our §3 was heading (Cryo's governor does per-table lock + per-replica caps, but not per-table slot quotas yet).

### 2.4 The catalogue-as-a-service angle

Amoro offers a *unified catalog service* that engines (Trino/Flink/Spark) can speak against instead of the underlying Hive Metastore / Glue / Nessie. That makes the optimizer work cross-catalog — you don't need to add Iceberg-REST support to every individual engine.

This isn't directly relevant to Cryo's design (we already speak to Iceberg REST natively; the engines are the consumers not the catalog source), but it's the reason Amoro has traction in mixed-engine fleets — and the reason §3 concludes we have nothing to integrate *here*.

---

## 3. What we'd actually borrow

Three things. Listed in order of cost/value.

### 3.1 Per-table optimizer quota (borrow) — **partially landed, 2026-09-27**

Cheap, high-value, matches the existing `Quota` policy in Amoro. Current Cryo governor (`cryo/governor/capacity.py`) treats native work as "all-of-one-budget". A per-table slot quota, configurable per group (e.g. "billing-tier gets 30% of native slots"), would give us an honest equivalent of Amoro's `self-optimizing.quota`.

**Status:** the *fairness* half of this — Amoro's `Balanced` policy equivalent — is implemented: `cryo.governor.admission.pack_fleet_budget` now takes a bounded, opt-in `staleness_boost_days`/`staleness_boost_max` (`AdmissionConfig`), so a table neglected past its threshold gets a capped score boost and can outrank a merely higher-scoring table that gets maintained every wave. Off by default (`staleness_boost_days=0.0` no-ops the existing sort); configurable per-destination via `_admission_config_from_destination`. See `cryo/governor/admission.py::_staleness_boost` and `tests/test_admission.py`'s fairness suite. **Not yet done:** the `Quota` half — a hard per-table/per-group *slot reservation* (e.g. "billing-tier gets 30% of native slots" as a guarantee, not just a tiebreak). The staleness boost prevents indefinite starvation; it does not guarantee a minimum share of capacity the way a real quota would.

**Cost:** ~1 week for the quota half. The model is well-understood; the implementation surface is a new `cryo.scheduler.rules` module plus a per-table/group quota column (likely on `Preset` or a new table, since neither `MaintenanceRun` nor `Table` has a group/tier field today).
**Risk:** Low. Existing load-aware admission stays unchanged; this is an orthogonal axis.
**Recommendation:** Adopt the remaining quota half when a real noisy-neighbor complaint (not just this evaluation) motivates it — the staleness boost already closes the acute starvation risk cheaply.

### 3.2 Minor/major/full split taxonomy (reference, don't port)

Amoro's GC-inspired split maps onto our existing `OperationType` enum (`expire_snapshots`, `remove_orphan_files`, `optimize`, `optimize_manifests`) if you squint — but forcing the mapping adds complexity without buying us anything. Our ops are already correctly sequenced in the planner (`expire → orphan → compact → manifests`) per `lld.md` §4.

**Cost to port:** High. We'd be writing a planner rewrite to handle file-size-driven op selection, and the savings only materialise under aggressive streaming ingest (which we explicitly don't target).
**Recommendation:** Don't port. Cite the Amoro mental model in `docs/` so the team knows the *why* behind our scheduler policies when reading the planner code.

### 3.3 REST catalogue service integration (defer)

Amoro can stand in as the Iceberg REST catalog. Cryo's `cryo/sources/` already speaks REST / Glue / HMS — we don't *need* Amoro as a catalog.

The one scenario where it would matter is "single tenant wants Amoro's optimizers *and* Cryo's policy/cost UI on the same set of tables." That's a joint-deployment mode, not a fork-in-the-road. Don't build it speculatively; build it when a customer asks.

**Recommendation:** Defer indefinitely.

---

## 4. What we explicitly diverge on

1. **Multi-format support.** Amoro supports Iceberg, Paimon, Mixed-Iceberg, Mixed-Hive. Cryo is Iceberg-only by design (`AGENTS.md` hard requirement). The narrowest non-Iceberg path Amoro would offer is Paimon, which is a meaningful spec — but the `research-and-roadmap.md` Part 2 already listed Paimon in the "⏳ later, not planned" bucket for good reasons (no in-fleet demand from the ex-Ryft window, doubles the QA surface).
2. **Streaming / CDC ingest.** Amoro's *reason to exist* is stream-and-batch-fused pipelines on top of LogStore. Cryo's README scopes streaming out — "streaming-primary shops are explicitly 'who this isn't for (yet)'". Adopting Amoro to get its scheduler would pull in the streaming surface area or fight it forever.
3. **Flink-first optimizers.** Amoro's optimiser implementations are Flink jobs (with Spark and local JVM as alternatives). Cryo's are K8s Jobs running either Go (native) or Python-with-subprocess (Trino/Spark) — see Move 3. Decoupling from Flink keeps the deployment footprint flat and avoids the JVM-as-sidecar dependency our Helm chart currently does not have.
4. **Centralised AMS as a primary service.** Amoro deploys AMS as the only stateful plane. Cryo already has governor + scheduler + REST catalog as separate logical services co-deployed; duplicating that with AMS would be a re-org, not a swap. We accept the duplication for the layering it lets us keep (sources vs destinations are separate per `AGENTS.md`).

---

## 5. The integration decision tree

A short, sharp rubric for when this evaluation should be revisited.

| Trigger | Action |
|---|---|
| A prospective customer asks for streaming Lakehouse features | Open a *new* Spike: "should we expose a streaming-front-end, and what does adding it to Cryo look like vs. running Amoro side-by-side?" Do NOT integrate Amoro scheduler in isolation. |
| Move 3 (K8s Jobs) + Move 4 (chunk checkpointing) ship and we observe per-table noisy-neighbour complaints | Adopt §3.1 (per-table quotas). It's the only Amoro piece that earns its keep on batch workloads. |
| Move 6 (governor live-capacity accounting) reveals Amoro has the *engine-state-shape* we need for free | Cite this doc; don't re-derive the comparison. Either adopt the format pattern or note we deliberately don't. |
| §5.3 of `research-and-roadmap.md` (Puffin statistics) moves from "post-Move-3-4" to "next quarter" | That opens Paimon-fleet benchmarking again. Use that trigger to ask the integration question with real data. |
| Amoro graduates from Apache incubator and has ≥1 year of stable APIs | Re-evaluate the "joint deployment" angle from §3.3. Cost goes from speculative to near-zero only when there's a non-trivial API to integrate against. |

---

## 6. What this evaluation changes in the roadmap

* **Move 6 (governor live capacity accounting)** — unchanged. Adds live state; no Amoro dependency.
* **Move 17 (protected snapshots)** — unchanged. Amoro doesn't have an equivalent; the Iceberg layer is what we extend.
* **The §3.1 quota model** — **Move 25a, fairness half landed 2026-09-27** (`cryo.governor.admission._staleness_boost`, opt-in). The hard-quota half (guaranteed minimum slot share per table/group) remains open; without it, "high-priority big-table compaction" is not *guaranteed* per-table, only protected from indefinite starvation.
* **The "from-scratch scheduler" risk in `native-engine-scoping-research.md` §6.8** — **closed.** With this doc, the team has a written decision; future contributors don't need to re-evaluate.
* **No change** to Moves 1–4 sequencing. The Move 3 K8s-Job plan was already a *well-trodden K8s pattern* even with Amoro's structural similarity — building it does not duplicate Amoro, because the *interface* (Trino/Spark/Go workers, our own governor) is what we'd ship either way.

---

## 7. Sources

* `https://amoro.apache.org/` — landing page
* `https://amoro.apache.org/docs/latest/` — architecture overview; catalog split; supported engines
* `https://amoro.apache.org/docs/latest/self-optimizing/` — minor/major/full taxonomy + Quota/Balanced scheduling policies; optimizer-group resource isolation
* `https://amoro.apache.org/docs/latest/managing-optimizers/` — operator surface for optimizer groups
* `docs/native-engine-scoping-research.md` §4.2 (Amoro comparison) and §6.8 (deferred integration decision) — internal reference
* `docs/research-and-roadmap.md` Part 2 (competitive landscape verification notes) — internal reference
* `AGENTS.md` §1 hard requirements (sources vs destinations separation, no Airflow, Trino/Spark SQL gen separation)

Numbers I didn't reproduce myself (the 10× vs Spark rewriteDataFiles claim, the per-table quota occupation ratios under load) are flagged in §2 and would need a quick reproduce-from-benchmark before being cited externally.
