# Cryo implementation plan: differentiation and adoption

Date: 2026-09-28. Status: **in progress; G0–G4 remain open**. External installs, interviews,
and a billed comparison are not closed by code landing in this tree.
Code baseline: `5e5bb589e75894ca053f7b8867d49c7e74d8a7e7`, with concurrent working-tree changes.
Owners below are roles to assign, not staffing commitments. Numeric targets are acceptance
proposals, not current performance claims. This document contains no new production measurements.

## 1. Product outcome and scope

Build an independently installable, fully open Iceberg operations platform that:

1. Prioritizes useful maintenance within an explicit budget.
2. Identifies supported data-quality problems from metadata without full-table data scans.
3. Performs bounded maintenance and explains its observed effects and costs.
4. Fits an organization's existing catalog and execution engines.

The first target users are teams operating Iceberg with Trino/Spark who want lower operating
cost, less maintenance toil, and control over the software. Validate that hypothesis with five
operator interviews and two independent installation pilots before expanding integrations.
The competitive objective is lower total cost and operator effort at comparable correctness,
retention, and query performance. Feature count and paper citations are not success metrics.

### Relationship to existing work

This is the feature-to-delivery map for the discussion on 28 September. It does not replace
the detailed plans below or create duplicate implementations of their tasks:

| Existing authority | How this plan uses it |
|---|---|
| [Cost/reliability plan](CRYO_COST_REDUCTION_PLAN.md) and [recovery plan](CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md) | Unresolved execution, correctness, and recovery issues precede feature rollout. |
| [OSS launch plan](oss-launch-plan-2026-09-25.md), OSS-0–5 | Owns portability, artifacts, Helm, support, and independent installation. |
| [Competitive strategy](COMPETITIVE_STRATEGY_AMORO_LAKEOPS_2026-09-28.md), C1–C6 and CS tasks | Owns competitor research, evaluation protocol, migration, and claim gates. Its W1–W4 repair work feeds this plan; recheck progress before touching those files. |
| [Paper implementation audit](PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md) | Defines implemented versus experimental research behavior. |
| [Workload optimization spec](implementation/workload-aware-optimization.md) | Supplies detailed algorithm/API designs; reconcile older status statements against current code. |
| [AGENTS.md](../AGENTS.md), [HLD](../hld.md), [LLD](../lld.md) | Own architectural boundaries, parity requirements, migrations, and verification conventions. |

Planning this work does not deploy it, enable experimental paths, authorize outreach, or publish
comparisons. Coordinate with the owner of an existing task and update its evidence rather than
opening a second implementation. Do not commit unless the user asks.

### Landed in the working tree, not a closed gate

| Package | Landed | Still open |
|---|---|---|
| DP-00 | Nightly selection is `BatchMaintenance._select` via `selection.py`. It does not call `pack_fleet_budget` or `ROIGate.decide`. Initial public profile remains REST + S3-compatible storage + Spark. | Candidate commit record, CI failure list, tuned baseline capture, named owners. |
| DP-01 | W1/W3/W4 are on `gitlab-main`. `AuditLogRead` now includes file counts. The table Activity tab shows files in → out and "Bytes unknown" instead of zero. | Versioned evidence payload, seven-day coverage measurement, retry reconciliation. |
| DP-02 | Not started here. OSS-0–5 still own the chart. | Two non-PW installs. |
| DP-03 | `maintenance_writes_enabled` defaults true. When false, queue enqueue, a non-dry-run batch, worker claims, and native SQL fallback dispatch refuse writes. | Quality onboarding UI and the read-only pilot. |
| DP-04 | W2 is on `gitlab-main`: shadow mode does not turn itself off. | Durable reservation on the nightly path. |
| DP-05 | A disabled native destination may fall back only to `spark_emr`, not Trino. | Full delete and concurrency certification. |
| DP-06, DP-07 | Not started. Layout stays behind G4. Comparisons stay behind G3. | External evaluations. |

## 2. What exists and what must become a finished product

“Implemented” below means source and wiring were inspected; it does not certify every deployment.

