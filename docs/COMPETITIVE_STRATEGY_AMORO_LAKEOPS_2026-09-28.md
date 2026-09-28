# Competitive strategy: Cryo, Apache Amoro and LakeOps

## Executive summary

Promise: Iceberg maintenance you control, with results you can verify. Win first with teams
that already run Trino or Spark and want both the software and the evidence.

Three P0 packages, in order: C1 independent installation, C2 a trustworthy outcome record,
C3 one budget contract on the path that actually runs. Layout search and a published
head-to-head wait behind those.

Allowed today, as a fact about Cryo and not a ranking: Cryo is an Apache-2.0, self-hosted
Iceberg maintenance platform. Connect a supported catalog and engine, inspect the plan, and
control the scope and budget. See §8 for the exact wording and §10.5 for everything else.

Banned claims live in one list, §10.5. Do not say Cryo is the better tool, the only open
optimizer, or that a modeled dollar figure is a bill. Do not quote a competitor's benchmark
back at them.

Four facts that must not be "fixed" away: layout autopilot does nothing with zero presets;
the staleness boost does not touch the nightly batch; the event watcher is not deployed;
do not turn the native engine back on to recover reclaim numbers.


Date: 2026-09-28. Status: **research-backed proposal; comparative wins are not yet measured**.
Publish only the protocol, comparison pages and results that clear their gates.
Code baseline inspected: `4079d5840b8309e15c6c6b4b8fd8fd72b13fc323`.
Companions: [OSS delivery plan](oss-launch-plan-2026-09-25.md),
[cost/reliability plan](CRYO_COST_REDUCTION_PLAN.md),
[paper implementation audit](PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md).

Section 10 preserves the same-day engineering addendum, reconciled with the execution paths
and evidence rules in sections 1–9. Its production observations (10.2), the competitive
campaign (section 11) and Appendix A are in `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`.

---

## 0. Implementation runbook (for the worker AI — do this first)

This section turns the review of this document into exact, ordered work. **An implementing
agent executes section 0 only.** Sections 1–11 are context; they authorize no other changes.
Every fact below was checked against source and production on 2026-09-28 at commit
`4079d58`. If a fact no longer matches what you find, **stop and report** — do not adapt the
task to fit.

**Execution status (2026-09-28): done on `gitlab-main`.** W1 `5078d6f`, W2 `6220ace`,
W3 `ca98cf2` + review fix `00d1de8`, W4 `140b7ab` + review fix `d659ec0`, W5 `9ec6b8a` + the
commit that added this line. W1–W5 reached production in deploy pin `ed250dd`; the two review
fixes and this doc update are not deployed. Findings are listed under 0.5. Do not merge branch
`feat/competitive-runbook` commit `7284b9d`: it is a second W4, superseded by `140b7ab`.

### 0.1 Ground rules (read all before touching code)

1. **Read before editing.** Open every file named in a task in full before changing it.
   Match the surrounding style: `from __future__ import annotations`, full type hints, no
   narrating comments, comments only for non-obvious intent.
2. **One task = one commit**, conventional message (`fix(...)`, `feat(...)`, `docs(...)`),
   ending with the repo's co-author trailer. Stage only the files the task names
   (`git add <paths>`, never `git add -A` / `git add .`).
3. **Do not touch unrelated dirty files.** At the time of writing these were modified by
   other work and must stay out of your commits: `docs/EXECUTION_PLAN.md`, `docs/README.md`,
   `todo.md`, `docs/cost-tracking/daily-cost-log.csv`. Run `git status` before each commit.
4. **Quality gate per task, no exceptions:** `make lint && make typecheck` plus the task's
   named tests, then `make test` before the final commit. Never use `--skip-tests`, never
   delete, skip or weaken an existing assertion to make a test pass. If an existing test
   encodes the bug you are fixing, change that single assertion and say so in the commit body.
5. **No production actions.** Do not run `deploy/deploy.sh`, `helm`, `kubectl` writes,
   Alembic against prod, or change `deploy/helm/cryo/values-prod.yaml`. Do not flip any
   feature flag default in `cryo/config.py` unless the task says so. Deployment is a separate
   human decision.
6. **No backfill or rewrite of historical rows** (`cryo.audit_log`, `fleet_impact_history`).
   Fixes apply going forward only.
7. **Unknown is not zero.** Anywhere you are not certain a value was measured, store `None`.
   Never coerce missing bytes to `0`.
8. **Stop conditions:** a precondition in a task fails; a fix needs a schema migration not
   listed; `make test` fails outside the files you touched; or you are about to change
   behavior on a path the task does not name. Stop, leave the tree clean (or stash your own
   work only), and report what you found.

### 0.2 Tasks, in order

#### W1 — Stop recording rewritten bytes as freed bytes (bug)

- **Why:** `cryo/engines/spark/procedure_metrics.py::bytes_freed_from_procedure` returns
  `rewritten_bytes_count` for `optimize` (lines ~41–42). That is bytes *read and rewritten*,
  not bytes freed. Iceberg's `rewrite_data_files` does not return an added-bytes figure, so
  a net value cannot be computed from its result.
- **Change:** the `optimize` branch returns `None`. Leave `rewrite_position_deletes`
  (already nets `rewritten - added`), `expire_snapshots` and `remove_orphan_files` unchanged.
  Update the module docstring only if it now reads inaccurately.
- **Tests:** add `test_optimize_rewritten_bytes_are_not_freed_bytes` to
  `tests/test_spark_procedure_metrics.py` asserting
  `bytes_freed_from_procedure("optimize", {"rewritten_bytes_count": 500}) is None`.
- **Precondition check:** `grep -rn "bytes_freed_from_procedure" cryo cryo_spark_runner.py`
  should show only `cryo_spark_runner.py` (legacy per-statement path) and the definition.
  If another production caller exists, stop and report.
- **Done when:** named test passes; lint/typecheck clean.

#### W2 — ROI gate must never leave shadow mode by itself (latent safety bug)

- **Why:** `cryo/governor/roi_gate.py::ROIGate.decide` (lines ~99–100) sets
  `shadow_mode = False` once a table's `raw.smart_maintenance.roi_decisions` history reaches
  `roi_shadow_min_samples` (200). That silently starts SKIP/DEFER enforcement while
  `CRYO_ROI_GATE_SHADOW_MODE` is still true. History is capped at 200
  (`_persist_shadow_observation`, `events[-200:]`), so once reached the switch is permanent.
  Production max today is 24 decisions per table — not triggered yet.
- **Change:** delete the automatic override. Enforcement happens only when the setting is
  false. Keep `_shadow_ready` but use it only to populate a new optional
  `ready_for_enforcement: bool = False` field on `GateDecision` (so an operator can see
  readiness). Do not add any code path that flips enforcement.
- **Tests:** in `tests/test_roi_gate.py`, add a test that seeds a table whose
  `stats.raw["smart_maintenance"]["roi_decisions"]` has 200 entries, leaves the shadow
  setting at its default (true), calls `gate.decide(..., record=False)`, and asserts
  `decision.shadow is True` and `decision.ready_for_enforcement is True`. Reuse the file's
  existing `_seed_table` / `_seed_stats` / `_job_for` helpers; read them first.
- **Also check:** `grep -rn "GateDecision(" cryo tests` — every constructor call must still
  work (the new field has a default).
- **Done when:** new test passes, `tests/test_roi_gate.py`,
  `tests/test_governor_shadow_sampling.py`, `tests/test_benefit_score_integration.py` pass.

#### W3 — Record Spark lane evidence on the batch audit row (fixes "0 of N actions reported a size")

- **Why:** production maintenance runs the lane path (`cryo_spark_runner.py::_run_manifest`)
  and writes audit rows through `cryo/maintenance/coordinator.py::_audit_row`. `_audit_row`
  never sets `actual_bytes_freed`, and `_run_manifest` never measures anything, so every Spark
  action is "unmeasured". The per-statement path does measure but is not the production path.
- **Change (coordinator):** in `_audit_row`, when `result.ok`, set
  `actual_bytes_freed=bytes_freed_from_procedure(result.action, result.metrics)`. After W1
  this yields: `rewrite_position_deletes` → net bytes; `expire_snapshots` → `0` only when
  every count is zero, else `None`; `optimize` → `None`; `remove_orphan_files` → value only
  if the lane supplied `bytes_removed` in metrics. Keep the existing
  `input_files_count`/`output_files_count` logic unchanged.
