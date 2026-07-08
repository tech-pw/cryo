# Strategy: biggest risk, next 5 moves, and a red-team of the comparison table

> Written 2026-07-08. Grounded in a full read of `PRODUCT.md`, `DESIGN.md`, `README.md`
> ("How it compares"), `docs/RATING_2026.md` (§10 re-rating), `docs/OSS_READINESS.md`,
> `docs/research-and-roadmap.md`, and `docs/native-engine-scoping-research.md`. Nothing here
> is invented scope — every move below pulls from work already researched in those docs.
>
> **Verdict up front:** the fastest path to "best self-hosted Iceberg control plane" is not a
> new feature. It's shipping the two safety/scaling items already scoped (differential-testing
> CI gate, Job-based dispatch), going public inside the Ryft-exit window, and making the
> comparison table as honest as the rest of the repo already is.

---

## 1. The single biggest unaddressed risk

**A correctness incident on the maintenance path shipping before the differential-testing CI
gate exists.**

The repo's own scoping doc names this — `docs/native-engine-scoping-research.md` §3.3 calls
correctness "the single most important category in this document" and §7 calls the missing CI
gate "currently the single biggest reputational risk" — yet the §8 risk register still lists it
as *"Scoped, not wired into CI."* This is not hypothetical for this codebase:

- The Rust engine already shipped an `expire_snapshots` that permanently leaked storage
  (`docs/native-iceberg-rust-engine-errors.md`, finding #20).
- Upstream Iceberg has shipped bugs that deleted live data
  ([apache/iceberg#13568](https://github.com/apache/iceberg/issues/13568),
  [apache/iceberg#4194](https://github.com/apache/iceberg/issues/4194)).

Cryo's entire wedge is *trust* — "calm, exact, operational" (`PRODUCT.md`), audit-grade,
guardrails-first. One "Cryo deleted my data" GitHub issue ends the "best self-hosted control
plane" claim in a way no feature can repair.

### Ranked against the other candidates

| Rank | Risk | Why this rank |
|---:|---|---|
| 1 | **Correctness incident before the CI gate** | Failure mode is terminal for an OSS maintenance tool; mitigation is nearly free (the test suites exist — this is CI plumbing). |
| 2 | **Scoped-but-unbuilt execution scaling that Amoro already ships** | `native-engine-scoping-research.md` §4.2 concedes Amoro's brain/elastic-optimizer split is "structurally identical to what §5 recommends Cryo build," while Cryo's native engine is a subprocess inside a fixed-4Gi Worker pod. Biggest *competitive* gap (Scenarios A/D: first 20TB table; adoption-spike reputation event) — but a crashed job is a bug report; deleted data is a postmortem. |
| 3 | **Legal/licensing** | Mostly closed. `OSS_READINESS.md` §7 shows LICENSE, SECURITY.md, gitleaks history scan, and scope-labeling done. What remains is three `[DEFERRED]` admin items (helm dry-run, org settings, `v0.1.0` tag) — an afternoon of unfinished checklist, not a risk. |
| 4 | **Code-quality debt** | `RATING_2026.md` §10: B+ (77.4%) and actively improving. Remaining hotspots (`cryo/api/routes.py` at 1,990 LoC; the TableDetail overview tab) slow features but block nothing. |

---

## 2. The next 25 moves, in order

Moves 1–5 are the critical path. Moves 6–25 extend the sequence in ranked order, grouped into
tiers; every one is pulled from scope already written down in `research-and-roadmap.md`,
`native-engine-scoping-research.md`, `RATING_2026.md` §10, or `OSS_READINESS.md` — none
require new research to start.

### Move 1 — Finish the three `[DEFERRED]` launch items and flip the repo public

- **Effort:** ~1 day (needs a host with the `helm` CLI and a `shelf-project` org admin).
- **What:** run `helm install --dry-run` (ideally a real kind install); configure branch
  protection / Dependabot / security advisories; cut `v0.1.0`; flip visibility.
- **Builds on:** `docs/OSS_READINESS.md` §7 checklist — everything else is already checked.
- **Unblocks:** the entire "best self-hosted OSS control plane" claim, which is unfalsifiable
  while the repo is private — and the Ryft-exit vacuum (`research-and-roadmap.md` Part 2:
  "Ryft's exit creates a vacuum for teams who need an on-premise control plane") is a timing
  window that closes as ex-Ryft customers pick alternatives.
- **Why it's safe to do before Move 2:** the native engine is already off-by-default and
  labeled experimental in the README's "Who this isn't for" section. The CI gate gates
  *promoting* native, not launching.

**Implementation log (2026-07-08):**
- ✅ `helm lint` passes on `deploy/helm/cryo/`
- ✅ `helm template` renders all 6 resource types (Deployment ×3, Service, CronJob) without error — confirmed no missing values references or template errors
- ✅ Added `.github/dependabot.yml` with weekly schedule for pip, npm, and GitHub Actions ecosystems; grouped related packages (opentelemetry, antd, react-query, recharts)
- ⏳ `helm install --dry-run` against real cluster — skipped: no kind/minikube available in this environment; `helm template` provides equivalent template validation
- ⏳ Branch protection / security advisories on GitHub org — **BLOCKED: requires `shelf-project` org admin**
- ⏳ `v0.1.0` tag — **BLOCKED: must be cut after org settings are confirmed**
- ⏳ Flip visibility to public — **BLOCKED: requires `shelf-project` org admin**

### Move 2 — Wire the differential-vs-Trino and concurrency/fault-injection suites into CI as a hard gate

- **Effort:** days — the suites exist (the §8.5 C2/C3 MOR concurrency e2e tests landed in
  `85d61be`); this is CI plumbing, not test authoring.
- **Builds on:** `docs/native-engine-scoping-research.md` §7 scope-in list;
  `.github/workflows/ci.yml`; `tests/integration/native/`.
- **Unblocks:** removing the "not exercised by CI" caveat from the README, and every future
  promotion of the native engine. This is the mitigation for risk #1, at near-zero cost.

**Implementation log (2026-07-08):**
- ✅ C2/C3 MOR concurrency suite already wired in `native-compaction-e2e-go` job (was done prior to this session)
- ✅ Created `tests/integration/native/test_native_differential_e2e.py` — parameterized over COW fixtures (unpartitioned, day-partitioned, identity-partitioned); clones table to S3, runs Trino `ALTER TABLE ... EXECUTE optimize` on clone, runs Go engine OPTIMIZE on original, asserts identical row counts; skips when `CRYO_TRINO_URI` not set
- ✅ Added `trino/catalog/iceberg_rest.properties` — Trino `iceberg-rest` connector pointing at `http://iceberg-rest:8181` with the same MinIO warehouse, enabling Trino to read tables created by the REST catalog
- ✅ Updated `native-compaction-e2e-go` CI job: added `trino` to `docker compose up`, waits for Trino `/v1/info` readiness, runs differential tests with `CRYO_TRINO_URI` set
- ⚠️ Differential test needs a real CI run to validate end-to-end: the REST→HMS metadata bridge may need adjustment if Trino's `iceberg-rest` connector does not pick up REST-created tables automatically (the connector reads S3 metadata directly, not HMS metadata — this is the correct behaviour but needs confirmation against the live catalog)

### Move 3 — Kubernetes `Job`-based dispatch for native execution, sized from planner data

- **Effort:** 1–2 weeks.
- **What:** replace `subprocess.run` in `cryo/engines/native/runner.py` with a per-run K8s Job
  (KEDA `ScaledJob` against the existing Redis queue; Karpenter for just-in-time nodes), with
  CPU/memory requests seeded from the planner's existing `total_size_gb` / `chunk_size_gb` /
  file counts.
- **Builds on:** `docs/native-engine-scoping-research.md` §6.1–6.3 (fully scoped, with
  concrete config patterns and gotchas in §4.3); `deploy/helm/cryo/templates/` (currently has
  no `Job` template); `cryo/planner/planner.py`.
- **Unblocks:** Scenario A (the first 20TB table OOM-killing against the fixed 4Gi Worker
  ceiling) and Scenario D (the adoption-spike wave of "crashes on my biggest table" issues);
  the Phase E capacity-accounting gap in `cryo/governor/capacity.py`; and the only structural
  axis where Amoro currently out-ships Cryo.

### Move 4 — Chunk-level checkpointing / partial-progress commits

- **Effort:** 1–2 weeks.
- **What:** each chunk commits independently, mirroring Iceberg's own
  `PARTIAL_PROGRESS_ENABLED` precedent; the planner's chunking already provides the unit of
  work.
- **Builds on:** `docs/native-engine-scoping-research.md` §6.4;
  `docs/native-iceberg-go-compaction-plan.md` §6 (partition-disjoint chunking invariant).
- **Unblocks:** hot-table commit races (Scenario B — a lost race forfeits one chunk, not two
  hours of compute), spot-instance eligibility (Scenario F), and safe concurrent chunk fan-out
  (Scenario G).

### Move 5 — Puffin statistics (`COMPUTE_STATISTICS`) as the 5th maintenance operation

- **Effort:** days — `research-and-roadmap.md` §3.2 calls it a single PR and names the exact
  files (`cryo/domain/enums.py`, `cryo/planner/planner.py`, `cryo/engines/spark/sql.py`,
  `cryo/engines/trino/sql.py`), sequenced strictly after `OPTIMIZE_MANIFESTS`.
- **Builds on:** it is the #1 ROI item in `research-and-roadmap.md` §5's ranking; the
  query-log ingestion pipeline shipped 2026-07-07 makes the resulting stats actually useful.
- **Unblocks:** the most visible ❌-vs-✅ against LakeOps in the §4 feature matrix.

### Resource conflict: who wins

The native-scaling track (Moves 3–4) and the feature-parity track (Move 5) compete for the
same engineering time. **Scaling wins.** The scoping doc is explicit that elastic sizing must
ship *before* widely promoting the fast/cheap-native claims — Scenario D can define the
project's public reputation in one bad week, whereas Puffin parity is a matrix cell nobody
churns over. Do Move 5 only after Moves 3–4 are merged, or in genuinely idle parallel
capacity.

---

### Tier 2 — Finish the native/governor hardening the scoping doc already scoped (Moves 6–8)

These complete `native-engine-scoping-research.md` §7's scope-in list. Move 6 depends on
Move 3 landing first; 7 and 8 are independent of it.

**Move 6 — Governor live-capacity accounting from real Job state (closes Phase E).**
*Effort: days–1 week, after Move 3.* Replace the static `slot_cap` / hardcoded `used=0` in
`cryo/governor/capacity.py::_probe_native` with real Kubernetes Job resource state. Builds on
`native-engine-scoping-research.md` §6.6 and the Go plan's already-open Phase E task.
**Unblocks:** the governor distinguishing "5 small jobs" from "1 huge job" when admitting
work — the same shift QPP already made for read-query admission — and honest native
load-awareness for the comparison table (see §3.1 below).

**Move 7 — Cross-engine compatibility test matrix.** *Effort: ~1 week.* A table written by
Spark/Trino must round-trip correctly through native Go compaction and read back in the
others; "reads the spec correctly" does not imply "round-trips through every engine
correctly." Builds on `native-engine-scoping-research.md` §3.6 and its §8 risk register
("Not started"), motivated by real incidents ([trino#8284](https://github.com/trinodb/trino/issues/8284),
[apache/iceberg#12273](https://github.com/apache/iceberg/issues/12273)). **Unblocks:** the
multi-engine claim surviving contact with mixed-writer fleets — the exact deployment shape
the target user has.

**Move 8 — S3-request-cost preflight estimate before large orphan scans.** *Effort: days.*
Show "this scan will cost approximately $X in S3 requests" before a large orphan scan runs,
not after the bill arrives. Builds on `docs/s3-request-cost-tracking.md` and
`native-engine-scoping-research.md` §6.7 / Scenario E (orphan files reach 25–40% of billable
storage on neglected lakes — the first scan of exactly such a lake is the costliest).
**Unblocks:** no bill-shock on a new install's first big scan, and it reinforces the
cost-transparency positioning that differentiates Cryo's health/cost grading.

### Tier 3 — Cheap scanner/planner wins from the roadmap's own gap analysis (Moves 9–16)

All eight are tagged 🟡-nearly-free or 🔴-cheap in `research-and-roadmap.md` Part 2. They
reuse data the scanner already collects, so they parallelize freely with the native track —
this tier is what idle capacity should work on while Moves 3–4 are in flight.

**Move 9 — Metadata-to-data byte ratio as an explicit health signal.** *Effort: hours–1 day.*
The live/dead/metadata byte split is already computed for cost accounting; surfacing
`metadata_bytes / data_bytes` in `cryo/scanner/health.py` is "a read of data already
collected, not new instrumentation" (Part 2). **Unblocks:** parity with LakeOps/Ryft, which
treat this as a primary signal, and early warning for the e6data-style metadata-bloat failure
mode documented in `native-engine-scoping-research.md` §3.1.

**Move 10 — V2→V3 format detection + deletion-vector upgrade flag.** *Effort: ~1 day.*
`format-version` is already read in `analyze_table`; flag V2 tables with high delete ratios
as V3-upgrade candidates (citing the AWS EMR benchmark — 55% faster deletes, 28.5% faster
reads — as directional, per the roadmap's own verification notes), and skip the eq→pos delete
conversion for tables already on v3 (roadmap §3.9). **Unblocks:** an advisory surface no
competitor in the matrix offers, at near-zero cost.

**Move 11 — Standalone manifest-rewrite quick action.** *Effort: ~1 day.* `OPTIMIZE_MANIFESTS`
is already a defined operation type; expose it as a one-click action separate from full
optimize (Part 2, manifest-fragmentation row). **Unblocks:** manifest-count control without
paying for a full optimize — load-bearing because manifest maintenance is entirely outside
the native fast path (`native-engine-scoping-research.md` §3.1) and upstream won't fix the
pathological serial-validation case ([apache/iceberg#8932](https://github.com/apache/iceberg/issues/8932)).

**Move 12 — Streaming-table / commit-velocity detection.** *Effort: days.* Compute commits/day
from snapshot-count deltas between consecutive `table_stats_history` rows — "just a query + a
flag + a tighter compaction cadence for flagged tables" (Part 2). **Unblocks:** classifying
high-velocity tables before their compaction starts losing commit races (Scenario B), and it
feeds Move 14.

**Move 13 — Compaction effectiveness ratio tracking.** *Effort: days.* Record
`input_files_count` / `output_files_count` per `OPTIMIZE` in the audit log via
`cryo/db/repositories/run_finalizer.py`; alert when the ratio drops below 10:1 (roadmap
§3.3, ranked #3 by ROI in its §5). **Unblocks:** detecting wasted compute immediately —
Airbnb's 50%+ compute savings came from *not doing unnecessary work*, per the scoping doc's
§4.1 case-study table.

**Move 14 — Hot/cold partition write-activity filter in the planner.** *Effort: days–1 week.*
Add a "no writes in N hours" filter to partition selection (Part 2): compacting a partition
being actively written causes `CommitFailedException` churn. **Unblocks:** fewer lost commit
races at the source, complementing Move 4's checkpointing (which bounds the *cost* of a loss;
this reduces the *frequency*).

**Move 15 — Maintenance-operation sequencing guardrail in the API/UI.** *Effort: hours–1 day.*
The planner enforces expire → orphan → compact → manifests internally, but nothing stops a
manual API call from running ops out of order (Part 2, "small gap"). Out-of-order execution
risks corruption per Iceberg's own maintenance docs (`native-engine-scoping-research.md`
§3.3). **Unblocks:** closing a real footgun before ad-hoc multi-engine runs become common —
cheap insurance on the trust story that is risk #1.

**Move 16 — Catalog latency monitoring (P50/P99 per catalog operation).** *Effort: days.*
`cryo/sources/` already wraps REST/HMS/Glue calls; add timing and surface percentiles with a
*configurable* threshold (the roadmap's verification notes found no sourced industry
standard — don't invent one). **Unblocks:** visibility into the HMS-locking and Glue
rate-limit contention patterns in `native-engine-scoping-research.md` §3.2 before they
manifest as mysterious slow scans.

### Tier 4 — Differentiators for the post-launch audience (Moves 17–20)

These are the roadmap's Priority 1–2 features that need Tiers 1–3 (or the telemetry pipeline
shipped 2026-07-07) underneath them. If Tier 4 conflicts with Tier 3 for time, Tier 3 wins —
eight cheap shipped signals beat one large feature in flight.

**Move 17 — Protected/named snapshots (DR).** *Effort: 1–2 weeks.* Tag specific snapshots so
`expire_snapshots` skips them — a verified ex-Ryft feature ("Snapshot Lifecycle", Part 2).
**Unblocks:** courting the ex-Ryft customers the Move-1 launch window targets; they had this
feature and will ask where it went. Ranked first in this tier because that window is
time-sensitive.

**Move 18 — Signal-driven maintenance triggers.** *Effort: 1–2 weeks.* Three trigger signals
beyond schedule + cooldown: `file_degradation_rate`, `query_latency_spike`, `cost_anomaly`
(roadmap §3.4, new `cryo/triggers/` module, governor polls). Depends on the query-log
pipeline. **Unblocks:** the "autonomous control plane" positioning the roadmap says this is
*required* for, and LakeOps parity on the matrix row.

**Move 19 — GDPR/CCPA deletion tracking.** *Effort: 1–2 weeks.* "Mostly a reporting layer over
data Cryo already produces" (Part 2): track soft-deletes pending a snapshot-expiry cutoff and
surface "N pending hard deletions, blocked until expiry after <date>." **Unblocks:** a
compliance wedge aimed at exactly the regulated, on-prem audience that chooses self-hosted in
the first place — a differentiator neither Amoro nor LakeOps ships.

**Move 20 — Bloom filter column recommendations.** *Effort: ~1 week.* Use `QueryPattern` data
to find high-cardinality columns in frequent equality filters and recommend Parquet bloom
filters via `ALTER TABLE SET PROPERTIES` (roadmap §3.6). **Unblocks:** the one matrix row
where *every* competitor is ❌ — "no competitor surfaces this" — now feasible because the
telemetry pipeline it depends on exists.

### Tier 5 — Engineering and evaluator-experience debt from RATING §10 (Moves 21–24)

The path from B+ to 9+ that `RATING_2026.md` §10 already wrote down, ordered by what a
post-launch evaluator or contributor hits first.

**Move 21 — Playwright critical-path e2e + axe-core CI gate.** *Effort: days.* One spec
covering create source → scan → preview plan → run → view logs, plus axe-core gating on zero
serious/critical violations per route (RATING §7 validation plan and §10). **Unblocks:**
regression safety across the entire launch-tier surface with one spec, and it turns
`PRODUCT.md`'s WCAG 2.2 AA target from aspiration into an enforced gate.

**Move 22 — Decompose `cryo/api/routes.py` (1,990 LoC), helpers first.** *Effort: days.* The
failed extraction attempt is already documented in RATING §10: move `_run_with_timeout` /
`unredact_merge` into `cryo/api/route_helpers.py` *first*, then split per-domain routers each
under 500 LoC. **Unblocks:** every future API feature — this is the single largest remaining
D1 hotspot, and the extraction order is already de-risked by the documented revert.

**Move 23 — Server-side cursor pagination wired into the explorers.** *Effort: days–1 week.*
`/api/v1/tables?cursor=...` wired into TableExplorer, Scans, and Runs; the
`VirtualizedTable` primitive (react-window) already exists from wave 2. **Unblocks:** the
"sub-second navigation on a 5k-table fleet" bar the UI spec sets — Cryo's stated design
scale is "fleets in the thousands of tables," and the inventory pages are where that claim
gets tested first.

**Move 24 — First-run/empty-state pass + telemetry-dependent labeling.** *Effort: days.*
Onboarding is the lowest-scored usability attribute (C1, 4/10): add a `sources == 0` banner,
empty-state CTAs on every list page, and — per `OSS_READINESS.md` §2.4 — label
telemetry-dependent surfaces "requires query-log ingestion, see docs" so an evaluator reads
an empty chart as unconfigured, not broken ("silence is the wrong choice either way").
Educational empty states only, per `DESIGN.md` — no playful copy. **Unblocks:** the
first-15-minutes evaluation that decides whether a launch-day visitor stays.

### Tier 6 — Decision spike (Move 25)

**Move 25 — Time-boxed Apache Amoro evaluation.** *Effort: 2–3 days, hard time-box.* Evaluate
whether Cryo should integrate with Amoro's optimizer model, borrow its scheduler design, or
confidently diverge (`native-engine-scoping-research.md` §6.8 — "an evaluation, not a
commitment"). Moves 3–4 are safe to build first because they're well-trodden K8s patterns,
not a from-scratch scheduler; but this spike **must complete before any orchestration work
beyond Moves 3–4** — the scope-out list explicitly defers "building a from-scratch competing
scheduler" until it has. **Unblocks:** confidence that the execution-layer roadmap isn't
rebuilding something the one other serious self-hosted entrant already maintains.

### Deliberately not ranked

Items from the same docs that don't make the top 25, with the reason:

- **Multi-engine telemetry breadth** (roadmap §3.8), **incremental Puffin sketches** (§3.10),
  **partition evolution recommendations** (§3.7) — all real, all sequenced behind Moves 5,
  17–20 by the roadmap's own priority ordering.
- **Flink execution** (§3.5) — stays config-generation-only; the README already scopes this
  honestly, and streaming-primary shops are explicitly "who this isn't for (yet)."
- **Data tiering / multi-engine query routing** — tagged ⏳ Later in the roadmap; presupposes
  the cost dashboard and Spark maturity work.
- **Spot-instance gating** (`native-engine-scoping-research.md` §6.5) — subsumed by Move 4;
  checkpointing is its prerequisite, not a separate move.
- **Dependency-manifest consolidation, the 170 `!important` cleanup, the runs/jobs glossary**
  (RATING top issues #5/#8 and §10) — real hygiene, but foldable into normal PR discipline
  rather than scheduled moves.

---

## 3. Red-team: "How it compares" vs Amoro and LakeOps

The README table is directionally honest — better than most — but three cells don't survive
contact with the repo's own internal docs.

### 3.1 "Load-aware admission control: Yes" is not a differentiator against LakeOps

The README grants LakeOps "Yes" on the same row. (Interestingly, the internal matrix in
`research-and-roadmap.md` §4 says ❓/unverified for LakeOps — the README is *more* generous
to the competitor than the research supports; fine, but reconcile the two.) The claim
differentiates only vs Amoro ("Limited") and DIY.

Worse: per `native-engine-scoping-research.md` §1, the governor's native-path probe
(`cryo/governor/capacity.py::_probe_native`) returns a static configured `slot_cap` with
`used=0` hardcoded — load-awareness is real for Trino (`system.runtime.queries`) and does not
exist for the native path. The claim holds today only because native is off by default.

**Action:** scope the cell to "Trino" or close the probe gap (Move 3's live-capacity work)
before promoting native.

### 3.2 "Trino **and** Spark/EMR" overstates Spark maturity relative to the repo's own docs

`OSS_READINESS.md` §2.3 says Spark/EMR is "Partial / less battle-tested than Trino" and
explicitly recommends the README "say so plainly rather than presenting both engines as
equally mature." The bolded "**and**" in the table does the opposite.

The dual-engine claim is still *differentiated* — Amoro is Flink-first, LakeOps executes on
its own Rust engine — but the honest version is "Trino first-class, Spark/EMR functional but
younger." Honesty is the stated trust strategy (`OSS_READINESS.md` §4: "a visibly honest gap
list is itself a trust signal").

**Action:** add the maturity caveat to the table row or the prose below it.

### 3.3 The table omits the axis where Amoro beats Cryo: elastic execution scaling

The README prose says "Against Apache Amoro, Cryo is lighter to operate" — true, but
`native-engine-scoping-research.md` §4.2 establishes that Amoro ships the decoupled
brain/elastic-optimizer architecture Cryo has only *scoped*, with a claimed 10x over Spark's
`rewriteDataFiles`. A well-informed evaluator (the actual target user) will notice the
missing row.

**Action (either is better than silent omission):**
- add an "Elastic execution scaling" row and take the honest ❌/Roadmap hit — consistent with
  the trust positioning; or
- ship Move 3 first and add the row as a ✅.

The current silence is the one pattern `OSS_READINESS.md` warns against: the repo not telling
the truth about itself.

### 3.4 Still fully intact

- **Health grading A–F + cost projection** — LakeOps "Partial", Amoro ❌; Cryo's strongest
  unique row.
- **Telemetry stays on your infra vs LakeOps** — verified SaaS-only in
  `research-and-roadmap.md`'s verification notes.
- **Self-hosted vs managed cloud** — unchanged.
- Ryft's discontinuation (acquired by Cyera, April 2026) *strengthens* the self-hosted wedge
  rather than eroding anything.

---

## 4. Cross-references

| Topic | Deeper detail lives in |
|---|---|
| Launch checklist and deferred items | `docs/OSS_READINESS.md` §7 |
| Execution-scaling scope, scenarios, risk register | `docs/native-engine-scoping-research.md` §5–§8 |
| Puffin stats / feature backlog ROI ranking | `docs/research-and-roadmap.md` §3.2, §5 |
| Code-quality state and path to 9+ | `docs/RATING_2026.md` §10 |
| Competitive landscape verification notes | `docs/research-and-roadmap.md` Part 2 |