| Capability | Existing engineering | Required delivery work | Buyer outcome |
|---|---|---|---|
| Budgeted prioritization | [Batch selection](../cryo/maintenance/selection.py) ranks file reduction, estimated GB-hours, and query heat; the [coordinator](../cryo/maintenance/coordinator.py) selects within a budget and calibrates throughput. | Consistent decisions, reservations, and outcome feedback across supported dispatch paths; missing-telemetry behavior. | Spend maintenance resources on useful work. |
| Metadata-based quality | [Constraints](../cryo/quality/constraints.py), [result persistence](../cryo/quality/constraint_store.py), and commit signals implement a supported subset with passed/failed/unknown states. | Read-only onboarding, coverage/freshness presentation, cross-scanner conformance, and a usable issue-to-action flow. | Find problems before granting maintenance permissions. |
| Targeted, protected maintenance | Partition/chunk limits, locks, deadlines, load-aware admission, and minor/major delete policy exist. | Certify the active paths and supported engine/table shapes; failure and concurrent-write tests. | Reduce unnecessary work and operational risk. |
| Workload-aware layouts | WAIR recommendations and partial PTO search exist; simulation predicts outcomes from samples. | Physical experiments, held-out query validation, bounded canaries, and regression monitoring. | Improve repeated queries while controlling rewrite cost. |
| Existing-stack integration | Separate sources/destinations; REST, Hive, Glue, Polaris, and Nessie factory adapters. | Tested catalog/auth/storage/engine combinations and complete public installation artifacts. | Adopt without redesigning the lakehouse. |
| Verifiable outcomes | Lane results, audit records, and impact history provide foundations. | Consistent per-action evidence, measurement coverage, resource attribution, and honest savings calculations. | Explain whether the investment paid off. |

Basic compaction, schedules, Helm, delete handling, and query-aware sorting are not unique to
Cryo. Differentiate through the measured combination of usability, budget control, quality
visibility, and outcomes. No overall advantage over Amoro or LakeOps has been established.

## 3. Delivery order and ownership

Package IDs below are navigation labels for this plan, not replacements for OSS/CS tickets.

| Package | Priority / owner | Prerequisites | Existing work to reuse |
|---|---|---|---|
| DP-00: candidate and baseline | P0 / maintainer + operator | None | Recovery plans; CS-01; OSS-0 |
| DP-01: outcome evidence | P0 / backend + engine | DP-00 | W1/W3/W4; C2; CS-03 |
| DP-02: portable installation | P0 / platform + frontend | DP-00 | C1; OSS-0–5; CS-02 |
| DP-03: read-only health and quality | P0 / scanner + frontend | DP-00; DP-02 for outside pilot | OSS-3; existing quality implementation |
| DP-04: one budget contract | P0 / planner + backend | DP-01 contract; DP-05 before write pilot | W2; C3; CS-04 |
| DP-05: execution and delete conformance | P0 for advertised writes / engine + test | DP-00; DP-01 evidence | Recovery work; C6; CS-05; OSS-4 |
| DP-06: measured layout improvement | P1 / optimizer + engine | DP-01/04/05 pilot gates | C4; CS-06; workload optimization spec |
| DP-07: comparison and adoption | P0 protocol, later publication / maintainer + operator | Protocol starts at DP-00; pilots require DP-02/03/05 | CS-08–11/13; OSS-5 |

Work on DP-01 and DP-02 can overlap after the baseline is recorded. DP-03 can develop against
local fixtures; DP-05 can extend correctness coverage while evidence plumbing is implemented.
Do not let layout research delay a working installation or reliable daily maintenance.

## 4. Work packages

### DP-00 — Establish the candidate and supported scope

- [ ] Record the candidate commit, relevant uncommitted changes, current CI failures, and the
  owners of overlapping W/OSS/CS tasks. Treat an in-progress patch as unverified until its gate passes.
- [ ] Inventory actual dispatch paths: scheduled batch lanes, worker/governor, manual execution,
  and any enabled event/native paths. Record the planner, admission, lock, execution, result,
  and reporting entrypoints for each. Dormant code does not establish deployment support.
- [ ] Choose the initial public profile: standard REST catalog + S3-compatible storage + Spark
  on Kubernetes, with Trino query observation optional. Certify further combinations separately;
  do not infer support for a cross-product from factory adapters. Retain configured existing
  Trino execution behavior while certifying its separate profile.
- [ ] Capture a tuned scheduled-maintenance baseline with fixed retention, representative
  ingestion/query workloads, measured resource consumption, and all failures/no-ops included.