- **Change (runner, orphan bytes, opt-in):** in `_run_manifest`, for statements whose
  `stmt["action"] == "remove_orphan_files"`, measure bytes with the existing
  `_orphan_bytes(spark, sql)` helper **before** executing the statement, and put the result
  in `metrics["bytes_removed"]` when not `None` — but only when a new manifest-level flag
  `measure_orphan_bytes` is true. Default false: the dry-run doubles the orphan LIST, and
  orphan LIST has OOM'd lanes before. Carry the flag from a new setting
  `maintenance_measure_orphan_bytes: bool = Field(default=False, ...)` in `cryo/config.py`,
  written into the manifest by `cryo/maintenance/manifest.py` (read how other params such as
  `retention_days` reach the manifest and follow that exact path). A failed probe logs and
  leaves bytes unmeasured; it must never fail the statement.
- **Tests:**
  - `tests/test_batch_maintenance_coordinator.py`: an `ok` optimize result with
    `rewritten_bytes_count` yields an audit row with `actual_bytes_freed is None`; an `ok`
    `rewrite_position_deletes` with `rewritten_bytes_count=100, added_bytes_count=40` yields
    `60`; an `ok` orphan result with `metrics={"bytes_removed": 7}` yields `7`; a failed
    result yields `None`. Find how existing tests build `StatementResult`/`TableOutcome` and
    reuse it.
  - `tests/test_batch_maintenance_units.py`: manifest includes `measure_orphan_bytes`
    reflecting the setting; runner stub test that with the flag false no dry-run SQL is
    issued. If the runner cannot be exercised without pyspark, cover the manifest part only
    and say so in the commit body.
- **Do not** turn the new setting on in Helm values.
- **Done when:** named tests pass; full `make test` green.

#### W4 — Carry engine provenance in the daily impact ledger

- **Why:** all measured reclaim recorded since 9 July came from the **native** engine
  (`audit_log.engine = 'native'`; totals are in the private file), the engine disabled on
  17 September for reporting success without committing. Reports must show which engine a
  byte figure came from.
- **Change:** in `cryo/reporting/impact_history.py::record_daily_impact`, add one grouped
  query over the same day window (`day_bounds(now, 1)`) summing
  `AuditLog.actual_bytes_freed` and counting rows with non-null bytes, grouped by
  `AuditLog.engine`, for the reclaim actions and success statuses used by
  `cryo/reporting/impact_signals.py` (`_RECLAIM_ACTIONS`, `_SUCCESS_STATUSES` — import them;
  do not redefine). Store under `raw["bytes_freed_by_engine"] = {engine: {"bytes": int,
  "actions_measured": int}}`. No schema change (column `raw` is JSONB).
- **Tests:** extend `tests/test_impact_history.py` with rows from two engines and assert the
  per-engine split. Remember `tests/conftest.py` `_AUTOINCREMENT_ID_MODELS` already includes
  `FleetImpactHistory`.
- **Done when:** named tests pass.

#### W5 — Split this document (docs-only)

- **Why:** this file mixes a public-safe strategy with an internal campaign and unretained
  production figures, and it sits in `docs/` of a repo whose `origin` remote is public.
