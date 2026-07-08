# Native maintenance engine: scaling, cost, and production-readiness scoping

**Status:** research / decision-support document, not an implementation plan. **Owner:** TBD.
**Depends on:** `docs/native-iceberg-go-compaction-plan.md` (the engine choice and correctness
design — this doc does not re-litigate that decision), `docs/native-iceberg-rust-engine-errors.md`,
`docs/s3-request-cost-tracking.md`.

## 0. Why this document exists

The engine-choice question ("Rust vs Go vs DataFusion vs Trino/Spark") is **already answered** —
see [§2](#2-confirming-the-engine-choice-what-changed-nothing). That decision was correctness- and
availability-driven (does the upstream library have an atomic rewrite-commit primitive without
forking) and it holds up under fresh research: nothing found here argues for reopening it.

What is **not** yet decided, and what actually determines whether Cryo is fast/cheap/reliable once
real users run it on real tables, is everything downstream of "which library parses the Iceberg
spec": how a job gets *compute*, how much compute it gets, what happens when that compute is wrong
(too little → OOM; too much → wasted spend), what happens when it's interrupted, and what happens
when the table is 100x bigger than anything tested in CI. That's the gap this document scopes.

It's structured as: confirm the engine decision briefly → survey what the wider Iceberg
maintenance community has actually gotten wrong in production (with sources) → survey how other
teams and engines solve the execution/scaling problem → run Cryo's own architecture through six
concrete "day 200 of running this in the wild" scenarios → consolidate into a risk register →
recommend a scope-in/scope-out split.

---

## 1. Where Cryo is today (verified against the code, not the docs, on 2026-07-08)

| Component | Current state | Source |
|---|---|---|
| Engine selection | `CRYO_ENGINE_IMPL` set to `go` or `rust`; Rust path structurally blocked (`NATIVE_RUST_OPTIMIZE_DETAIL`: "native compaction blocked upstream on iceberg-rust (EPIC #2269)") | `cryo/engines/native/routing.py` |
| Execution model | Native engine runs as an OS **subprocess of the Worker pod** (`subprocess.run`/`Popen`) — no resource isolation from the Worker's own container | `cryo/engines/native/runner.py` |
| Worker pod sizing | Fixed: `requests: {cpu: 500m, memory: 1Gi}`, `limits: {cpu: "2", memory: 4Gi}`, 2 replicas — **identical ceiling for a 2GB table and a 20TB table** | `deploy/helm/cryo/values.yaml` |
| Governor capacity model (native) | `_probe_native()` returns a **static configured `slot_cap`** (concurrency count only); `used=0` is hardcoded — no live CPU/memory/subprocess accounting | `cryo/governor/capacity.py` |
| Planner chunking | Already splits a table's plan by `chunk_size_gb` / `gb_budget` — the input a resource-sizing formula would need already exists | `cryo/planner/planner.py` |
| Compaction prioritization | Already implements the LinkedIn **AutoComp** paper's benefit-score ranking (not naive small-file-count ordering) — explicitly cited in-code | `cryo/planner/benefit_score.py` |
| Query latency prediction | QPP model predicts **read-query** p50/p95/p99 latency for Governor admission — not resource sizing for maintenance jobs; a structurally similar model doesn't yet exist for "how much CPU/mem will this compaction job need" | `cryo/governor/qpp.py` |
| K8s deployment primitives | Only `Deployment` manifests exist (worker/governor/scheduler/scanner) — **no `Job` template** for per-run elastic dispatch | `deploy/helm/cryo/templates/` |
| CI coverage (Go engine) | Unit tests + build + spawn-verification run in CI. Differential testing vs. Trino and the concurrency/fault-injection suite (§8.5/§8.7 of the Go plan) **do not yet run in CI** | `.github/workflows/ci.yml` |
| Phase E status (Go plan) | "Live native capacity from queue/DB in-flight subprocess accounting" is explicitly **planned but not done** (rust finding #9) — this document's scaling recommendations feed directly into that already-open task | `docs/native-iceberg-go-compaction-plan.md` §7 |

Two things fall out of this table immediately, before any external research: **(a)** nothing in
Cryo's current architecture prevents a large-table job from being confined to the same 4Gi ceiling
as every other job, and **(b)** the team has already identified this class of gap (Phase E), just
not yet scoped it as concretely as the rest of the correctness work.

---

## 2. Confirming the engine choice: what changed, nothing

Fresh research (LakeOps, RisingWave — both Rust+DataFusion) reports dramatic wins over Spark:
RisingWave 5.5x faster / 82% cheaper on a 193GB bin-pack job and successful where Spark OOM'd on a
60K-file/40K-delete job; LakeOps 86% faster and completed a 1.2TB table Spark failed on outright.
This is real signal that **JVM-free, embedded, vectorized execution is the right general shape** —
but it is not an argument for Rust specifically. Both of those companies almost certainly own
enough of their own commit-path code that they didn't hit the wall Cryo hit twice: **upstream
`iceberg-rust` has no merged atomic overwrite/rewrite commit action**, and forking it was ruled
out. `iceberg-go` has the primitive natively, upstream, unforked, as of v0.6.0. The team's own
`native-iceberg-rust-engine-errors.md` (27 findings, several severe — silent `ok:true` on stub
operations, `expire_snapshots` permanently leaking storage by never deleting files) is exactly the
kind of failure mode a maintenance tool cannot afford to reintroduce by reviving Rust prematurely.

**Verdict: no change.** Go/iceberg-go remains correct. The open questions are all about what
surrounds the engine, not the engine itself.

---

## 3. What the Iceberg maintenance community actually gets wrong

Organized by category; each entry notes whether Cryo's current design is exposed, protected, or
untested against it.

### 3.1 Manifest and metadata bloat

- A table with 45M files / ~50TB data / **5TB of metadata** accumulated ~200K manifests; query
  coordinators OOM'd reading stats for 22M files and entered retry loops that stalled the whole
  workload. Fixed by layered manifest pruning (>95% of manifests became skippable per query).
  [e6data](https://www.e6data.com/blog/apache-iceberg-million-files-metadata)
- `RewriteManifests` on a 7,000+-manifest table took **over an hour** because snapshot-ID
  validation runs serially, not distributed; without it, under 2 minutes. Closed "not planned" by
  Iceberg maintainers — i.e. **this is not going to be fixed upstream**, callers must avoid the
  pathological case themselves. [apache/iceberg#8932](https://github.com/apache/iceberg/issues/8932)
- Socket/TIME_WAIT exhaustion reading manifests from S3 during maintenance ops, still open.
  [apache/iceberg#14951](https://github.com/apache/iceberg/issues/14951)

  **Cryo exposure:** `optimize_manifests` is routed to Trino/Spark on both native engines (neither
  implements it) — so manifest-count control is entirely outside the fast/cheap path today. A
  table that compacts data files quickly via Go but never gets manifest rewrite scheduled will
  still hit this class of problem. **Untested**: no CI coverage exercises a manifest count in the
  tens-of-thousands range.

### 3.2 Catalog commit contention

- Compaction-vs-ingestion conflicts reportedly cause **90% of production commit conflicts**; a
  concrete failure mode: a 2-hour compaction job loses to 47 ingestion micro-batches and gets
  rejected seconds before completion — the entire compute spend is wasted, not partially credited.
  [Ryft](https://www.ryft.io/blog/handling-commit-conflicts-in-apache-iceberg-patterns-and-fixes)
- If a streaming writer retries aggressively and batch compaction retries conservatively (default:
  4 attempts, 100ms–60s backoff), compaction can be **permanently starved** on a hot table.
- Catalog choice changes this materially: Hive Metastore's table-level locking bottlenecks under
  concurrency; Glue is bound by account/region API rate limits; REST catalogs (Polaris, Nessie)
  handle contention best. [RisingWave catalog guide](https://risingwave.com/blog/iceberg-catalog-comparison-guide/)
- Adobe's AEP platform reports a real-world ceiling of **~15 commits/minute per table** from
  Iceberg's optimistic-concurrency metadata swap — a hard constraint independent of engine choice.

  **Cryo exposure:** the Go plan's §5.3 already builds application-level retry (reload → re-plan →
  re-run) for non-REST catalogs that don't wrap `ErrCommitFailed` — good. **Gap**: nothing in
  Cryo's current design distinguishes "compaction lost the race, retry cheaply" from "compaction
  lost the race after 2 hours of compute, retrying from scratch is expensive" — this is exactly
  what chunk-level checkpointing (§6) would fix, and it isn't built yet.

### 3.3 Correctness incidents (the reputation-defining category)

- **Confirmed on Iceberg 1.6.1 and 1.9.1**: `expire_snapshots` erroneously deleted data files still
  referenced by the table when multiple refs (tags/branches) existed — `IncrementalFileCleanup`
  misclassified manifests as safe-to-revert. [apache/iceberg#13568](https://github.com/apache/iceberg/issues/13568)
- `deleteOrphanFiles` deleted manifest `.avro` files still actively referenced by table metadata,
  **bricking the table** ("Failed to open input stream") until manual repair.
  [apache/iceberg#4194](https://github.com/apache/iceberg/issues/4194)
- Iceberg's own docs warn: a retention window shorter than in-flight write duration can delete
  not-yet-committed files; path-string mismatches (e.g. HDFS authority changes) cause false-positive
  orphan detection. Prescribed order is strict — expire snapshots → remove orphan files → rewrite
  manifests — running out of order risks corruption. [Iceberg maintenance docs](https://iceberg.apache.org/docs/latest/maintenance/)

  **This is the single most important category in this document.** A maintenance tool's entire
  value proposition is "we make your table healthier, safely." An incident here isn't a performance
  regression, it's a **data-loss postmortem with Cryo's name on it**, and for an OSS project, one
  well-publicized incident can be terminal for adoption regardless of how fast the engine is.
  **Cryo exposure**: the Rust-era findings document (#20: `expire_snapshots` leaked storage by
  never deleting files) shows this class of bug is not hypothetical for this codebase specifically
  — it already happened once. The Go plan's §5/§8 correctness invariants and differential testing
  are the correct mitigation, but per §1's table, **differential testing vs. Trino is not yet
  wired into CI** — meaning this protection currently exists as a local/manual test suite, not a
  release gate.

### 3.4 Cost surprises

- Managed compaction (AWS S3 Tables) cost **$47.69 for a 954GB job vs. $2.29 self-managed — a 20.8x
  markup** — and in testing failed to trigger at all below 10,000 files/100GB.
  [Onehouse](https://www.onehouse.ai/blog/s3-managed-tables-unmanaged-costs-the-20x-surprise-with-aws-s3-tables)
- Orphan files on mature streaming lakes routinely reach **25–40% of billable S3 storage**; one
  production scan found ~200TB dead data across 324 tables (~1.8M orphan files, ~$4,000/month).
  [LakeOps](https://lakeops.dev/blog/reduce-aws-s3-cost-iceberg)
- A table costing "$10/query when healthy" was reported climbing to "hundreds per query" once
  compaction fell behind.

  **Cryo relevance**: this is a direct validation of `docs/s3-request-cost-tracking.md` — the
  orphan-file cost-drift pattern above is precisely what that feature measures. It also argues that
  **"maintenance kept up to date" is itself a cost-control feature**, not just a performance one —
  worth stating explicitly when scoping priorities.

### 3.5 MOR / equality-delete pain

- Equality deletes require a join-like scan across every relevant data file at read time —
  structurally expensive, widely discussed ("the equality delete problem").
  [RisingWave](https://risingwave.com/blog/the-equality-delete-problem-in-apache-iceberg/) /
  [HN discussion](https://news.ycombinator.com/item?id=44880081)
- Compacting can leave **dangling equality-delete files** still referenced in every future
  snapshot's manifests with zero rows left to delete — open cleanup gap in iceberg-go itself.
  [apache/iceberg-go#946](https://github.com/apache/iceberg-go/issues/946)
- **v3 deletion vectors solve this only for positional deletes.** Equality deletes are unsolved by
  the spec upgrade — a table using equality deletes gets no relief from moving to v3.

  **Cryo exposure**: the Go plan already routes v3 partitioned-deletion-vector tables to Trino/Spark
  (upstream gap #1135) and implements v2 position+equality delete compaction. The dangling
  equality-delete-file cleanup gap (#946) is upstream and unowned by Cryo — worth tracking, not
  currently actionable.

### 3.6 Multi-engine friction

- Dropping a partition field via Spark's evolution API broke Trino's ability to read the table
  afterward. [trino#8284](https://github.com/trinodb/trino/issues/8284)
- `snapshot-id-inheritance` + Spark's `add_files` produced partition-spec source-ID mismatches
  between manifest headers and metadata JSON. [apache/iceberg#12273](https://github.com/apache/iceberg/issues/12273)
- Common community guidance: standardize *writes* on one engine (usually Spark), treat cross-engine
  writes as an architecture problem to design around, not something any single maintenance tool
  fixes.

  **Cryo relevance**: Cryo's own multi-engine reality (Trino, Spark, native Go, native Rust legacy)
  makes this directly applicable — worth an explicit compatibility-matrix test (which engines wrote
  the table originally, does native compaction on it read back correctly in the others) rather than
  assuming "reads the spec correctly" implies "round-trips through every engine correctly."

---

## 4. How others solve execution & scaling

### 4.1 Production case studies

| Company | Scale | Approach | Scheduling model | Numbers | Lesson |
|---|---|---|---|---|---|
| **Netflix** | ~1.5M tables, 100s of PB | Custom: Polaris (REST catalog/CockroachDB) + Autotune (Spark compaction) + Janitors (cleanup) | Event-driven — metadata commits emit SQS notifications that trigger evaluation | Not disclosed | Built a dedicated *decision* service (Autotune) separate from the *execution* (Spark) — decoupling matches §5's recommendation independently |
| **Adobe (AEP)** | ~1M batches/day, 13TB/32B events/day | Iceberg embedded directly as a library, no separate service | Threshold/event-triggered | ~15 commits/min/table ceiling (real constraint) | "No separate service to operate" was explicitly cited as a benefit — a data point *against* over-building orchestration if embedding is viable |
| **Airbnb** | 35B+ events/day, 1,000+ tables | Spark, metadata-only rewrite when files already optimal (skip unnecessary I/O) | Daily batch | **50%+ compute savings, 40% less job time** vs. prior stack | The win came from *not doing unnecessary work*, not from a faster engine — reinforces `min-input-files` gating already in Cryo's cost-aware design (§6 of the Go plan) |
| **LinkedIn** | Production-scale (unquantified) | **AutoComp** — metric-driven compaction decisions, not periodic schedule | Workload-signal-driven | "Significant" improvement, no absolute figures | **Cryo's planner already implements this exact paper** (`cryo/planner/benefit_score.py`) — the planning side is ahead of the execution side |
| **Magnite (Databricks UC)** | 2T+ daily ad requests, PB-scale | Fully managed Predictive Optimization | Fully automatic | **50–70% faster queries, 33% lower load cost, 70% lower compute cost** | Automation itself (not engine choice) was the primary lever cited |

### 4.2 The engine landscape includes one very relevant prior-art project Cryo hadn't been compared against

**Apache Amoro (AMS)** — a purpose-built, self-hostable lakehouse *management* service that
dispatches actual rewrite work to pluggable "Optimizer" workers (Flink/Spark/Trino today). Its
architecture is explicitly split into an always-on lightweight "brain" (scheduling/decisioning) and
ephemeral, horizontally-scaled execution workers — **structurally identical to what §5 below
recommends Cryo build**, and it's a real, existing, maintained OSS project claiming ~10x speedup
over Spark's `rewriteDataFiles`. [Amoro docs](https://amoro.apache.org/docs/latest/self-optimizing/)

This doesn't change the engine-execution decision (Go/iceberg-go), but it's directly relevant to
the *orchestration* layer this document scopes, and should be an explicit evaluation item — see
[§7](#7-scope-in--scope-out), not an afterthought discovered later.

The rest of the landscape, briefly: **Flink** requires bolting on your own compaction job (no
built-in service); **Dremio** and **Databricks/Snowflake** are managed/catalog-bound, not
embeddable; **StarRocks**/**Doris** are early-to-emerging for native Iceberg maintenance;
**ClickHouse**/**DuckDB** are not distributed-service-viable (single-node or experimental write
support). None of these change §2's conclusion; they confirm the OSS field for a *dedicated,
embeddable, self-hosted* maintenance engine is thin, and Amoro + Cryo are close to the only two
serious entrants.

### 4.3 Elastic compute mechanics (concrete numbers)

- **KEDA `ScaledJob`**: creates one Job per queued unit of work — maps directly onto Cryo's
  existing Redis maintenance queue. Real config pattern: `maxReplicaCount` as a hard concurrency
  ceiling, `pollingInterval` 15–30s, `minReplicaCount > 0` to avoid cold starts,
  `activeDeadlineSeconds` to kill runaway jobs. **Gotcha**: default `backoffLimit: 6` auto-retries
  failed jobs — maintenance job logic must be idempotent (Cryo's chunk-based, per-chunk-commit
  design already tends this way) or duplicate work/commits can occur.
  [KEDA ScaledJob spec](https://keda.sh/docs/2.19/reference/scaledjob-spec/)
- **Karpenter**: ~45–60s from pod-pending to schedulable node (direct EC2 API), vs. Cluster
  Autoscaler's 2–5 minutes (ASG polling). Real friction: packing heterogeneous request shapes
  (small jobs vs. one 20TB job) onto uniform node pools fragments capacity — directly analogous to
  Cryo's job-size distribution.
- **Spot instances**: 5–15%/hour real-world interruption rate (higher than AWS's marketed 5%).
  Explicit practitioner guidance: **do not use spot for jobs longer than ~1 hour unless
  checkpointed into ≤30-minute segments.** A 20TB compaction job without checkpointing is exactly
  the case spot is wrong for.
- **Sizing pitfalls**: Kubernetes cgroups don't see JVM heap accounting — off-heap buffers/GC
  overhead can OOM-kill even when the JVM "looks healthy" (Spark-on-K8s-specific; less relevant to
  Cryo's Go engine, which has no JVM, but the general lesson — cgroup limits are the real ceiling,
  not application-reported memory — still applies). **Kubernetes in-place pod resize graduated to
  GA in v1.35 (Dec 2025)** — a running job's CPU/memory can now grow without a restart, explicitly
  called out by the Kubernetes project as useful for "batch jobs that encounter unexpectedly large
  datasets." This is new enough (7 months old as of this doc) to be a genuinely current option, not
  legacy advice.
- **Checkpointing precedent**: Iceberg's own `rewrite_data_files` procedure already has a
  `PARTIAL_PROGRESS_ENABLED` option that commits per file-group instead of one giant commit, so a
  failure only costs the affected group. AWS's own compaction guidance explicitly sizes clusters
  off `MAX_FILE_GROUP_SIZE_BYTES` (100GB default) and recommends **iterative profiling**, not a
  one-shot resource estimate, for new/unknown-size tables.

---

## 5. Six-months-into-production scenarios

Concrete, specific, and chosen because each one maps to a real gap identified above — not
hypothetical stress-testing for its own sake.

### Scenario A — The first 20TB table
A self-hosted install onboards a table far larger than anything in Cryo's test fixtures. The Worker
pod's fixed 4Gi limit either OOM-kills the native subprocess mid-compaction or the job silently
falls back to Trino/Spark at much higher cost than the "fast native engine" pitch promised.
**Gap hit**: §1's fixed-pod-ceiling finding. **Mitigation**: §6's Job-based elastic dispatch,
sized from Planner-known `total_size_gb`.

### Scenario B — The hot table compaction never wins
A high-ingest-rate streaming table's compaction job repeatedly loses the optimistic-concurrency
race (Adobe's ~15 commits/min ceiling; Ryft's "2-hour job rejected seconds before completion"
pattern). Compute is wasted repeatedly, file count grows unchecked, and the operator sees Cryo
"running constantly but not helping." **Gap hit**: §3.2. **Mitigation**: chunk-level checkpointing
(§6) so a lost race only forfeits the current chunk's work, plus surfacing "compaction is losing
the commit race on table X" as a first-class alert rather than a silent retry loop.

### Scenario C — An operator's retention config deletes live data
An operator sets an aggressive orphan-file retention window (or the table has an unusually
long-running write) and Cryo deletes a file still referenced by an in-flight commit — the exact
documented pattern from apache/iceberg#4194/#13568. For a self-hosted OSS tool, this is a
trust-ending incident, not a bug report. **Gap hit**: §3.3. **Mitigation**: this is precisely why
differential testing vs. Trino belongs in CI as a hard release gate (§7), not a local-only suite —
and why default retention windows should be conservative, with the tool actively warning rather
than silently accepting a dangerously short value.

### Scenario D — OSS adoption spike, wildly different table shapes
50 new self-hosted installs appear in a month (a real "success" scenario for an OSS project).
Nobody has profiled their own worst-case table against Cryo yet. A meaningful fraction hit
Scenario A independently, generating a wave of "compaction crashes on my biggest table" GitHub
issues in the same week — which, for a young project, can define its reputation before the
architecture fix ships. **Gap hit**: same as A, but at fleet/community scale.
**Mitigation**: ship the elastic-sizing fix *before* actively promoting "faster and cheaper" claims
widely, and make the sizing formula's inputs (table GB, file count) visible/overridable so an
operator isn't purely dependent on Cryo's guess being right the first time.

### Scenario E — Neglected-table cost blowup
A table with maintenance disabled or failing silently for months accumulates orphan files up to
the documented 25–40% of billable storage. An operator finally runs Cryo, and the orphan-scan's
S3 LIST cost itself becomes a noticeable line item before any savings are realized.
**Gap hit**: §3.4. **Mitigation**: this is exactly what `docs/s3-request-cost-tracking.md` exists
to make visible — worth explicitly cross-linking maintenance-run cost estimates to that feature so
"this scan will cost approximately $X in S3 requests" is shown before a large orphan scan runs, not
discovered after the bill arrives.

### Scenario F — Spot interruption on an uncheckpointed huge job
To save cost (per this whole document's cost-consciousness), an operator or Cryo's own default
config runs native-engine Jobs on spot capacity. A 3-hour compaction job on a large table gets
interrupted at hour 2 by a spot reclaim, and restarts from scratch — repeatedly, if the node pool
stays capacity-constrained. **Gap hit**: §4.3's explicit "don't use spot for >1hr jobs without
checkpointing" finding. **Mitigation**: §6 — checkpointing is a prerequisite for spot eligibility,
not orthogonal to it; gate spot usage on job size/estimated duration, not a blanket policy.

### Scenario G — Chunk fan-out causes its own commit contention
Cryo's own `chunk_size_gb` splitting (used to bound blast radius, §3.2) is pushed further to
parallelize a 20TB table across many concurrent chunk jobs for speed. If those chunks touch
overlapping partitions or commit against the same table snapshot lineage too aggressively, they
can start contending with *each other*, not just with ingestion — the same optimistic-concurrency
mechanics as Scenario B, self-inflicted. **Gap hit**: a scaling-introduced version of §3.2.
**Mitigation**: partition-disjoint chunking (already implied by "max-partitions-per-chunk" in the
Go plan's §6) must be a hard invariant, not just a cost heuristic, once chunks run concurrently
rather than sequentially.

---

## 6. Recommended architecture direction (synthesis)

This section is a recommendation, not a decision — see the auto-mode framing in §7 for what's
actually being asked to be scoped in now vs. deferred.

1. **Decouple execution from the Worker pod.** Move native-engine invocation from
   `subprocess.run` inside the long-lived Worker container to a **Kubernetes `Job`** dispatched
   per maintenance run (or per chunk), sized from Planner-known `total_size_gb` /
   `chunk_size_gb` / file counts. The Worker stays small and constantly-on (cheap); compute cost
   tracks actual work.
2. **Use KEDA `ScaledJob`** against the existing Redis queue as the dispatch mechanism, and
   **Karpenter** for right-sized, just-in-time node provisioning. This is a well-trodden,
   documented pattern (§4.3), not a novel build.
3. **Size Jobs from a formula, start simple, iterate like AWS's own guidance recommends**: seed
   CPU/memory requests from `total_size_gb` and file count (already computed by the Planner),
   accept it will be wrong sometimes, and use **Kubernetes 1.35's in-place pod resize** (GA,
   Dec 2025) as the correction mechanism for underestimates rather than a crash-and-retry loop.
4. **Build chunk-level checkpointing as a first-class requirement**, mirroring Iceberg's own
   `PARTIAL_PROGRESS_ENABLED` precedent: each chunk commits independently, so a failure —
   commit-race loss, spot interruption, node failure — only forfeits that chunk's work. This is
   the single change that unblocks safe spot usage (Scenario F) *and* reduces the cost of losing
   commit races (Scenario B) *and* is a natural fit given chunking already exists in the Planner.
5. **Gate spot instance usage on job size/duration estimate**, not a blanket setting — small/short
   jobs are fine on spot today; large uncheckpointed jobs are not, per §4.3's explicit evidence.
6. **Close Phase E's already-identified gap** ("live native capacity from queue/DB in-flight
   subprocess accounting") using real K8s Job state once Jobs exist, rather than the current
   static `slot_cap`. This turns the Governor from "counts concurrent jobs" into "knows real
   resource pressure" — the same shift QPP already made for read-query admission, applied to
   maintenance execution.
7. **Treat differential-testing-vs-Trino and the concurrency/fault-injection suite as a hard CI
   gate before wide promotion of native compaction**, not a parallel-track nice-to-have — per
   §3.3, a correctness incident is categorically worse for an OSS tool than a missed performance
   target.
8. **Spike Apache Amoro as an evaluation, not a commitment.** Its brain/optimizer split validates
   the direction above independently; worth an explicit time-boxed evaluation of whether Cryo
   should integrate with it, learn from its scheduler design, or confidently diverge — currently
   an unknown, not a "definitely build from scratch" default.

---

## 7. Scope in / scope out

**Scope IN (next 1–2 quarters, in dependency order):**
- Finish Go engine COW+MOR compaction to Phase E exit criteria (already scoped, in flight).
- Wire differential-testing-vs-Trino and concurrency/fault-injection suites into CI as a hard
  release gate (closes §3.3's exposure; currently the single biggest reputational risk).
- Kubernetes `Job`-based dispatch for native-engine execution, replacing subprocess-in-Worker-pod
  (closes Scenario A/D).
- Resource-sizing formula seeded from Planner's existing `total_size_gb`/`chunk_size_gb`/file
  counts (closes Scenario A; feeds Phase E's capacity-accounting gap).
- Chunk-level checkpointing / partial-progress commits (closes Scenario B/F; prerequisite for
  everything else being safe).
- Governor live-capacity accounting from real Job resource state (closes the Phase E gap with
  actual data instead of a static slot count).
- Surface maintenance-run S3-request-cost estimates before large orphan scans, cross-linked to
  `docs/s3-request-cost-tracking.md` (closes Scenario E).

**Scope OUT (explicitly deferred, revisit only on proven need):**
- DataFusion integration — already correctly rejected by the Go plan (cgo bridge risk, no
  correctness benefit); nothing in this research changes that.
- z-order / global sort — opt-in, build-tag-gated, only if a real need is proven (per existing
  Go plan §7 Phase F).
- Native `optimize_manifests` implementation — keep routed to Trino/Spark (Go plan Phase F
  option 1); building it natively is a large, separately-hardened effort not justified yet.
- Default/unconditional spot-instance usage for large jobs — gate on size/duration and
  checkpointing maturity first (§6 point 5).
- Retiring the Rust engine — only after Go hits Phase E exit criteria, per the existing plan;
  this document doesn't accelerate or change that sequencing.
- Adopting Apache Amoro wholesale, or building a from-scratch competing scheduler — defer to the
  time-boxed spike in §6 point 8; no commitment either way yet.

---

## 8. Consolidated risk register

Extends the existing risk table in `docs/native-iceberg-go-compaction-plan.md` §9 (engine-internal
correctness risks) with the orchestration/deployment/community risks this document surfaced.

| Risk | Impact | Mitigation | Status |
|---|---|---|---|
| Native subprocess confined to fixed Worker pod resources | Large-table jobs OOM or silently fall back to expensive engines | K8s Job-based dispatch, sized per job (§6.1–3) | Not started |
| No live resource accounting for native jobs | Governor can't distinguish "5 small jobs" from "1 huge job" when admitting work | Extend capacity model with real Job state (§6.6) | Planned (Phase E), not started |
| No job checkpointing | Any interruption (commit-race loss, spot reclaim, node failure) forfeits the entire job | Chunk-level partial-progress commits (§6.4) | Not started |
| Differential/concurrency tests not in CI | A correctness incident (data loss, table corruption) ships before it's caught | Hard CI gate before wide promotion (§7) | Scoped, not wired into CI |
| Manifest bloat outside the fast path | `optimize_manifests` always routes to Trino/Spark; native speed gains don't cover this cost center | Accept as scoped-out (Go plan Phase F option 1); monitor manifest counts | Accepted, documented |
| Aggressive retention config deletes live data | Table corruption / data loss, OSS trust incident | Conservative defaults + active warning on short retention windows; differential testing | Partially addressed (correctness invariants exist), needs CI gate |
| Spot usage on uncheckpointed large jobs | Wasted multi-hour compute repeatedly | Gate spot on size/duration + checkpointing (§6.5) | Not started |
| OSS adoption outpaces elastic-sizing fix | Wave of "crashes on my biggest table" issues defines early reputation | Sequence: ship §7 scope-in items before wide promotion | Sequencing decision, not yet made |
| Cross-engine table compatibility unverified | A table written by Spark/Trino may not round-trip correctly through native Go compaction | Explicit cross-engine compatibility test matrix | Not started |

---

## 9. Sources

**Community pain points:**
[apache/iceberg#8932](https://github.com/apache/iceberg/issues/8932) ·
[apache/iceberg#14951](https://github.com/apache/iceberg/issues/14951) ·
[apache/iceberg#13568](https://github.com/apache/iceberg/issues/13568) ·
[apache/iceberg#4194](https://github.com/apache/iceberg/issues/4194) ·
[apache/iceberg#1286](https://github.com/apache/iceberg/issues/1286) ·
[apache/iceberg#12273](https://github.com/apache/iceberg/issues/12273) ·
[apache/iceberg-go#946](https://github.com/apache/iceberg-go/issues/946) ·
[trino#8284](https://github.com/trinodb/trino/issues/8284) ·
[trino#12323](https://github.com/trinodb/trino/issues/12323) ·
[Iceberg maintenance docs](https://iceberg.apache.org/docs/latest/maintenance/) ·
[Iceberg table spec — deletion vectors](https://iceberg.apache.org/spec/?h=deletion+vec) ·
[e6data — metadata at massive scale](https://www.e6data.com/blog/apache-iceberg-million-files-metadata) ·
[Ryft — commit conflicts](https://www.ryft.io/blog/handling-commit-conflicts-in-apache-iceberg-patterns-and-fixes) ·
[RisingWave — catalog comparison](https://risingwave.com/blog/iceberg-catalog-comparison-guide/) ·
[Onehouse — S3 Tables 20x cost](https://www.onehouse.ai/blog/s3-managed-tables-unmanaged-costs-the-20x-surprise-with-aws-s3-tables) ·
[LakeOps — S3 cost reduction](https://lakeops.dev/blog/reduce-aws-s3-cost-iceberg) ·
[RisingWave — equality delete problem](https://risingwave.com/blog/the-equality-delete-problem-in-apache-iceberg/) ·
[HN discussion](https://news.ycombinator.com/item?id=44880081)

**Production case studies & engine landscape:**
[Netflix Iceberg at scale](https://vutr.substack.com/p/i-spent-4-hours-learning-how-netflix) ·
[Netflix — optimizing warehouse storage](https://netflixtechblog.com/optimizing-data-warehouse-storage-7b94a48fdcbe) ·
[Adobe — Iceberg at Adobe](https://medium.com/adobetech/iceberg-at-adobe-88cf1950e866) ·
[Airbnb — warehouse infra upgrade](https://medium.com/airbnb-engineering/upgrading-data-warehouse-infrastructure-at-airbnb-a4e18f09b6d5) ·
[DoorDash — realtime + Iceberg](https://blog.dataengineerthings.org/how-does-doordash-evolve-realtime-processing-platform-with-iceberg-15486712cfbc) ·
[AutoComp paper (arXiv:2504.04186)](https://arxiv.org/abs/2504.04186) ·
[Uber — Hudi at trillion-record scale](https://www.uber.com/us/en/blog/apache-hudi-at-uber/) ·
[Magnite — Unity Catalog managed Iceberg](https://www.databricks.com/customers/magnite/unity-catalog-managed-iceberg) ·
[Databricks predictive optimization](https://docs.databricks.com/aws/en/optimizations/predictive-optimization) ·
[Apache Amoro docs](https://amoro.apache.org/docs/latest/self-optimizing/) ·
[Amoro 10x vs Spark rewriteFiles](https://medium.com/@jinsong.zhou1990/10x-efficiency-boost-compared-to-spark-rewritefiles-procedure-how-apache-amoro-efficiently-7e7a993950d7) ·
[Dremio — optimizing Iceberg tables](https://www.dremio.com/blog/optimizing-iceberg-tables/) ·
[StarRocks Iceberg catalog](https://docs.starrocks.io/docs/data_source/catalog/iceberg/iceberg_catalog/) ·
[Apache Doris 4.1 on Iceberg v3](https://www.velodb.io/blog/apache-doris-4-1-on-iceberg-v3-full-lakehouse-lifecycle) ·
[ClickHouse is data lake ready](https://clickhouse.com/blog/clickhouse-is-data-lake-ready) ·
[DuckDB Iceberg features](https://duckdb.org/2026/05/29/new-iceberg-features) ·
[AWS Glue auto compaction](https://aws.amazon.com/blogs/big-data/accelerate-queries-on-apache-iceberg-tables-through-aws-glue-auto-compaction/) ·
[Snowflake managed Iceberg](https://docs.snowflake.com/en/user-guide/tables-iceberg-manage) ·
[Tabular → Databricks](https://www.databricks.com/blog/databricks-tabular)

**Elastic compute mechanics:**
[KEDA ScaledJob spec](https://keda.sh/docs/2.19/reference/scaledjob-spec/) ·
[LeanIX — KEDA job processing](https://engineering.leanix.net/blog/using-keda-for-job-processing-in-kubernetes/) ·
[kedacore/keda#2488](https://github.com/kedacore/keda/issues/2488) ·
[kedacore/keda#7329](https://github.com/kedacore/keda/issues/7329) ·
[Karpenter provisioning latency discussion](https://github.com/aws/karpenter/issues/2906) ·
[Karpenter vs Cluster Autoscaler 2026](https://scaleops.com/blog/karpenter-vs-cluster-autoscaler/) ·
[Karpenter consolidation benchmark](https://cast.ai/blog/karpenter-cost-optimization-consolidation-benchmark/) ·
[Spot instance loss rates](https://leanopstech.com/blog/aws-spot-instances-when-to-use-2026/) ·
[AWS Batch spot best practices](https://docs.aws.amazon.com/batch/latest/userguide/bestpractice6.html) ·
[Spark on spot — spot.io](https://spot.io/blog/how-to-run-spark-on-kubernetes-reliably-on-spot-instances/) ·
[Kubernetes 1.35 in-place pod resize GA](https://kubernetes.io/blog/2025/12/19/kubernetes-v1-35-in-place-pod-resize-ga/) ·
[VPA in-place resize in production](https://www.rack2cloud.com/vertical-pod-autoscaler-in-place-resize-production/) ·
[AWS compaction best practices — MAX_FILE_GROUP_SIZE_BYTES](https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/best-practices-compaction.html) ·
[Iceberg optimization #9 — checkpointing/fail recovery](https://dev.to/alexmercedcoder/apache-iceberg-table-optimization-9-483m)

**Cryo's own prior work (grounding, not external research):**
`docs/native-iceberg-go-compaction-plan.md`, `docs/native-iceberg-rust-engine-errors.md`,
`docs/native-datafusion-compaction-plan.md`, `docs/s3-request-cost-tracking.md`,
`cryo/engines/native/routing.py`, `cryo/governor/capacity.py`, `cryo/governor/qpp.py`,
`cryo/planner/benefit_score.py`, `cryo/planner/planner.py`, `deploy/helm/cryo/values.yaml`.