- [ ] Record customer requirements for identity, networking, installation, query SLOs, and
  billing evidence. Assign a named owner to each package before estimating delivery dates.

**Done:** the release candidate, execution-path matrix, supported profile, baseline protocol,
and blockers are reviewable. No undocumented private dependency is required by the chosen profile.

### DP-01 — Make every attempted action explainable

**Implementation areas:** [lane results](../cryo/maintenance/results.py),
[coordinator](../cryo/maintenance/coordinator.py),
[Spark metrics](../cryo/engines/spark/procedure_metrics.py),
[runner](../cryo_spark_runner.py), audit models, and
[impact reporting](../cryo/reporting/impact_history.py).

- [ ] Reconcile W1/W3/W4 against the current code and complete their missing tests. Rewritten
  bytes must never be reported as reclaimed storage. A logical reduction in delete-file bytes
  is also not proof of physical deletion or a billing reduction.
- [ ] Extend existing result/audit contracts with a versioned evidence payload. Preserve legacy
  readers; add an additive migration where indexed fields or durable constraints require one.
  Do not reinterpret historical values silently or bulk-backfill unproven measurements.
- [ ] Record plan/policy identity, table/snapshot identity, input age, selection reason,
  engine/version, operation scope, attempt identity, timestamps, and outcome status.
- [ ] Preserve execution success separately from effect verification: committed change,
  verified no-op, partial completion, failed, and unverified outcome must remain distinguishable.
  A successful Spark process alone does not establish a changed Iceberg snapshot.
- [ ] Add bounded reconciliation for uncertain completion. Use durable operation identities;
  retries must neither double-count an effect nor hide resources consumed by failed attempts.
  Classify legitimate concurrent commits before assigning changes to Cryo.
- [ ] Export the same evidence through the API, run detail, and impact report. Display
  measured/estimated/unavailable/not-applicable per metric, with units, timestamp, method,
  coverage, and pricing provenance where relevant.

**Evidence fields must separate:** files removed/added, bytes rewritten, logical referenced-byte
change, verified physical object deletion, billable storage change, maintenance resources/cost,
and comparable query outcomes. Unknown bytes remain null. Metadata estimates remain estimates.
Optional deletion-byte probes need explicit request/time bounds; avoid an unbounded or repeated
object listing solely to populate the dashboard.

**Done:** every attempted pilot action has a durable status record; retry/crash scenarios reconcile
without duplicate effects. At least 95% of eligible successful actions have the predeclared effect
measurements over seven pilot days. Report coverage separately for files, bytes, and cost, alongside
success rate. Zero eligible actions means not applicable, not 100% coverage. Missing byte evidence
does not prevent honest file-count reporting, but prevents a physical-byte savings claim.

### DP-02 — Deliver an independent Helm installation

Execute OSS-0–5 under their existing owners; do not build an alternative chart or onboarding flow.

- [ ] Publish a coherent versioned set of chart, backend, UI, scanner, and required execution
  images, with reproducible build instructions, licenses, signatures/provenance, and SBOMs.
- [ ] Finish evaluation/production values, secure bootstrap, database/Redis wiring, migrations,
  portable service names, documented resource requirements, and optional placement settings.
- [ ] Remove implicit organization defaults for timezone, catalog/schema names, private images,
  node labels, pricing integrations, and endpoints. Preserve explicitly configured existing behavior.
- [ ] Make secret references, workload identity, trust bundles, permissions, and optional
  integrations explicit. An adopter must not need a particular Slack, Grafana, or AI account.
- [ ] Add install-time and in-app diagnostics for catalog access, engine permissions, storage,
  dependencies, and unsupported combinations. Return actionable failures without credentials.
- [ ] Run fresh-cluster install, restart/recovery, backup/restore, and supported upgrade checks.
  Publish a capability matrix tied to tested versions, not just adapter names.

**Done:** two non-PW operators complete install → authenticate → connect → scoped scan → preview
→ explicitly enabled maintenance → verified result using the published artifacts and docs.
Target first scoped scan within 30 minutes after documented prerequisites are met; record active
operator time, wall-clock time, prerequisites, and dataset size separately. Publish actual results.

### DP-03 — Make metadata-based quality the read-only entry point