- **Change:**
  1. Create `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md` (this path is gitignored —
     verify with `git check-ignore -v docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`;
     stop if it is not ignored). Move into it, verbatim: section 10.2, section 11, and
     Appendix A.
  2. In this file, replace each moved section with a one-line pointer to the private file,
     delete the "Distribution: internal" header line, and keep sections 0–9, 10.1, 10.3–10.7
     and Appendix B.
  3. Add a 12–20 line **Executive summary** directly under the title: the promise
     (§1), the three P0 packages (C1–C3), the claims allowed today (§8 "code-backed
     wording"), the banned claims list (§10.5 "Banned"), and the four correctness facts in
     0.3 below.
  4. Remove the duplicated "do not say" lists: keep §10.5 "Banned" as the single list; in §8
     and §11.10 replace duplicates with "See §10.5."
- **Tests:** none; run `grep -n "_private-pw" docs/COMPETITIVE_STRATEGY_AMORO_LAKEOPS_2026-09-28.md`
  to confirm pointers, and confirm no internal figures from §10.2 remain in this file.
- **Commit** only this file (the private file is ignored by design).

### 0.3 Correctness facts the worker must not "fix" away

- **Layout autopilot is a no-op in production today.** `_run_layout_autopilot`
  (`cryo/scheduler/service.py`) applies a recommendation only when the global flag is on
  **and** the table's effective preset sets `params.auto_apply_layout_recommendations`.
  Production has **0 presets and 0 table assignments** (all deleted 2026-09-24). Enabling
  the flag changes nothing. Measurement exists (`applied_prune_efficiency_baseline` →
  `observed_prune_improvement`, PDAE-8288) but has 0 observations. A preset-free opt-in
  (e.g. by source/namespace scope) needs a human design decision — **blocked, do not build.**
- **The staleness boost does not affect the nightly batch.** It lives in
  `cryo/governor/admission.py`; production maintenance selects tables through
  `cryo/maintenance/selection.py` (AutoComp) and bypasses the governor. Flipping it alone
  proves nothing — **blocked, do not flip.**
- **The event watcher is not deployed and polls.** `cryo events` (`EventsRunner`,
  `cryo/cli.py`) has no Helm template; `cryo/events/watcher.py` polls snapshot IDs. Adding a
  Deployment is gated on C3/S2 — **blocked, do not add.**
- **Do not re-enable the native engine** to recover reclaim numbers.

### 0.4 Correction to send with the next management update (text, not code)

Moved to `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`; it quotes unretained production figures.

### 0.5 Final report the worker must produce

For each of W1–W5: commit hash, files changed, tests added, the exact `make` results, and
anything stopped or skipped with the reason. List any fact in 0.2/0.3 that did not match the
code. Do not claim a task done without its "Done when" evidence.

**Findings from the 2026-09-28 execution:**

- **W1 precondition:** the grep now also lists `cryo/maintenance/coordinator.py`. W3 added that
  caller by design.
- **W3 manifest path:** no `retention_days` path into the manifest exists (it is a
  `DEFAULT_PARAMS` constant rendered into SQL). The flag travels as a keyword argument set at
  both lane-creation sites; the `ca98cf2` commit body says so. The review fix `00d1de8` moved the
  probe's path extraction inside its `try`, which the "never fail the statement" rule required.
- **W4:** a second agent committed it (`140b7ab`) before W1–W3 landed. Engines with no measured
  rows are omitted rather than stored as `None`. `d659ec0` merges a NULL engine and a literal
  `"unknown"` engine into one entry.
- **W5:** "keep sections 0–9" conflicted with "no §10.2 figures", because §0.2 W4, §0.4,
  §10.4 and §10.5 quoted them. §0.4 moved to the private file; the other lines were
  generalized, with the originals kept verbatim in the private file.
- **Open, not fixed (out of scope):** the `expire_snapshots` branch of
  `bytes_freed_from_procedure` ignores `deleted_statistics_files_count`, so an expire that only
  removed statistics files would record a measured `0`. Verify the column against Iceberg
  1.11.0 before changing it. `ready_for_enforcement` is set but not shown in the API or logs,
  and nothing reads `raw["bytes_freed_by_engine"]` yet.

## 1. Recommendation

**Make Cryo the easiest fully open Iceberg maintenance platform to evaluate, operate within a
budget, and verify economically.** Win first with teams running their own Trino/Spark and
Iceberg stack who want control of both the software and the evidence behind its decisions.

The proposed promise is:

> **Iceberg maintenance you control, with results you can verify.**

The product should answer four questions for every action: **Why this table? Why now? What
did it cost? What actually improved?** A successful run is only the beginning of that answer.

This is a stronger strategy than adding every competitor feature or advertising the number
of papers implemented. Amoro has substantial scheduling and streaming engineering. LakeOps
already markets query-aware optimization, simulations and cost outcomes. Cryo must demonstrate
a better outcome for a defined customer and workload, not claim to be the only intelligent
optimizer.

Three priorities, in order:

1. **Independent installation and safe operation.** Finish the OSS plan's public artifacts,
   Helm install, diagnostics, supported combinations, recovery and upgrade gates.
2. **One observable decision loop.** Connect workload evidence, maintenance budgets, execution
   and outcome measurement on the path that actually runs. Reuse existing work; do not enable
   dormant paths merely because their code exists.
3. **Reproducible economic proof.** Publish a fair harness, real limitations, and independent
   operator case studies. Promote numerical superiority only for the tested conditions.

The strategic advantage we seek is the combination of openness, understandable decisions,
safe operation and verifiable net value. No research reviewed establishes that Cryo already
outperforms either competitor.

The competitive-displacement campaign built on this recommendation is in `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`.

## 2. Research method and corrections to the earlier draft

Primary sources were reviewed on 2026-09-28: versioned Amoro documentation, current development
docs, Apache improvement proposals, GitHub release/issue metadata, LakeOps product/docs/pricing
pages and public repositories, and Apache Iceberg semantics. Neither competitor was deployed
or benchmarked during this research. A vendor's documented feature is evidence of its claim,
not independent verification of its performance.

Use these evidence labels in future comparisons:

| Label | Meaning |
|---|---|
| Released/documented | Identified released version and documentation; still requires an execution test for compatibility claims. |
| Development | Current source/docs or closed development issue; not assumed present in the latest stable release. |
| Vendor-stated | First-party description, benchmark or price; not independently reproduced. |
| Code-backed | Cryo source/contracts support the statement; not proof of production activation or value. |
| Reported internally | Inherited operational observation awaiting a retained query/export, scope and timestamp. |
| Measured comparison | Versioned protocol, raw results, cost boundary and correctness checks retained for both systems. |
| Proposed | Work or target in this strategy; not implemented or achieved by writing this document. |

Important corrections:

- **Helm and self-hosting are not unique to Cryo.** Amoro documents Kubernetes/Helm deployment;
  LakeOps advertises VPC/on-prem Enterprise options.
- **Query-aware layouts and branch experiments are not unique to Cryo.** LakeOps explicitly
  documents them. A sampled predictor in Cryo is not the same as a real rewrite/replay experiment.
- **Do not call Amoro a simple file-count scheduler.** Its released design trades read against
  write amplification and has resource/fairness policies; development work targets evaluation
  overhead and elastic capacity.
- **Do not claim competitors lack costs, audit or per-action impact.** LakeOps documents those
  surfaces. The opportunity is a more inspectable, reproducible accounting contract, which must
  be demonstrated.
- **Do not compare Cryo's catalog count to a marketing dashboard as a scale victory.** Amoro's
  AIP-3 discusses a deployment with over 40,000 tables. LakeOps' illustrated table/volume
  counters are not evidence of its product limits.
- **Do not promote the old draft's live numbers as newly verified here.** Preserve them in
  Appendix A with provenance requirements. In particular, snapshot file-size deltas do not
  prove physical deletion, and physical deletion does not by itself prove billed savings.

Sources for these corrections are linked in the corresponding sections below.

## 3. Competitor assessment

### 3.1 Apache Amoro: take the engineering and community seriously

GitHub's latest-release endpoint returned **v0.8.1-incubating**, published 2025-09-11.
The website explicitly identifies `latest` docs as development documentation. Benchmark the
stable tag first and label any development-build comparison separately.
[Release](https://github.com/apache/amoro/releases/tag/v0.8.1-incubating),
[documentation version boundary](https://amoro.apache.org/docs/latest/self-optimizing/).

| Dimension | Evidence and implication for Cryo |
|---|---|
| Product scope | AMS and optimizer plugins manage a broader lakehouse surface, including native and mixed formats and streaming use cases. Existing metadata services can be integrated; do not imply users always have to migrate catalogs. Cryo should target existing Iceberg operations rather than recreate Amoro's broader platform. [Architecture](https://amoro.apache.org/docs/0.8.1/) |
| Maintenance design | Released documentation describes minor/major/full optimization, delete conversion, optimizer groups and Quota/Balanced scheduling. Cryo needs good delete handling and understandable capacity/fairness controls before claiming superiority here. [Stable self-optimizing design](https://amoro.apache.org/docs/0.8.1/self-optimizing/) |
| Installation | Kubernetes documentation provides Helm installation, configurable images/database and service accounts. Cryo can compete on a tested time-to-first-result and smaller measured operating burden, not on the mere existence of a chart. [Kubernetes deployment](https://amoro.apache.org/docs/latest/deployment-on-kubernetes/) |
| Evaluation overhead | AIP-3 describes redundant metadata/file evaluation in a 40,000+ table deployment and proposes metric/commit-driven evaluation. Its 9-hour log analysis of 40,804 tables reports most batch tables unchanged within a day yet refreshed and scanned every minute, 42.5% of scan time spent on effective scans, and 11% of scans on empty tables; the metric-driven filter raised effective scans from 18% to over 80% in preliminary production use. Recheck: umbrella issue #3774 was closed on July 13, 2026 **as not planned by the stale bot**, not completed. The metric-driven sub-task (#3775) is completed (release inclusion unverified); the commit-triggered refresh sub-task (#4018) is not planned; AIP-3 keeps periodic refresh as the default and fallback. Treat this as a moving competitor, not a permanent polling weakness. [AIP-3](https://cwiki.apache.org/confluence/spaces/AMORO/pages/378473120/AIP-3+Event-Triggered+Optimization+of+Iceberg+Tables+in+Amoro), [issue #3774](https://github.com/apache/amoro/issues/3774), [#3775](https://github.com/apache/amoro/issues/3775), [#4018](https://github.com/apache/amoro/issues/4018) |
| Elastic capacity | AIP-5 specifies demand-based optimizer capacity, limits and draining; its issue was closed September 3, 2026. Recheck: closed as completed with all five phases merged to master, but no published release contains it as of 2026-09-28 (latest is v0.8.1). It is opt-in and disabled by default. Confirm shipped version and scope before asserting that Amoro always pays for idle workers. [AIP-5](https://cwiki.apache.org/confluence/spaces/AMORO/pages/421957106/AIP-5+Dynamic+Resource+Allocation+for+Optimizer), [issue #4191](https://github.com/apache/amoro/issues/4191) |
| Public performance evidence | The official report uses Amoro 0.4, Iceberg 0.13 and Trino 380, with about 10 GB initial data and 12 CHbenchmark tables. It studies continuous-ingestion query behavior; it is not a current all-in cost comparison against Cryo. Acknowledge its purpose instead of dismissing it for age or size. [Benchmark](https://amoro.apache.org/benchmark-report/) |
| Trust | Open source and an Apache community are real adoption advantages. Cryo needs named maintainers, reliable releases and outside contributors to compete on trust. Incubation status is not a claim of poor code quality. [Project](https://amoro.apache.org/) |
| Positioning in its own docs | Stable 0.8.1 docs: teams with non-updatable tables (logs, sensor data) who already use Iceberg's Spark rewrite action can turn self-optimizing off; enabling it is advised for primary-key tables with CDC or streaming updates. The optimizer is described as a resident process managed by AMS. Defaults include `clean-orphan-file.enabled=false` and `snapshot.keep.duration=12h`. This locates Amoro's centre of gravity; it is not evidence that Amoro handles batch tables poorly. [Self-optimizing 0.8.1](https://amoro.apache.org/docs/0.8.1/self-optimizing/), [Configurations 0.8.1](https://amoro.apache.org/docs/0.8.1/configurations/) |
| AI surface (development) | AIP-6 MCP server on master: eleven read-only tools, no generic action executor, AMS 0.8+ required. Unreleased. [MCP server](https://github.com/apache/amoro/tree/master/amoro-mcp-server) |
| Commercial ecosystem | A GitHub organization, GlacioData, describes itself as "the commercial version of Amoro" and maintains a fork (last updated August 25). No product was reviewed. Expect an enterprise sales motion around Amoro, not only a community one. [GlacioData](https://github.com/GlacioData) |

**Where to win:** an independently demonstrated, budget-conscious operating experience for
existing Iceberg fleets, with workload-linked decisions and complete evidence. **Where not to
force a win:** streaming/CDC infrastructure or multi-format breadth when those dominate a
customer's requirements.

“No Flink required” may describe a Cryo profile, but must not become “Amoro requires Flink”:
Amoro has other optimizer deployment options.

### 3.2 LakeOps: compete with its actual offer, including its open components

| Dimension | Evidence and implication for Cryo |
|---|---|
| Buying experience | LakeOps documents catalog connection, table exploration and progressive optimization onboarding. Cryo needs a comparably understandable first-use path with no internal setup knowledge. Its ten-minute claim is a vendor target, not our independently measured result. [Quick start](https://lakeops.dev/docs/getting-started) |
| Workload optimization | Docs describe a Rust/DataFusion executor, binpack/sort, query-pattern inputs, scoped predicates and per-action records. Treat these as competitive capabilities, not an empty marketing story. A faster engine alone is not a sufficient Cryo strategy. [Compaction docs](https://lakeops.dev/docs/compaction) |
| Experiments | Docs describe branch-based candidate layout experiments and result comparison. Cryo must distinguish predicted effects, actual candidate measurements and production outcomes, and compete on experiment reproducibility and overhead. [Simulations](https://lakeops.dev/docs/simulations) |
| Policies | Scheduled and adaptive policies, scoped inheritance, overrides and auditing are documented. The product is not exclusively event-triggered; our comparison must not set “events” against a caricature of “cron.” [Policies](https://lakeops.dev/docs/policies) |
| Broader scope | Query routing is a separate part of its proposition, with cost/latency controls. Keep routing gains separate from maintenance gains in a benchmark. Cryo does not need a serving proxy to compete in maintenance. [Query routing](https://lakeops.dev/solutions/query-routing) |
| Commercial model | Posted Team price is $5/TB optimized; Free and Team buttons say “Coming soon.” Enterprise pricing is custom and advertises SaaS/VPC/on-prem. Availability, billing units and compute inclusion need confirmation for a purchase comparison. Cryo's opportunity is complete open software without a mandatory per-TB software fee, while acknowledging self-hosting costs. [Pricing](https://lakeops.dev/pricing) |
| Open-source boundary | Public repositories include Apache-2.0 compaction and QueryFlux components. A complete LakeOps control-plane source release was not found in the reviewed org; do not describe all LakeOps technology as closed. Comparing its standalone executor is not benchmarking the commercial service. Recheck detail: the compaction repository is a fork of `nimtable/iceberg-compaction` (11 commits behind upstream, 2 stars, no releases). Both pin the `iceberg` crates to a RisingWave fork of iceberg-rust, because upstream `apache/iceberg-rust` still lacks the write actions compaction needs (EPIC #2269 lists RewriteFiles as missing; OverwriteAction #2185 is open with correctness issues in review). QueryFlux is Apache-2.0, 141 stars, 17 contributors, v0.3.0 last month. Nothing here describes LakeOps' commercial engine. [Repositories](https://github.com/lakeops-org), [compaction](https://github.com/lakeops-org/iceberg-compaction), [QueryFlux](https://github.com/lakeops-org/queryflux), [iceberg-rust #2269](https://github.com/apache/iceberg-rust/issues/2269) |
| Numeric claims | The compaction page shows 221 s versus 1,612 s for one example while also using a 95%-faster headline. Arithmetic for that pair is about **86.3% less elapsed time / 7.29× speedup**. The page's broader savings and illustrated fleet counters are vendor material, not an audited cross-product result. Request the harness and accounting boundary rather than turning an inconsistency into Cryo's sales pitch. [Benchmark claims](https://lakeops.dev/solutions/compaction) |
| Content strategy | A large comparison-content program. Its "9 compaction tools compared" post (May 2026) covers LakeOps plus eight vendor built-ins and no open-source control plane, Amoro included. The "open-source Iceberg maintenance" comparison slot is unowned. [Comparison post](https://lakeops.dev/blog/iceberg-compaction-tools-compared) |
| Website terms | Terms of Use (updated October 7, 2025) prohibit robots/spiders and reuse of site content without consent. This review's retrievals were tool-assisted; future monitoring of lakeops.dev must be manual, and comparison pages link and quote minimally rather than copying screenshots or text. [Terms](https://lakeops.dev/legal/terms-of-use) |

**Where to win:** source and deployment control, no mandatory hosted dependency, exportable
decisions/results, transparent total cost, and an installation that does not need a sales call.
**Where LakeOps may be preferable:** teams who value a managed service, contracted support and
its broader routing/governance offering more than operating their own platform.

Do not promise lower total cost merely because Cryo has no license fee. Do not use “only
metadata” to describe the entire execution path: a compaction engine reads and rewrites data;
control-plane access and data-plane access must be described separately for every product.

### 3.3 What is actually differentiating?

| Candidate selling point | Competitive status | Cryo decision |
|---|---|---|
| Compaction, expiry, orphans, manifests | Baseline category capability | Make reliable; do not lead with a feature-count victory. |
| Helm / self-hosting | Existing alternatives offer deployment choices | Measure setup, security, recovery and upgrade experience. |
| Open source | Shared with Amoro; LakeOps has open components | Deliver complete open core/UI/chart/policies and a sustainable community. |
| Query-aware layouts | LakeOps documents this; Amoro documents sorting but reviewed docs do not establish automatic query-log-selected layouts | Prove query/SLO improvement after rewrite cost; do not label unsupported unknowns “No.” |
| Budget and explainability | Strong strategic opportunity, not established exclusivity | Make every decision inspectable and enforce policy on every execution path. |
| Net economic evidence | Competitors discuss cost; independently reproducible coverage differs and needs evaluation | Publish the accounting, uncertainty, failures and counterfactual, not just a dashboard dollar. |
| Metadata-based health and quality | Useful adjacent value | Offer a low-friction read-only entry point; document incomplete metadata and rule limits. |
| Native execution | Competing runtimes exist | Optimize only after correctness and measured end-to-end cost justify investment. |

## 4. Choose a customer before choosing a comparison

**Primary design partner:** a data-platform team with an existing supported Iceberg catalog,
Trino and/or Spark, a heterogeneous batch-heavy fleet, and recurring maintenance/cost pain.
It can operate Kubernetes and wants source visibility. Table count is a workload descriptor,
not a qualification threshold or a savings guarantee.

| Situation | Cryo offer | Evidence needed |
|---|---|---|
| Scripts and cron have become hard to operate | Install, observe read-only, import explicit maintenance policies, consolidate diagnostics/audit | Less operator effort and successful recovery from documented failures. |
| Maintenance itself costs too much | Rank useful work within compute/request budgets; skip or defer low-value work with reasons | Lower total maintenance expense while meeting agreed health and query SLOs. |
| Hot tables scan too much data | Workload-based candidate layouts, held-out replay and bounded canary | Net benefit after rewrite/experiment costs and no unacceptable query regression. |
| SaaS control plane is unacceptable | Complete open deployment; optional external integrations; verifiable egress behavior | Fresh install with core egress blocked and a tested identity/security profile. |
| Customer already runs Amoro | Read-only assessment and optional adoption on an isolated table cohort | No competing maintenance owner for the same tables; comparison on that customer's priorities. |

Deprioritize customers primarily seeking a streaming ingestion framework, multi-format
management, a fully managed SLA, or a multi-engine query router. Those are different projects,
not gaps to fill before Cryo's first successful independent installations.

Run five discovery interviews before expanding scope. Ask for the last maintenance incident,
current operator effort, actual optimizer spend, required catalog/auth combinations, purchase
constraints, and the evidence needed to trust automated writes. Record lost evaluations as
carefully as wins. Do not assume savings potential from fleet size alone.

## 5. Product work that earns stronger claims

### C1 — Make the open product independently usable

**Priority P0; owner platform + frontend; delivery in the OSS workstream.**

Finish public chart/images, secure bootstrap, read-only onboarding, connection diagnostics,
explicit support versions, secret/identity handling, and upgrade/restore rehearsal. Complete
two independent non-PW installs. Keep source access, standard policy controls, core observability
and ordinary contribution paths fully open; optional services must not be needed to operate.

**Gate:** install → connect → scoped scan → preview → explicitly enabled maintenance → verified
result → recover → upgrade, using public docs and artifacts. Measure both operator active time
and wall-clock time, including prerequisites separately. Proposed first-scan usability target:
30 minutes after prerequisites are met; publish actual results and fleet size.

This is the necessary adoption foundation, not a claim of superiority over another chart.

### C2 — Make the outcome record trustworthy

**Priority P0; owner backend + engine maintainers.**

Use [lane results](../cryo/maintenance/results.py), audit, and
[impact records](../cryo/reporting/impact_history.py) as the foundation for a versioned,
exportable record for every attempted action:

- Table/catalog identity, plan/policy version, reason, inputs' timestamps, engine/version,
  scope, expected cost, budget reservation, and decision to execute/skip/defer.
- Attempt and committed snapshot identities where applicable; retries, duration, resource use,
  output files, correctness checks, and explicit no-op/failure/unknown states.
- Separate **logical file change**, **objects physically deleted**, **billable storage change**,
  and **attributed cost estimate**. Snapshot `removed-files-size - added-files-size` is not a
  physical deletion receipt. Retained references/versioned objects can remain billable.
- Measurement method, coverage denominator, units, pricing source/date, uncertainty and links
  to supporting evidence. Deduplicate retries and downstream cleanup to avoid double counting.

Reconcile effects off the request path using bounded evidence collection. Protect branches,
tags and in-flight writes; never add a full live file scan to an API/planning hot path just to
draw a number. Iceberg explicitly separates snapshot maintenance and safe orphan removal.
[Maintenance semantics](https://iceberg.apache.org/docs/latest/maintenance/).

**Gate:** every attempted action has a status/evidence record; at least 95% of eligible
successful pilot actions have the defined effect measurements for seven days. Publish the
remaining coverage gap. “95% measured” never unlocks “every byte measured.” Non-applicable
fields are distinct from missing fields, and no-action days are not 100% coverage successes.

### C3 — Use one budget and decision contract across execution paths

**Priority P0; owner backend/planner; depends on C2 evidence.**

The current [batch selector](../cryo/maintenance/selection.py) implements AutoComp-style
budgeted ranking. [ROIGate](../cryo/governor/roi_gate.py) is a different path; its presence does
not prove that batch lanes use it. Reconcile the decision semantics on the active path before
adding a fast path. Share pure policy logic and audit contracts, preserving engine separation;
a new orchestration rewrite is not required.

Provide compute ceilings, request-cost bounds where measured, per-source concurrency,
cooldowns, retry limits, protected retention, and an explicit benefit horizon. Distinguish
GB-hours/resource reservations from dollar prices and realized node costs. An economic estimate
with weak telemetry must say “unknown”; it must not disable essential safety/retention work.

Run shadow evaluation first. The existing gate can leave shadow mode after its sample condition;
audit that behavior and require an explicit controlled activation policy before a pilot.
Review telemetry sources: table activity/patterns and sampled `QueryLog.bytes_read` are not
interchangeable. No query observation is not proof of no queries.

**Gate:** review at least 200 stratified decisions; all supported dispatch paths respect the
same hard constraints; enforcement can be paused; seven pilot days meet agreed health/SLOs.
Calibrate predicted cost/benefit on held-out outcomes. Count skipped-job savings as a
counterfactual estimate unless a controlled comparison measures the reduction.

### C4 — Deliver measured workload-aware layout improvement

**Priority P1; owner optimizer + engine; depends on C2/C3 and write-conformance gates.**

Keep the implementation boundaries from the [paper audit](PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md):
AutoComp ranking is implemented with a labeled Cryo query-heat extension; WAIR is
recommend-only; PTO uses a heuristic and is shadow-only. Paper citations are design provenance,
not outcome evidence.

The [layout simulator](../cryo/optimizer/layout_simulation.py) predicts pruning from sampled
rows and explicitly warns about bias. Use predictions to shortlist candidates, then:

1. Establish a stable baseline and held-out query mix, with parameter/selectivity diversity.
2. Run a bounded physical candidate experiment on an isolated copy, or on a branch only where
   the catalog/engine/operation combination has a tested isolation contract.
3. Compare correctness, bytes read, query p50/p95, engine load, rewrite/experiment cost and
   expected payback horizon; retain the unchanged-layout control.
4. Promote a small cohort explicitly, revalidate the source state, and monitor drift/regression.
   Recommend no change when evidence is weak or net benefit is negative.

A branch is not a complete sandbox: Iceberg table schema is shared across branches. Promotion
must validate ancestry/concurrent writes; a blind fast-forward can fail or be invalid. Use
isolated table copies for schema/spec experiments until their semantics are certified, budget
temporary files, and clean them safely. Replaying queries means approved read-only queries with
timeouts, not executing arbitrary captured SQL.
[Iceberg branches](https://iceberg.apache.org/docs/latest/branching/).

**Gate:** start with five controlled tables, then at least twenty representative tables with a
defined observation window and sufficient query samples. Predeclare an acceptable regression
margin (initial proposal: p95 no more than 5% worse, with uncertainty). Report observed
regressions and future-drift risk. Never promise zero future regressions.

### C5 — Add responsiveness and fairness only where they pay

**Priority P1; owner orchestration; depends on C3 and pilot demand.**

[EventsRunner](../cryo/cli.py) and the [commit watcher](../cryo/events/watcher.py) exist, but the
watcher polls snapshot IDs: it is not a native commit-notification subscription. Deployment,
recovery, signal completeness and active execution integration need verification. Treat it as
an evaluation hint followed by reconciliation, not a guaranteed delivery log.

For 20–50 explicitly selected hot tables, trial bounded, debounced evaluation with checkpointing,
deduplication, restart/loss recovery, a periodic reconciliation fallback and a reserved budget.
Use one maintenance owner and the same locks/admission policy as scheduled work. Measure the
monitoring requests/CPU as part of cost. Do not restore expensive always-on activity across
the whole fleet simply to match a competitor headline.

The [staleness boost](../cryo/governor/admission.py) is bounded. Enabling it does **not** prove
starvation freedom. Measure eligible-work waiting time, define service classes and capacity
shares if needed, and surface overload when deadlines cannot be met. Preserve per-table locking;
group/tenant fairness may matter more than a per-table slot setting when only one job can run
per table.

**Gate:** a selected pilot cohort meets a specified threshold-to-commit SLO (initial target
p95 under 30 minutes), without exceeding its budget or degrading scheduled coverage. Workload
arrival rates and capacity must be stated. No blanket “no table starves” claim.

### C6 — Close delete-heavy maintenance gaps; optimize the engine second

**Priority P1 for supported delete correctness; P2 for new runtime acceleration.**

Exercise existing minor/major delete policy on real supported shapes, using the correct engine
capabilities and canonical operation order. Measure merge-on-read query overhead, delete-file
growth and write amplification. A request flag is not proof of equality-delete conversion or
position-delete handling on every engine/version; V3 boundaries remain explicit.

Revisit Go-native execution only after committed-state verification, row/delete semantics,
restart/conflict tests and safe fallback behavior pass. Measure total cost per useful rewrite,
not just executor throughput. Also benchmark the tuned Spark lane path; eliminating startup
and repeated work may be more valuable than changing languages.

**Gate:** delete-heavy integration/recovery suite passes; pilot query and maintenance costs are
reported. Native promotion needs a measured advantage for named supported table shapes.
“Rust” or “Go” is an implementation choice, not a performance result.

## 6. Quantify “better”: a fair comparison protocol

**Primary question:** for the same ingestion/query workload and agreed correctness/retention/
latency constraints, which system produces the lowest total cost and operator burden?

### Arms and isolation

Compare (A) a tuned scheduled Iceberg/Spark maintenance baseline, (B) Amoro's pinned stable
release, and (C) Cryo's pinned candidate. Add (D) LakeOps only when the actual service is
available for evaluation under an agreed reproducible protocol. Its open compaction runtime can
be a separate executor experiment, explicitly labeled; it cannot stand in for the entire service.

Use disposable physically isolated copies, independent prefixes/catalog entries and identical
initial snapshots/data. A “cloned catalog” can still reference production files: it is not safe
isolation for expiry/orphan tests. Never run destructive comparative tests on shared warehouse
objects or with two optimizers owning the same tables.

### Workload matrix

| Cohort | What it tests |
|---|---|
| Small append-heavy files | File overhead versus unnecessary rewrite cost. |
| Mixed hot/cold fleet and skewed table sizes | Budget selection, fairness, heavy-tail handling. |
| Delete-heavy supported V2 tables | Logical correctness, minor/major tradeoff, query overhead. |
| Predicate-selective queries and later workload drift | Layout benefit, adaptation, expensive wrong decisions. |
| Mostly idle fleet | Control-plane CPU, memory, requests and minimum worker expense. |
| Failures, concurrent writers, protected snapshots | Recovery cost, duplicate work, correctness and retention. |
| Large metadata fleet | Start at 1,000 then 10,000+ synthetic table identities; measure scan/control-plane bounds separately from real data rewrite scale. |

Stage the expenditure: small correctness fixture → ~200 GB executor experiment → mixed
multi-table run → 14-day operational trial → at least 30 days of eligible billing evidence.
These are proposed experiment sizes, not a universal statistical sample-size rule. Define a
cost cap before provisioned experiments; this research authorizes none.

Match hardware class, region, storage, compression, target file sizes, table features,
retention and useful output. Give each product a documented tuning budget and retain configs.
Report default setup and tuned results separately. Separate engine performance from scheduling,
layout and serving-engine routing; do not credit a faster query engine to maintenance alone.

### Metrics and accounting

| Metric | Required reporting |
|---|---|
| Correctness | Row multiset/schema/delete semantics, retained-reference readability; include all failures. |
| Query experience | Per-query-class p50/p95, bytes read, concurrency and timeout/error counts. |
| Maintenance | Bytes actually rewritten, commits/no-ops/retries, amplification, duration, CPU/memory-hours. |
| Resources | Control plane, scanner, metadata calls, idle capacity, ephemeral job buckets, temporary storage. |
| Economics | Measured compute/storage/request expense; software/service fees separately; currency, region, dates and uncertain allocation. |
| Operations | Active operator minutes, install/upgrade/recovery success, days of continued use. |
| Coverage | Scheduled/eligible/completed/failed/deferred work and measured-effect coverage. |

For an equivalent workload and time horizon:

```text
total cost = query compute + maintenance compute + control-plane/scanner cost
             + storage (including retained/temporary/versioned objects)
             + object/catalog requests + retries + service/license fees

relative saving = (comparator total cost - Cryo total cost) / comparator total cost
```

Report operator time separately, or show sensitivity to an explicit labor rate. List missing
costs instead of treating them as zero. A $5/TB vendor headline is not a complete TCO boundary:
confirm chargeable bytes, repeated passes, compute inclusion and contract terms first.

Randomize repeated query/engine runs and disclose cache states/warmup. Use matched or randomized
cohorts, track ingestion/query changes, report confidence intervals and workload strata, and
account for correlated table/day observations. Choose repetitions/power from a pilot. A fixed
“200 tables” or seven days does not automatically establish statistical significance.

Snapshot/file changes are operational evidence; bill-level attribution needs sufficient billing
lag/retention observation and a credible comparison. Lower CPU on an unchanged fixed-price
cluster is capacity relief, not automatically cash saved. A before/after monthly bill alone
does not identify causality.

### Targets, not current claims

- Seek **at least 20% lower measured total cost** versus a tuned scheduled baseline for the
  initial batch-heavy target segment, while meeting correctness and query SLOs.
- Seek a materially faster independent setup/recovery experience versus Amoro; measure it
  rather than setting the result in advance.
- Against LakeOps, seek a demonstrable TCO/control advantage for self-hosting customers.
  Until its service is tested, publish a feature/ownership comparison with no performance rank.
- Publish losses and neutral outcomes. If the targeted value fails to appear, narrow the
  supported use case or stop the feature instead of changing the headline.

A defensible eventual statement is: “On workload W, Cryo version C used X% less total cost
than Amoro version A over N runs/days, with query p95 within Y%; protocol and raw results here.”
It is not “Cryo is X% better” without the scope and denominator.

## 7. Ninety-day delivery and claim gates

Indicative sequence for two backend/engine engineers, one platform engineer and part-time
frontend/research/maintainer support. Assign named owners before work; re-estimate after the
first baseline. This is a proposal, not a delivery guarantee or permission to deploy.

| Window | Work | Exit evidence / claim unlocked |
|---|---|---|
| Days 0–14 | Publication/installation baseline; C2 evidence schema and coverage; audit actual execution paths; five user interviews; benchmark protocol | Honest current-state page, reproducible baseline, prioritized supported profile. No comparative superiority claim. |
| Days 15–35 | OSS install/release work; C2 implementation; C3 shadow decisions and bounded enforcement; baseline harness | Two independent installs and verified scoped maintenance; budget decisions visible on the active path. Open-product promise only after publication gates pass. |
| Days 36–60 | Delete-heavy conformance; C4 canary/held-out replay; start matched Amoro comparison; trial C5 only if justified | Named workload/layout outcomes with cost and regression data. Responsive maintenance claim limited to its measured cohort. |
| Days 61–90 | Extend operational/billing observation; independent review; beta upgrade/restore exercises; case-study and comparison pages | Measured numerical comparisons where evidence supports them; explicit gaps elsewhere. Public beta requires OSS gates too. |

Reserve capacity for incidents and contributor support. If staff is smaller, cut hot-table
acceleration and native work first. Do not delay installability, safety or evidence to complete
PTO training, a query router, or a broad new cloud matrix.

Immediate work packages, each split into focused implementation PRs:

| ID | Owner role | Concrete deliverable | Depends on |
|---|---|---|---|
| CS-01 | Backend/reliability | Retained baseline query/export, measurement definitions, explicit data-coverage report | None |
| CS-02 | Platform | Independent Helm installation/recovery and complete artifact distribution | OSS-0/2/3 |
| CS-03 | Backend/engine | Versioned per-action records, commit/effect distinctions, reconciliation tests | CS-01 |
| CS-04 | Planner/backend | Shared budget/ROI semantics on active batch and any enabled ad-hoc/event paths | CS-03 |
| CS-05 | Engine/test | Correctness/delete/retention/concurrency fixture and tuned Spark baseline | CS-01 |
| CS-06 | Optimizer | Held-out layout evaluation and bounded physical canary | CS-03/04/05 |
| CS-07 | Orchestration | Optional hot-table evaluation and overload/fairness reporting | CS-04/05; pilot demand |
| CS-08 | Research/release | Versioned comparative harness, raw results, immutable artifact links | CS-02/05 |
| CS-09 | Maintainer/docs | Public proof page, verified comparison pages, external operator case studies | CS-08 + relevant claim gates |
| CS-10 | Connectors/platform | Policy-mapping and migration kit; safe maintenance-owner handoff and return | CS-02/04/05; section 11.4 |
| CS-11 | Project lead/docs | Qualified evaluations, win/loss reviews and retained-adoption campaign | Product/claim gates; section 11.5 |
| CS-12 | Engine | Upstream iceberg-rust write-action reviews, tests and fixes | None; section 11.8 |
| CS-13 | Maintainers/docs + backend | Open-source comparison page, editable TCO worksheet, release train | C1, C2; section 11.9 |
| CS-14 | Research | Manual competitor-monitor runbook and watchlist; feeds the CS-11 log | None; section 11.11 |

Existing paper implementations are inputs to these packages. This plan does not authorize
turning disabled flags on in production or replacing the current execution architecture.

## 8. Adoption and promotion plan

### An evaluation path that earns trust

1. **Discover:** publish the project, honest compatibility table, two-minute walkthrough and
   a clear “install and inspect your lake” call to action.
2. **Observe:** first value is a scoped read-only health/maintenance report, with useful findings
   even without query logs or billing access.
3. **Understand:** show a proposed operation, reason, estimated expense and evidence gaps.
4. **Try:** maintain one approved disposable or low-risk table; verify the result visibly.
5. **Operate:** expand a capped cohort, then show a week of outcome/expense/recovery evidence.
6. **Contribute:** provide reproducible diagnostics, real small issues, versioned connectors,
   public maintainer ownership and prompt triage.

Track where evaluators stop. A half-hour spent resolving catalog credentials is a product
problem to fix, not a lack of marketing.

### Promotion assets and channels

| Asset | Message | Release condition |
|---|---|---|
| README / landing page | Complete open Iceberg maintenance for teams operating their own stack | Working public install, support matrix, secure defaults. |
| “Cryo vs Amoro” page | A workload-specific choice: operating experience, decision evidence, tested cost | Fair sourced matrix first; head-to-head numbers only after CS-08. |
| “Cryo vs LakeOps” page | Source/deployment ownership and total-cost tradeoffs | Acknowledge its open components and Enterprise deployment; do not fabricate a service benchmark. |
| Proof page / downloadable report | Plan → execution → observed effect → cost, including unknowns | C2/C3 coverage and appropriate case-study disclosure. |
| Technical articles | What maintenance costs; why fewer files is not a lower bill; avoiding low-value rewrites | Reproducible examples, source links and runnable artifacts. |
| Engineering talks/demos | A real install, incident recovery and a measured workload improvement | Public fixture and honest failure/unsupported states. |
| Ecosystem recipes | Tested REST/Polaris/Glue/Trino/Spark combinations | Exact supported tuple; community/partner review where available. |
| External case studies | Why the operator adopted it and what changed | Operator consent, retained evidence, complete cost boundary. |

Use maintainer-authored launch posts and technical communities where relevant contributions
are welcome, with clear affiliation. Contribute useful interoperability fixes upstream. Seek
two to three design partners rather than buying attention before the installation works.
No outreach or publishing is performed by this planning task.

Do not make enterprise safety features or basic audit a prerequisite paid upgrade if the goal
is complete open adoption. If a business model is later needed, optional managed hosting,
support and integration services can be evaluated separately without making the core depend
on them.

### Messaging we can earn

**After the public-release gate, code-backed wording:**

> Cryo is an Apache-2.0, self-hosted Iceberg maintenance and health platform for teams running
> their own data stack. Connect a supported catalog and engine, inspect the plan, and control
> the scope and budget of maintenance.

**After C2/C3:** “See why each job ran, what it cost, what changed, and what remains unmeasured.”

**After measured comparisons:** a scoped X% statement with version, workload, interval,
cost boundary and raw results. Use the weaker correct statement when evidence is partial.

See §10.5.

### Adoption scorecard

Initial pilot goals, not vanity metrics: two independent successful installs; first scoped scan
within 30 minutes of satisfied prerequisites; a verified maintenance outcome within the agreed
workload window; an externally executed recovery/upgrade drill; and two operators still using
Cryo after four weeks. Track unsuccessful installs and drop-off as denominators.

Report time to first useful finding, operator active time, error/recovery rate, cost per useful
action, effect-measurement coverage, query regressions, triage responsiveness and independent
contributions. GitHub stars and raw job counts do not substitute for retained users or net value.
Collect pilot feedback manually first; no mandatory usage telemetry.

## 9. What to defer, and when to change course

| Temptation / risk | Decision |
|---|---|
| Copy every LakeOps feature | Defer serving-query routing and AI wrappers; deliver the maintenance outcome first. |
| Rebuild Amoro's streaming ecosystem | Remain Iceberg/batch-focused; revisit integration only for demonstrated customer demand. |
| Treat dark code as ready | Require execution-path, failure, budget and value evidence before enabling it. |
| Use free software as a lower-cost proof | Include compute, idle footprint, upgrades and operator time. |
| Optimize for a single favorable benchmark | Publish workload strata and losses; repeat with independent operators. |
| Add native-engine work before safety | Keep it conditional; first improve active execution and truthful outcomes. |
| Confuse fairness with priority boost | State feasible capacity/SLO assumptions and expose overload. |
| Competitors add the same cost feature | Compete on implementation quality, calibration, openness and operator trust, not an exclusive feature claim. |

Monthly, recheck competitor stable releases, development proposals, pricing/availability and
public documentation. A closed upstream issue is a reason to investigate, not proof of a
shipped feature. Refresh comparison pages with a date and invite factual corrections.

**Continue investing** when independent users finish installation, trust the outcome record,
and retain Cryo because it improves their workload or operating effort. **Narrow the offer**
when the measurable value is confined to particular shapes. **Stop a feature** when its
ongoing complexity and expense exceed the demonstrated benefit.

## 10. Getting ahead, and what we can sell

A contributor added this section after reporting a production read on 2026-09-28. Its useful
S1–S4 engineering proposals are retained here; the subsequent review did not independently
repeat those production queries. This section does not establish comparative superiority.

### 10.1 A focused message for a Spark-isolated maintenance profile

Lead with the buyer and workload Cryo can win. A blanket "better than Amoro and LakeOps"
claim is not established by the current evidence:

> **Keep Iceberg maintenance off your Trino query cluster with Cryo's Spark execution
> profile. Control the software, the budget and the evidence behind each action.**

Use this message after the profile's installation and routing tests pass. It is a customer-fit
hypothesis, not proof of a better engine or exclusive capability. Amoro supports native Iceberg
and non-Flink optimizers; LakeOps documents separate execution and private deployment options.
Neither engine superiority nor inferiority has been measured here. Cryo's supported Trino
execution remains available for customers who explicitly choose it; Spark isolation is a
profile, not a new global ban on Trino maintenance.

Amoro's self-optimizer is ahead because it detects file changes, compacts small files
often and large files rarely, and feeds the result into the next schedule. LakeOps is
ahead because it claims that loop plus query-chosen layout, on an engine that is not
Spark. Cryo already has scan, trigger and planning components. Integrating them through the
active, budgeted execution path is a promising next step. The current batch selector does
not require a preset pin; diagnose why a particular table is excluded before attributing the
gap to legacy preset resolution or rebuilding an execution path.

### 10.2 Contributor-reported production observations

Moved to `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`. Not cleared for public use.

### 10.3 Four engineering moves to test against the competitors

Do these in order. Each one unlocks one sentence. None of them is a new engine.

**S1. Normalize Spark outcome evidence. Owner: engine. Depends on CS-01 metric definitions;
may proceed with the C2 baseline work.**

Parse the metrics dict already written by `cryo/engines/spark/procedure_metrics.py` and
`cryo/maintenance/coordinator.py` into the audit row: files in, files out, files deleted,
and bytes only when the procedure actually returned a size. `rewritten_bytes_count` is
bytes rewritten, not bytes freed. Do not store it as `actual_bytes_freed`.

**Source finding from this review:** `bytes_freed_from_procedure` currently returns
`rewritten_bytes_count` for `optimize`. Include correction and caller/history-impact review
in S1 before using that path as savings evidence. A rewrite-size difference still does not
prove objects were physically deleted. This planning edit does not change runtime code.

Gate: seven days where every attempted supported Spark action has an explicit execution
status and evidence coverage. Preserve the existing measurement vocabulary; define any
additional per-field state in the data contract. Missing bytes remain unmeasured even when
file counts are measured. Failure and no-action days are explicit; absence is never zero.

Sell line this unlocks: "Every maintenance action records what changed. If the engine
did not return a size, we say so. We do not print zero."

**S2. Let a critical file signal enqueue a job with no pin. Owner: scheduler. Depends on S1.**

First trace signal evaluation to actual dispatch. Legacy preset resolution and today's batch
selection are different: `cryo/maintenance/selection.py::load_candidates` filters active sources,
readable/fresh statistics, source scope, retry backoff and cooldown; it does not require a
preset assignment. Record the actual exclusion reason before changing that contract.

Within an explicitly enabled source/namespace cohort, a fresh critical small-file signal may
request one bounded evaluation. Reuse the active coordinator or a verified shared admission
contract from C3; preserve locks, chunk caps, cooldown, deduplication and the total budget.
Do not revive a second orchestration stack or let a signal authorize previously unapproved
scope. Health grade alone is not an optimize trigger: snapshot debt can lower a grade too.

Gate: an eligible, opted-in, unpinned table with a fresh critical file signal gets a bounded,
audited evaluation and valid work; healthy or excluded tables do not. For the Spark-isolated
pilot, no maintenance writes go to the designated Trino query cluster. Verify the pilot's
routing directly rather than using an unretained lifetime count as the gate.

Sell line this unlocks: "Cryo compacts a table because its files crossed a threshold, not
because someone remembered to pin it. The query cluster is still not the maintenance
engine."

This demonstrates the selected Cryo profile. It does not establish exclusivity or that Amoro
cannot preserve a Trino query cluster; a comparative advantage still needs section 6 evidence.

**S3. Split the cadence. Owner: planner. Depends on S2.**

Amoro's minor pass merges fragments under 16 MB often. The major pass is rare. We
already have `small_files_count`, `delete_file_pct`, and `commits_per_day` on the scan.
Use them:

- Between nightly runs, a bounded `optimize`-only pilot on compatible table shapes whose fresh
  file signal crosses its threshold. If a table needs prerequisite delete operations, route it
  through the supported full plan rather than skipping those prerequisites.
- The nightly run keeps `CORRECT_ORDER` in `cryo/planner/guardrails.py`: compact first,
  then manifests, then expire, then orphan, so one run can reclaim what its own
  compaction freed. LakeOps documents the opposite order. Ours is a design choice for
  batch reclaim, not a measured win. Do not advertise it as cheaper until section 6
  says so. Recheck: same-run reclaim only happens when the pre-compaction snapshot is
  already older than `retention_days` (cold tables). For tables written inside the
  window, the replaced files wait for a later run under either order.

Do not turn the native engine back on to get this. It reported success and committed
nothing. Spark is the path until committed-state verification exists.

Gate: triggered runs are optimize-only, finish inside the governor budget, and the
nightly four-op run still happens for snapshot debt. Write amplification is reported,
not assumed away.

Sell line after the measured cadence gate: "Selected hot tables receive bounded maintenance
between scheduled fleet runs, with write amplification and expense reported." Cadence alone
does not prove that bytes were never rewritten twice.

**S4. Turn fairness on, then cap a table's share. Owner: governor. Depends on S2.**

`staleness_boost_days` in `cryo/governor/admission.py` defaults to 0. After replay and execution-path
verification, trial the bounded boost on the opted-in destination. Add class capacity shares
only when pilot evidence warrants them, preserving feasible budget and lock constraints.

Gate: waiting time for eligible D/F tables is reported. A single table cannot take more
than its cap. Overload is visible when the cap means some tables wait. No "nothing
starves" sentence.

Sell line after quota tests: "Waiting time influences priority, class capacity is bounded,
and overload is visible." A capped boost does not guarantee that every neglected table
outranks every recently maintained table.

Query-aware sort and runtime replacement remain behind the C4/conformance gates. Do not infer
workload coverage from the thin `query_logs` table alone: `table_query_activity` and
`query_patterns` are separate sources. Verify each source's freshness, query diversity and
byte/latency coverage before deciding whether a layout pilot has enough evidence.

### 10.4 What "ahead of LakeOps" means

We will not out-benchmark a Rust compaction engine by writing a slide. Ahead, for the
buyer in section 4, means the following things they can check:

| Their claim | Our answer, after the gate | What we do not say |
|---|---|---|
| 95% faster than Spark, 12× queries, 76% less spend | We publish the section 6 protocol and the result, including a loss. | We do not "correct" their 221 s vs 1,612 s arithmetic in a sales post. |
| Event-driven, every table | S2 fires on a threshold, governor-capped. We show the table that was skipped and why. | We do not say we touch every table every night. |
| Query-chosen sort, tested on a branch | C4, after receipts and a real query sample. | We do not demo the sampled predictor as a rewrite experiment. |
| $5/TB optimized, Enterprise if you want VPC | No software fee. Self-host cost is itemized, including Spark, idle capacity, and operator time. | We do not quote a namespace-only daily cost as Cryo's cost. It leaves out the compaction. |
| Vendor-controlled platform versus a customer-owned outcome store | The audit row is in the customer's database, exportable, with unknown distinct from zero. | We do not say they have no audit. Their docs say they do. |

The LakeOps buyer who wants a managed service and a contracted SLA is not our buyer.
Sending them a comparison deck wastes the pilot. The page should say so in the first
paragraph.

### 10.5 Sell sheet

Use these. Do not paraphrase them into a stronger claim.

**Code-backed positioning, after the relevant publication/profile gates:**

- Cryo is an Apache-2.0, self-hosted Iceberg maintenance platform. Catalog access and
  engine execution are separate; the planner does not emit SQL.
- A supported Spark-isolated profile can keep maintenance writes off a selected Trino
  query cluster. Show the tested routing configuration rather than claim all deployments do so.
- Catalog-scoped Iceberg maintenance does not optimize arbitrary unregistered object prefixes.

Section 10.2's production numbers remain internal observations until CS-01 retains and
reconciles the evidence. Their repetition in this sell sheet would not clear them for promotion.

**Allowed after S1:**

- "Here is yesterday's file change, per action. Bytes are shown only when the engine
  returned them."

**Allowed after S2:**

- "Within the enabled scope, file signals can request bounded maintenance without a per-table
  pin. The Spark-isolated profile keeps those writes off the selected Trino query cluster."

**Allowed after S3 and S4:**

- "Selected hot tables receive bounded work between fleet runs. Waiting time influences
  priority, capacity is capped and unmet demand is visible."

**Allowed only after section 6 has a retained result:**

- "On workload W, Cryo version C cost X% less than Amoro version A over N days, with
  query p95 within Y%. Protocol and raw results: [link]."

**Banned, including in internal slides that will be forwarded:**

- "Cryo is the better tool."
- "The only open optimizer." Amoro is open. LakeOps has open components.
- A modeled monthly saving, or a modeled amount "still waiting", quoted as money. Both are models.
- "Coverage tripled, so next month's reclaim will too." It did not, and Spark still
  does not price the bytes.
- "95% faster" aimed back at LakeOps, or "10× vs Spark" aimed at Amoro. Those are their
  claims. We have not reproduced them.
- "Turn native back on and the reclaim returns." That engine reported success with no
  commit.

### 10.6 How this increases adoption

Adoption follows the sentence a skeptical platform engineer can verify in one sitting.
The sequence is the product, not a campaign around it.

1. **Read-only first.** A new install scans a scoped catalog and shows health, small-file
   count, and snapshot debt with no engine credentials that can write. This is the OSS
   plan's step, and it is the top of the funnel. Measure how quickly an evaluator gets a useful
   finding; do not claim the competitor's only possible onboarding path is a writer deployment.
2. **One table, one receipt.** After S1, the demo is a disposable table: plan, run,
   file counts before and after, and an explicit "bytes unknown" if that is the truth.
   That demo beats a dashboard of vendor percentages.
3. **Demonstrate query-cluster isolation.** Configure the Spark-only maintenance policy and
   show audited dispatch to its destination, with no writes to the designated Trino query
   cluster. Test that prohibited fallback is rejected. This is a routing/isolation demonstration,
   not a claim that a load probe proves isolation or that other products cannot provide it.
4. **Design partners, not a launch blast.** Five teams that match section 4. Each gets
   the sell sheet and a written "we will not claim X." Two still running it after four
   weeks is the adoption number. Stars are not.
5. **Comparison pages after the demo works.** "Cryo vs Amoro" leads with streaming vs
   batch-Trino, not a feature grid. "Cryo vs LakeOps" leads with who operates the
   control plane and who pays per TB. Head-to-head numbers wait for CS-08.

Promotion channels stay the ones in section 8. What changes is the asset order: the
receipt demo and the Trino refusal ship before the comparison pages. A comparison page
without those two artifacts is a feature list, and we lose a feature list to both of
them.

### 10.7 Staff and stop rules

Provisionally allocate S1/S2 to days 15–35 and S3/S4 to days 36–60 within section 7's team.
Re-estimate after tracing the active path and baseline defects; these are not delivery guarantees.
If staff is smaller, S3 and S4 wait.
Native engine work, query routing, and PTO training wait behind all four.

Stop the Spark-isolated S2 pilot if a run issues maintenance writes to its prohibited query
cluster, bypasses scope/budget/chunk constraints, or lacks an outcome record. Stop S3 when
it breaches pre-agreed rewrite-byte/compute budgets or query/health SLOs. Compare like units;
write amplification cannot be compared directly with a file-count reduction. Stop promotion
whenever a public sentence is stronger than the evidence that supports it.

## 11. Competitive displacement

Moved to `docs/_private-pw/COMPETITIVE_CAMPAIGN_2026-09-28.md`.

## Appendix B. Source register

All reviewed 2026-09-28; preserve the referenced version or access date when producing a public
comparison. Sources support the associated factual statements, not Cryo's proposed targets.

| Source | Use and boundary |
|---|---|
| [Amoro stable release](https://github.com/apache/amoro/releases/tag/v0.8.1-incubating) | Version/date baseline; queried through GitHub's public API. |
| [Amoro 0.8.1 architecture](https://amoro.apache.org/docs/0.8.1/) | Product scope and existing metadata integration. |
| [Amoro 0.8.1 self-optimizing](https://amoro.apache.org/docs/0.8.1/self-optimizing/) | Released scheduling and delete-maintenance design. |
| [Amoro development docs](https://amoro.apache.org/docs/latest/self-optimizing/) | Explicit version boundary; do not silently mix with stable. |
| [Amoro Kubernetes deployment](https://amoro.apache.org/docs/latest/deployment-on-kubernetes/) | Helm and configurable deployment; development docs. |
| [Amoro AIP-3](https://cwiki.apache.org/confluence/spaces/AMORO/pages/378473120/AIP-3+Event-Triggered+Optimization+of+Iceberg+Tables+in+Amoro) | Evaluation overhead and large-fleet motivation; proposal, not a Cryo comparison. |
| [Amoro AIP-5](https://cwiki.apache.org/confluence/spaces/AMORO/pages/421957106/AIP-5+Dynamic+Resource+Allocation+for+Optimizer) | Elastic optimizer direction; validate release inclusion. |
| [Amoro benchmark](https://amoro.apache.org/benchmark-report/) | Historical experimental scope. |
| [LakeOps quick start](https://lakeops.dev/docs/getting-started) | Vendor onboarding flow. |
| [LakeOps compaction docs](https://lakeops.dev/docs/compaction) | Vendor capability/configuration claims. |
| [LakeOps simulations](https://lakeops.dev/docs/simulations) | Vendor experiment workflow. |
| [LakeOps policies](https://lakeops.dev/docs/policies) | Scheduling, scope, inheritance and audit. |
| [LakeOps routing](https://lakeops.dev/solutions/query-routing) | Separate product scope; avoid mixed attribution. |
| [LakeOps pricing](https://lakeops.dev/pricing) | Posted rate, availability labels and Enterprise options; not a quote. |
| [LakeOps benchmark page](https://lakeops.dev/solutions/compaction) | Vendor numerical claims; not independently reproduced. |
| [LakeOps public repositories](https://github.com/lakeops-org) | Open component boundary; repositories/license metadata checked. |
| [Iceberg maintenance](https://iceberg.apache.org/docs/latest/maintenance/) | Cleanup and retained-data semantics. |
| [Iceberg branches](https://iceberg.apache.org/docs/latest/branching/) | Reference lifecycle, table schema sharing and audit workflow. |
| [Cryo paper audit](PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md) | Implementation fidelity and research claim limits. |
| [Cryo OSS plan](oss-launch-plan-2026-09-25.md) | Installation, distribution and independent adoption gates. |
| [Cryo cost plan](CRYO_COST_REDUCTION_PLAN.md) | Physical effects, complete expense and economic evidence. |
| [Amoro 0.8.1 configurations](https://amoro.apache.org/docs/0.8.1/configurations/) | Property names and defaults for the K2 importer. |
| [Amoro issues #3775](https://github.com/apache/amoro/issues/3775) / [#4018](https://github.com/apache/amoro/issues/4018) | AIP-3 sub-task outcomes (completed / not planned). |
| [Amoro MCP server](https://github.com/apache/amoro/tree/master/amoro-mcp-server) | Development AI surface; unreleased. |
| [GlacioData](https://github.com/GlacioData) | Self-described commercial Amoro; no product reviewed. |
| [nimtable/iceberg-compaction](https://github.com/nimtable/iceberg-compaction) | Upstream of LakeOps' public runtime; RisingWave iceberg-rust pin checked in both `Cargo.toml` files. |
| [iceberg-rust EPIC #2269](https://github.com/apache/iceberg-rust/issues/2269), [#2185](https://github.com/apache/iceberg-rust/pull/2185), [#2367](https://github.com/apache/iceberg-rust/pull/2367) | Upstream write-action status for K5; all open on 2026-09-28. |
| [LakeOps Terms of Use](https://lakeops.dev/legal/terms-of-use) | Automated-access and content-reuse restrictions for competitive monitoring. |
| [LakeOps tools comparison](https://lakeops.dev/blog/iceberg-compaction-tools-compared) | Vendor content scope for K7. |
| [Cryo cost log](cost-tracking/daily-cost-log.csv) | Retained daily namespace cost used in the Appendix A and §10.2 rechecks. |
| [Cryo native DataFusion plan](native-datafusion-compaction-plan.md) | Upstream-only iceberg-rust constraint behind K5. |