**Implementation areas:** [constraints](../cryo/quality/constraints.py),
[constraint store](../cryo/quality/constraint_store.py),
[commit signals](../cryo/scanner/commit_signals.py), both scanners,
[quality API](../cryo/api/routes_quality_constraints.py), and existing table-detail UI.

- [ ] Offer a catalog-only discovery and quality workflow with no maintenance destination
  required. Use read-only credentials and ensure the selected mode cannot enqueue write jobs.
- [ ] Expose the supported rules: size, completeness, numeric/temporal range, size change, and
  freshness. Show the latest scanned snapshot, observation age, metric coverage, and unknown reason.
- [ ] Keep data arrival distinct from maintenance activity. After the source scan becomes stale,
  label freshness as observed at that scan; do not present an old pass as current pipeline health.
- [ ] Verify field-ID stability through renames, missing/truncated bounds, empty tables,
  deletes, schema evolution, and incomplete snapshot history. Preserve conservative lower bounds
  for delete-aware completeness; a percentage pass is not automatically preserved by deletions.
- [ ] Define when live-row counts are exact versus bounded/unknown, including overlapping
  position deletes and equality deletes. Match Go/Python scanner results on shared fixtures.
- [ ] Provide a rule setup flow, evidence-linked alerts with deduplication, and suggested next
  steps. Separate upstream pipeline issues from table-maintenance issues; do not automatically
  rewrite a table because a quality rule failed.
- [ ] Instrument metadata requests, CPU/memory, evaluation duration, and coverage. Measure
  incremental quality overhead over an ordinary scanner run; no full data scan for supported rules.

**Done:** an independent read-only pilot discovers a deliberately injected stale-arrival,
null-completeness, or range issue, while incomplete evidence produces unknown. Quality evaluation
does not fail the scanner transaction. A stale observation is visible in API, UI, and alerts.
Ship measured coverage/overhead; do not reuse LinkedIn's coverage percentages as Cryo results.

### DP-04 — Apply a common budget contract on supported execution paths

**Implementation areas:** batch selection/coordinator, [ROI gate](../cryo/governor/roi_gate.py),
admission, scheduler/worker, domain DTOs, and durable repositories.

- [ ] Share pure decision inputs and reason codes; adapt existing entrypoints to them. Preserve
  the batch pipeline instead of routing all work through a newly invented orchestration system.
- [ ] Separate the resource envelope (GB-hours, bytes, concurrency, deadline) from a monetary
  estimate. Dollar limits require declared prices and coverage; never manufacture zero cost when
  telemetry is missing. Enforce resource ceilings even when financial evidence is unavailable.
- [ ] Add durable budget reservation and settlement for the scope/time window. Use transactional
  admission and idempotent identities. Record estimated reservation, observed consumption, and
  uncertainty separately; charge each executed retry's real consumption once.
- [ ] Reconcile reservations after process loss, engine timeout, cancellation, and uncertain
  completion before releasing capacity. Enforce runtime/resource bounds and report any overshoot:
  an estimated budget cannot guarantee an exact cloud invoice ceiling.
- [ ] Preserve the AutoComp ranking and its labeled query-heat extension. Treat absent query
  telemetry as unknown, with a documented fallback; do not classify an unobserved table as unused.
- [ ] Keep shadow/enforced mode an explicit setting. Resolve W2; sample count may mark readiness
  but must not silently enable enforcement. Record policy version and mode in every decision.
- [ ] Add reserved capacity or explicit operator escalation for overdue required work. Weak
  benefit evidence must not indefinitely suppress policy-required cleanup, and cleanup must still
  respect retention, locks, resource bounds, and concurrent-writer safety.
- [ ] Reuse measured throughput and optional Ernest calibration. Prefer the learned estimator
  only when held-out results beat the baseline; retain the incumbent when evidence is weak.

**Done:** review at least 200 decisions stratified by size, activity, deletes, and telemetry
coverage. All advertised dispatch paths pass the same hard-constraint tests. Shadow remains
shadow after any sample count; concurrent admissions cannot double-reserve the same allowance.
A seven-day bounded pilot meets its predeclared service constraints, with a tested pause/reconcile
procedure. Skipped-job savings remain counterfactual estimates until controlled comparisons exist.

### DP-05 — Certify safe execution and efficient delete handling

- [ ] Preserve precomputed partition stats, GB/chunk caps, oversize-partition isolation,
  small-table behavior, cooldowns, per-table locks, and canonical operation order from
  [guardrails](../cryo/planner/guardrails.py). Preserve load probes on paths that execute on Trino.
- [ ] Verify source-to-destination capability checks before dispatch. A Spark-isolated profile
  should not fall back into Trino writes implicitly; separately configured Trino maintenance
  remains supported only with its own protection and evidence gates.
- [ ] Test position/equality deletes, mixed file sizes, partition evolution, concurrent writers,
  branches/tags, retention, lock loss, engine cancellation, restart, and partial success on
  the exact supported Iceberg/engine versions. Mark V3 or other unsupported shapes explicitly.
- [ ] Verify that minor delete maintenance avoids unnecessary data rewrites and that escalation
  to major maintenance is bounded. Measure read overhead, rewritten bytes, and maintenance cost;
  do not present existing thresholds as experimentally optimal.
- [ ] Compare logical query results before/after rewrites using deterministic fixtures and the
  existing correctness oracle. Verify committed state, not only process status or generated SQL.
- [ ] Keep native execution promotion separate: correctness, recovery, supported shapes, and
  measured total cost must justify it. Language choice alone cannot justify a release claim.

**Done:** required integration/recovery cases pass for each advertised write profile; a scoped
pilot completes the maintenance lifecycle with correct results and no unexplained writes.
Query p95/load limits are predeclared for the pilot. On regression, pause new dispatch and
reconcile running work; do not promise to undo physical deletion or concurrent writes.

### DP-06 — Turn layout recommendations into measured improvements

Keep this behind DP-01/04/05. Follow the research investment order: finish AutoComp feedback,
then PTO experiment infrastructure, then WAIR/other secondary enhancements. The existing WAIR
recommendation can remain available with its limits while this work proceeds.

- [ ] Retain PTO's heuristic/shadow label and WAIR's recommendation-only label. Record input
  coverage and assumptions; sampled pruning predictions are not physical experiment results.
- [ ] Build bounded candidate experiments with fixed baseline data and an unchanged-layout
  control. Use isolated copies for schema/partition experiments. A branch is usable only for
  operations with a tested isolation contract; Iceberg schema is shared across branches.
- [ ] Replay approved read-only query families with representative parameters, held-out samples,
  query/time/resource limits, and controlled cache/concurrency conditions. Captured SQL is not
  permission to execute arbitrary statements.
- [ ] Compare correctness, bytes read, CPU, p50/p95, temporary storage, rewrite/experiment
  cost, and payback horizon. Avoid optimizing the same query sample used to judge the winner.
- [ ] Validate WAIR's selected scope against the files the engine actually rewrites. Iceberg
  predicate semantics and sample coverage can broaden work; abort or revise the estimate when
  the real scope exceeds the reserved budget.
- [ ] Revalidate current snapshot/schema/spec and concurrent changes before applying a selected
  layout. Use an explicit bounded canary, operation-specific recovery, and drift monitoring.

**Done:** five controlled tables pass before expanding to at least twenty representative tables.
Require positive net benefit over a declared horizon and a proposed p95 regression margin of
no more than 5%, assessed with uncertainty and sufficient samples. Inconclusive results keep the
current layout. A production-support label applies only to the tested cohort/profile.

### DP-07 — Prove the advantage and make adoption repeatable

- [ ] Run a version-pinned comparison against a tuned scheduled Spark baseline and available
  comparable Amoro/LakeOps configurations. If an evaluation is unavailable, report that limitation;
  do not substitute vendor marketing numbers into the measured comparison.
- [ ] Hold ingestion, query mix, data scale, retention, storage region/prices, and service
  constraints constant. Use isolated equivalent table copies and one maintenance owner per copy.
- [ ] Count query compute, maintenance compute, scanner/control-plane dependencies, storage,
  requests, retries, experiments, and any applicable software fees once. Report operator time
  separately unless an explicit labor rate is agreed. Show unallocated costs and uncertainty.
- [ ] Measure warm-up and steady state across weekly workload patterns; target a 28-day
  observation for billing claims. Seven-day pilot gates establish operational evidence, not
  causal proof of durable savings. Predeclare sample adequacy and analysis before reporting wins.
- [ ] Publish sanitized fixtures, protocol, versions, configuration, raw evidence/coverage,
  and both favorable and unfavorable results. Contributor-reported internal figures are not
  publishable case-study evidence without retention and disclosure review.
- [ ] Deliver the existing CS-10 migration kit: inventory policies, map supported semantics,
  surface unsupported settings, establish ownership, rehearse handover, and test return of control.
- [ ] Finish contributor setup, good first issues, maintainer ownership, vulnerability reporting,
  compatibility/support policy, changelog, and release cadence through OSS-5.
- [ ] Track independent install → read-only value → first verified maintenance → four-week
  retained use. Collect feedback without mandatory telemetry or a hosted account. Outreach and
  publication are later actions, not performed merely because this plan lists them.

**Done:** two independent installations and a reproducible report with complete accounting.
The initial competitive target is at least 20% lower measured total cost than the tuned baseline
without breaching agreed correctness/retention/query constraints; it is a goal, not a release
guarantee or current claim. A claim against Amoro/LakeOps requires that exact comparison and
names the workload, versions, interval, coverage, and limitations.

## 5. Shared implementation contracts

Extend existing domain/result/audit structures rather than inventing a parallel event platform.
Specify DTO/OpenAPI changes and persistence migrations in the implementation PR before consumers
depend on them. Keep the planner pure and SQL in the engine-specific renderers.

| Contract | Required contents / invariant |
|---|---|
| Decision | Existing plan/table/source/destination identity; policy version; observed snapshot and age; execute/defer/skip reason; shadow/enforced mode; estimate basis and uncertainty. |
| Reservation | Budget scope/window, resource units, optional priced estimate, idempotency key, lifecycle, observed consumption, and reconciliation state. Never release uncertain running consumption merely because a lease timed out. |
| Execution evidence | Stable operation and attempt identities; scope; engine/version; submitted query/job IDs; commit evidence where applicable; status, retries, errors, timestamps, and effect metrics. |
| Quality result | Existing rule/field ID and snapshot identity; observation age; passed/failed/unknown; coverage; measured value or bound; reason. A later UI refresh does not refresh the source observation. |
| Impact aggregate | Coverage denominators and provenance by metric/engine; logical versus physical effects; query-workload comparability; estimates versus billed costs; deduplicated attribution. |

API/UI should expose these contracts through existing runs, tables, connections, and reports.
Use generated frontend types and shared UI primitives. Bound list/reconciliation queries,
avoid live manifest/file scans on request paths, and preserve secret/privacy controls in exports.

## 6. Validation and release gates

Reuse these existing suites and extend them for meaningful behavior changes:

| Area | Existing tests / additional acceptance |
|---|---|
| Selection and calibration | `test_autocomp_selection.py`, `test_autocomp_baselines.py`, `test_ernest.py`; missing telemetry, skew, cap semantics, and held-out model selection. |
| Execution evidence | `test_batch_maintenance_coordinator.py`, `test_batch_maintenance_units.py`, `test_spark_procedure_metrics.py`, `test_impact_history.py`, `test_impact_signals.py`; active lane wiring, crash/retry reconciliation, no double counting. |
| Quality | `test_quality_constraints.py`, `test_commit_signals.py`; scan parity, delete bounds, schema changes, stale observations, read-only integration. |
| Budget decisions | `test_roi_gate.py` plus integration tests for simultaneous admission, retries, timeouts, and every advertised dispatch path. |
| Layout and correctness | `test_wair_boundary.py`, `test_wair_report.py`, `test_layout_simulation.py`, existing `test_pto_*` and `test_pqs.py`; physical rewrite/replay tests are additional gates. |
| Packaging | Real fresh-cluster install, auth, connection, scoped scan, maintenance, restore, and upgrade; chart rendering alone is insufficient. |

For code changes run `make lint`, `make typecheck`, and `make test`, plus affected engine
golden/parity and integration checks. Run frontend build/tests when UI changes. Schema changes
need additive migrations; API changes need generated types. Record unavailable infrastructure
checks as open gates. Documentation-only changes need link/diff validation, not a fictitious
claim that integration or production tests ran.

| Gate | Allowed outcome |
|---|---|
| G0: DP-00 + unresolved recovery blockers classified | Begin implementation against the declared candidate. |
| G1: DP-02/03 read-only journey + OSS publication hygiene | Independently evaluable public alpha with explicit supported scope. |
| G2: DP-01/04/05 + OSS installation/recovery gates | Bounded maintenance beta; claims limited to the tested profiles. |
| G3: DP-07 comparable economic evidence | Publish the supported cost/outcome claims and case studies. |
| G4: DP-06 physical experiments/canaries | Promote specific workload-aware layout capabilities beyond experimental. |

## 7. Suggested sequence and first implementation batch

This is a planning envelope assuming backend, platform, and frontend capacity can overlap.
Confirm staffing and size the first batch after DP-00; time passing never closes a gate.

| Window | Focus | Reviewable deliverable |
|---|---|---|
| Days 0–7 | Baseline, ownership, W repair reconciliation, evidence contract, install blocker list | DP-00 record; small implementation tickets; stable benchmark protocol. |
| Days 8–21 | Outcome wiring, portable chart/artifacts, read-only quality flow, conformance fixtures | One disposable table's full evidence; reproducible local/CI adopter journey. |
| Days 22–42 | Independent installs, budget shadow review, recovery/concurrency tests | Two install reports; 200 reviewed decisions; scoped write-pilot readiness. |
| Days 43–70 | Bounded enforced pilots, migration rehearsal, weekly workload/billing observations | Seven-day operational gates plus longer economic evidence where available. |
| Days 71–90 | Retained adoption, publishable comparisons; layout experiments only if prerequisites pass | Scoped evidence-backed claims; layout remains experimental when gates are open. |

Create or reuse small tickets in this order:

1. DP-00: candidate/path/support matrix and existing-task owner reconciliation.
2. DP-01: finish W1/W3/W4 fixes where still missing; define evidence semantics before aggregating.
3. DP-01: active-path evidence persistence, API presentation, and retry/recovery validation.
4. DP-02: finish OSS artifact/bootstrap/dependency blockers and one real install CI profile.
5. DP-03: read-only quality journey, observation/coverage semantics, and conformance tests.
6. DP-04: resolve W2 and add shared shadow decisions, then durable reservation/reconciliation.
7. DP-05: certify the first write profile before any independent write pilot.
8. DP-07: conduct the independent evaluations and migration rehearsal; advance DP-06 only
   after the core gates pass and pilot demand supports the investment.

Update existing task checkboxes only when their evidence passes. Store candidate commit, test
results, sanitized fixture/report links, known gaps, and the next gate with each completion.

## 8. Scope boundaries and competitive evidence

Do not add a query router, ingestion framework, new table format, general-purpose DQ engine,
native-runtime rewrite, or autonomous layout rollout to the first release. Hot-table/event
evaluation remains a conditional follow-on: justify its demand and monitoring cost, then reuse
the same decision/lock/evidence contracts. Do not introduce mandatory remote telemetry or a
license service into the open product.

The market rationale comes from documented operational needs and competitor capabilities,
not a representative customer-demand survey. Recheck sources at comparison-publication time:

- [AutoComp](https://arxiv.org/abs/2504.04186): production motivation for resource-constrained compaction.
- [Zero-Scan Data Quality](https://arxiv.org/abs/2605.30308): production motivation for metadata-based quality; its scale and savings are not Cryo results.
- [WAIR](https://arxiv.org/abs/2602.23289): workload-aware selective reclustering; Cryo's adaptation has explicit limits.
- [Amoro self-optimizing](https://amoro.apache.org/docs/0.8.1/self-optimizing/): existing optimization and delete-maintenance capabilities.
- [LakeOps compaction](https://lakeops.dev/docs/compaction) and [simulations](https://lakeops.dev/docs/simulations): vendor-documented query-aware features; not independently benchmarked here.
- [S3 Tables maintenance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-maintenance.html): automated maintenance is already a standard managed offering.
- [Iceberg maintenance](https://iceberg.apache.org/docs/latest/maintenance/) and [branching](https://iceberg.apache.org/docs/latest/branching/): retention, deletion, and experiment-isolation constraints.

The release story we aim to earn is: **install Cryo, understand your tables, run useful work
within clear limits, and inspect the evidence of what improved.**
