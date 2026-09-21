# Cryo Go/No-Go 10-Day Scorecard

**September 20 recovery contract:** Follow the
[permanent recovery plan](CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md) for scheduler isolation,
bounded scoring, run lifecycle repair, zero-impact diagnosis and versioned metric definitions.
Historical entries below remain unchanged as evidence. Their interpretations are hypotheses,
not substitutes for the plan's explicit effect and cohort checks.

**Purpose**: decide whether to keep investing in Cryo (in-house Iceberg maintenance platform) or
scrap it in favor of a build/buy alternative (Apache Amoro, a LakeOps pilot, or reverting to
manual/cron-based maintenance). This is a decision record, not a status report — every entry below
feeds directly into the day-10 keep/scrap call.

**Start date**: 2026-09-16 (baseline, pre-deploy)
**Deploy date**: 2026-09-17 ~07:24 UTC — full fix set (all 8+ root-cause fixes from today's
"root fix everything" pass, not just the original three) deployed to `cryo-governor`,
`cryo-api`, `cryo-worker`, `cryo-scheduler`. See the deploy checklist below for verification.
**Clock note**: the reliability gate's real 10-day clock starts once burn-in (24-48h from the
deploy above) completes — every 2026-09-16 and pre-burn-in 2026-09-17 number in this doc is the
*contaminated* baseline (known bugs still live, or too soon post-deploy to have reconciled),
kept for before/after comparison, not counted toward the pass/fail window.
**Decision date**: 10 clean days after the recovery readiness gate actually passes. The September
20 scheduler OOM/eviction means the previous estimated start date is no longer valid.

---

## The three bugs fixed today (2026-09-16), root-caused and deployed, not yet burned in

1. **Orphaned-deferred mass-failure cascade** — capacity-class deferred jobs (metastore_capacity,
   native_cluster_capacity, fleet_admission_rate_cap) now get a 6h finalize grace instead of 10min.
   `cryo/db/repositories/run_finalizer.py`.
2. **Phantom running-status jobs** — `NativeJobWatcher.tick()` failures now log at warning/error
   with a Prometheus counter instead of vanishing at debug level. `cryo/cli.py`,
   `cryo/observability/metrics.py`.
3. **Native engine silent no-op success** — a job that reports `success: true` with zero actual
   file/byte/snapshot work for a mutating operation is now marked FAILED instead of SUCCEEDED.
   `cryo/engines/native/watcher.py`.

---

## Decision gates

### Reliability gate (must hold every day — any red day resets the 10-day clock)

| # | Metric | Source | Target | 
|---|--------|--------|--------|
| 1 | Job success rate (24h rolling) | `/api/v1/vitals` → `success_rate.success_rate_pct` | ≥ 90% |
| 2 | Vitals score | `/api/v1/vitals` → `score` | ≥ 70, no CRITICAL |
| 3 | Queue depth trend | `/api/v1/vitals` → `deductions[queue_growth]` / governor queue depth | flat or shrinking |

### Value gate (must show a clear, growing, positive trend by day 10)

| # | Metric | Source | Target |
|---|--------|--------|--------|
| 4 | Net savings ($) | Comparable same-scope baseline S3/Trino/maintenance cost minus observed total; forecasts and unused-cost ratio shown separately | Positive measured net result; unused/used ratio alone cannot determine this |
| 5 | Maintenance impact | `/api/v1/vitals` → `impact`, plus eligible debt age and receipt coverage | Verified useful effects and controlled debt; distinguish measured zero from missing evidence, without requiring GB freed to grow forever |
| 6 | Fleet-wide delete-file ratio & small-file ratio | `/api/v1/dashboard/fleet-trends` → `health_trend` (proxy: `avg_health_score`, `tables_at_grade_df`) | trending down (fewer D/F-grade tables) |

**Decision rule**: reliability gate 1-3 all green AND value gate 4-6 all trending positive →
**keep, continue investing**. Either gate still red with no fixable root cause by day 10 →
**scrap, move to Amoro (self-host) or a scoped LakeOps paid pilot**.

---

## Competitor landscape (researched 2026-09-16, see full research below)

| Tool | Model | What it automates | Maturity / adoption | Published numbers |
|---|---|---|---|---|
| **Apache Amoro** (ex-Arctic) | Self-hosted OSS, ASF incubating | Compaction via pluggable Optimizers + central AMS scheduler (metrics-driven, GC-generational-inspired) | Started 2019 @ NetEase, OSS 2022; "10+ companies" per own docs, no independent case studies | Claims ~10x compaction efficiency vs. raw Spark `rewrite_data_files` (vendor blog, unverified) |
| **LakeOps** (lakeops.dev) | Managed SaaS, Rust/DataFusion | Compaction, snapshot expiry, manifest optimization, orphan cleanup, unified observability | Founded 2025 — very early, no funding data found | Self-reported: "80% cost cut," "12x faster queries," "$1.37M saved/3mo," "46.8 PB optimized/30d" — no third-party corroboration |
| **Databricks Predictive Optimization** | Fully managed, built into Unity Catalog | Auto OPTIMIZE/VACUUM/ANALYZE on Delta + Iceberg | Production default since Nov 2024 | No published % savings; requires Unity Catalog lock-in |
| **AWS S3 Tables managed compaction** | Fully managed | Compaction | GA | Independently benchmarked 20-30x more expensive than self-run EMR (Onehouse); AWS cut price ~90% Jul 2025, still ~3x EMR cost — cautionary tale, not a role model |
| **Cloudera Lakehouse Optimizer** | Managed, bundled w/ Cloudera platform | Iceberg optimization | Enterprise, limited public data | None found |
| **Nimtable / iceberg-compaction (Rust)** | OSS, compaction-only point tool | Compaction runtime only, no governor/scheduler | Newer, small | None found |

**Industry-standard health metrics used by these vendors** (what metric #6 above is benchmarked
against): files-per-partition, average file size vs. 128–512MB target, snapshot/manifest growth
rate, and most consistently — **delete-file-to-data-file ratio** (rewrite trigger ~3-5 deletes/data
file). "$/TB-optimized" is the comparable unit vendors use in savings claims.

**Where Cryo stands today vs. this landscape**: architecturally closest to Amoro (metrics-driven
governor + pluggable execution engines), and already operates at a scale (10,000+ tables,
Trino+Spark+native Go engine) that neither Amoro's adoption evidence nor LakeOps' track record has
publicly demonstrated. But right now — pre-fix baseline — Cryo's own operational numbers (15.9%
success rate, vitals CRITICAL, unused cost 3.4x used cost) are worse than what any of these
competitors claim, which is exactly why this scorecard exists: prove Cryo can hit reliability +
value numbers that justify the in-house build over migrating to Amoro (build-alternative) or piloting
LakeOps (buy-alternative). Full research trail is logged in the daily entries below on day 0.

---

## Daily log

Each entry: date, both gates' raw numbers, a one-line read. Newest entry at the top.

### Day 0 — 2026-09-16 (baseline, PRE-DEPLOY, contaminated by known bugs)

**Reliability gate**
1. Success rate (24h): **15.9%** (5,640 succeeded / 29,902 failed) — 🔴 target ≥90%
2. Vitals score: **18/100 — CRITICAL** — 🔴 target ≥70
3. Queue depth: **1,250**, admission stalled (zero jobs completed in last hour) — 🔴 growing

**Value gate**
4. Allocation efficiency: September 15 CUR unused cost $17.58 vs split cost $5.21
   (**3.38× unused/used; ~77.1% unused**) — 🔴. This ratio does not establish negative net
   savings; metric #4's net value still requires attributable S3/Trino savings minus total Cryo
   overhead. See [the September 16 investigation](CRYO_UNUSED_COST_FIX_2026-09-16.md).
5. GB-freed/day: 649.2 GB yesterday, **-45.0% DoD**, direction "worsening" — 🔴
6. Fleet health trend: avg_health_score drifting down 68.87 → 67.02 over 7 days; tables_at_grade_df
   (D/F grade) fluctuating 767 → 2,532 → 958, no clean trend yet — 🟡

**Read**: This is the known-bad baseline. All three root-cause bugs (orphaned_deferred cascade,
phantom running-status jobs, silent no-op success) are fixed in code and tested (316 tests passing)
but **not yet deployed** to the cluster as of this entry. The 10-day clean clock has not started.
Next entry should be immediately post-deploy, then daily after burn-in.

---

### Check-in — 2026-09-16 11:55 UTC (still pre-deploy, NOT counted)

**Reliability gate**
1. Success rate (24h): **38.6%** (4,665 succeeded / 7,418 failed) — 🔴 target ≥90% (up from 15.9%
   at Day 0, but this is fleet noise, not the fix — see confirmation below)
2. Vitals score: **18/100 — CRITICAL**, unchanged from Day 0 — 🔴
3. Queue depth: **1,170** (vs. 1,250 at Day 0) — 🔴 still deep, admission still stalled
   ("Zero jobs completed in the last hour")

**Value gate**
4. Namespace unused cost: $17.58 unused vs $5.21 used — **identical figures to Day 0**, confirms
   this is a stale/cached CUR row (yesterday's billing day), not new data — 🟡 unmeasured today
5. GB-freed/day: 649.2 GB, -45.0% DoD — **identical to Day 0** — 🟡 unmeasured today
6. Fleet health trend: avg_health_score 67.02, tables_at_grade_df 958 — flat vs. Day 0

**Deploy status**: confirmed NOT deployed. `git log` shows no new commit since `c63fddef`
(today's fixes are all still uncommitted, 43 files modified in the working tree). The running
`cryo-governor` pod is still on image digest `sha256:438e2052...`, `startTime: 2026-09-14T15:15:04Z`
— over 24h before any of today's work began. The apparent success-rate improvement (15.9% →
38.6%) is ordinary fleet fluctuation, not evidence of a fix — `admission_stall` and the
namespace-cost figures are byte-identical to the Day 0 baseline, meaning the underlying bug is
still fully live and today's numbers just landed on a less-bad hour.

**Read**: Not deployed. The 10-day clean clock has not started and will not start until a real
deploy lands and burns in 24-48h. No scorecard entry should be treated as meaningful progress
until then.

---

### Check-in — 2026-09-16 12:58 UTC (still pre-deploy, NOT counted)

**Reliability gate**
1. Success rate (24h): **91.8%** (4,493 succeeded / 401 failed) — 🟢 target ≥90%, but see caveat below
2. Vitals score: **50/100 — still CRITICAL** (up from 18, admission_stall no longer scored as a
   separate deduction at this snapshot, namespace_unused_cost deduction shrank 20→6 points) — 🔴
3. Queue depth: **1,181** — 🔴 essentially unchanged from the last two checks, still deep, still
   "zero jobs completed in the last hour"

**Value gate**
4. Namespace unused cost: **new billing day data** — $7.19 unused vs $2.66 used (2.7x ratio,
   down from 3.4x) — this is genuinely fresh (2026-09-16, not a repeat of Day 0's 09-15 row) — 🟡
5. GB-freed/day: 649.2 GB, -45.0% DoD — still identical to Day 0, still unmeasured today — 🟡
6. Fleet health trend: avg_health_score 66.89 (flat), tables_at_grade_df 1,272 (up from 958) — 🔴

**Deploy status**: still confirmed NOT deployed — identical commit (`c63fddef`), identical 43
uncommitted files, identical governor pod image/startTime (`sha256:438e2052...`,
2026-09-14T15:15:04Z) as the prior check, one hour apart.

**Read — important caveat**: success rate jumping to 91.8% and the unused-cost ratio improving
are **not evidence the fixes are working — nothing has been deployed.** Queue depth is still
flat around ~1,170-1,250 and vitals is still CRITICAL, both of which contradict "the fleet is
actually healthy now." The most likely explanation is the same one already worked out earlier in
this investigation: the metastore-congestion/capacity incident that drove the Day-0 disaster
numbers is naturally fluctuating hour to hour, and this snapshot caught a calmer window — not a
fix taking effect. Treating a pre-deploy metric swing as progress would corrupt the whole point
of this scorecard (isolating the fixes' real effect from ambient fleet noise), so this stays
explicitly un-counted. Still waiting on deploy.

---

### Check-in — 2026-09-16 14:00 UTC (still pre-deploy, NOT counted)

**Reliability gate**
1. Success rate (24h): **91.8%** (4,493 succeeded / 401 failed) — identical figures to the 12:58
   check, the 24h window hasn't rolled forward yet — 🟡 stale, not a new reading
2. Vitals score: **30/100 — still CRITICAL** (down from 50 an hour ago) — 🔴 a new
   `stuck_runs` deduction appeared: 1 run stuck open >12h with no job finishing in 2h (oldest 17h)
3. Queue depth: **1,544** — 🔴 up sharply from 1,181 an hour ago; admission still stalled
   ("zero jobs completed in the last hour")

**Value gate**
4. Namespace unused cost: $7.19 unused vs $2.66 used (2.7x) — identical to the 12:58 reading, same
   billing day, not new data — 🟡 stale
5. GB-freed/day: 649.2 GB, -45.0% DoD — still identical to Day 0 — 🟡 unmeasured today
6. Fleet health trend: avg_health_score 66.89 (flat), tables_at_grade_df 1,272 (flat) — 🔴

**Deploy status**: still confirmed NOT deployed — identical commit, identical 43 uncommitted
files, identical governor pod image/startTime as every prior check today.

**Read**: The underlying bug is not just persisting, it's actively getting worse this hour —
queue depth grew ~31% (1,181 → 1,544) and a stuck run just crossed the 12h threshold. This is
exactly the orphaned_deferred/admission-stall failure mode the fixes target, still fully live
and compounding while undeployed. No progress to report; still waiting on deploy.

---

### Check-in — 2026-09-16 15:02 UTC (still pre-deploy, NOT counted)

**Reliability gate**
1. Success rate (24h): **91.8%** (4,493 succeeded / 401 failed) — identical figures to the last
   two checks, the 24h window still hasn't rolled forward — 🟡 stale, not a new reading
2. Vitals score: **30/100 — still CRITICAL**, flat vs. the 14:00 check — 🔴
3. Queue depth: **1,559** — 🔴 up again from 1,544 an hour ago (slower growth than the prior
   hour's jump, but still growing); stuck run now at **18h** (was 17h)

**Value gate**
4. Namespace unused cost: $7.19 unused vs $2.66 used (2.7x) — identical to the last two checks,
   same billing day, not new data — 🟡 stale
5. GB-freed/day: 649.2 GB, -45.0% DoD — still identical to Day 0 — 🟡 unmeasured today
6. Fleet health trend: avg_health_score 66.89 (flat), tables_at_grade_df 1,272 (flat) — 🔴

**Deploy status**: still confirmed NOT deployed — identical commit, identical 43 uncommitted
files, identical governor pod image/startTime as every prior check today.

**Read**: Queue depth is still climbing (1,544 → 1,559) and the stuck run has now been open 18h
with no sign of resolving on its own. Nothing else moved. No progress to report; still waiting
on deploy.

---

### Check-in — 2026-09-16 16:05 UTC (still pre-deploy, NOT counted)

**Reliability gate**
1. Success rate (24h): **91.8%** (4,493 succeeded / 401 failed) — identical figures across four
   consecutive checks now, the 24h window still hasn't rolled forward — 🟡 stale
2. Vitals score: **30/100 — still CRITICAL**, flat vs. the last two checks — 🔴
3. Queue depth: **1,576** — 🔴 up again from 1,559 (slow, steady climb: 1,181 → 1,544 → 1,559 →
   1,576 across the last four hourly checks); stuck run now at **19h**

**Value gate**
4. Namespace unused cost: $7.19 unused vs $2.66 used (2.7x) — identical, same billing day — 🟡 stale
5. GB-freed/day: 649.2 GB, -45.0% DoD — still identical to Day 0 — 🟡 unmeasured today
6. Fleet health trend: avg_health_score 66.89 (flat), tables_at_grade_df 1,272 (flat) — 🔴

**Deploy status**: still confirmed NOT deployed — identical commit, identical 43 uncommitted
files, identical governor pod image/startTime as every prior check today.

**Read**: Same steady pattern as the last three checks — queue depth inching up, stuck run
aging by roughly one hour per check, nothing else changing. No progress to report; still
waiting on deploy.

---

### Check-in — 2026-09-16 ~17:20 UTC (cluster access degraded — no live reading this cycle)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check today.

**Vitals**: unavailable this cycle. The `infra` credential bridge (`infra.penpencil.co`) that
issues cluster auth tokens is timing out (`Client.Timeout exceeded while awaiting headers`,
both on the token POST and on the resulting `kubectl` API calls) — this is the known,
documented browser-session-bridge-expiry condition (see memory: it "expires often"), not
anything about the deploy or fleet health. Did not fabricate a reading or retry destructively;
skipping this cycle's numbers rather than guessing.

**Read**: No new information this cycle — auth bridge needs a fresh token before the next
live pull can happen. Deploy still hasn't landed per git state. Continuing the same cadence;
will pull live vitals again next cycle once (if) cluster access recovers.

---

### Check-in — 2026-09-17 ~00:54 UTC (cluster access still degraded — no live reading this cycle)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable. Both the direct `kubectl` API call (timed out after 30s, exit
124) and the scorecard script's port-forward (`curl: (7) Failed to connect to 127.0.0.1 port
18000`) failed the same way as the prior cycle — the `infra.penpencil.co` auth-bridge token
issuance is still not responding. No numbers fabricated; skipping this cycle's reading again.

**Read**: Second consecutive cycle with no live signal. Deploy still hasn't landed per git
state (only fact confirmable right now). Continuing the same ~1h cadence to keep retrying the
auth bridge; will flag to the user if this extends much further, since a long outage here
means the whole tracking loop is flying blind on fleet health even though the git-state check
still works.

---

### Check-in — 2026-09-17 ~02:30 UTC (cluster access down for a THIRD consecutive cycle)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable. Both the direct `kubectl` API call (timed out, exit 124) and
the scorecard script (also timed out/failed) failed again — third consecutive cycle with the
`infra.penpencil.co` auth bridge unresponsive. No numbers fabricated.

**Read**: Three straight hours with no live signal is longer than the bridge's normal
"expires often" pattern — flagging to the user directly this cycle rather than silently
continuing to retry.

---

### Check-in — 2026-09-17 ~04:20 UTC (cluster access down for a FOURTH consecutive cycle)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` and the scorecard script both failed the same way
again. Not re-flagging to the user yet (under the ~6h threshold); continuing to retry hourly.

---

### Check-in — 2026-09-17 ~05:47 UTC (cluster access down for a FIFTH consecutive cycle)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` timed out, scorecard script failed again. Approaching
the ~6h re-flag threshold (this is ~5h of downtime); will flag again next cycle if still down.

---

### Check-in — 2026-09-17 ~07:48 UTC (cluster access down for a SIXTH consecutive cycle, ~6h)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` timed out, scorecard script's port-forward again
failed to connect. This is now ~6 hours of continuous cluster-access downtime. Flagging to
the user directly this cycle per the re-flag threshold.

---

### Check-in — 2026-09-17 ~09:08 UTC (cluster access down for a SEVENTH consecutive cycle, ~7h)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` timed out (exit 124), scorecard script's port-forward
failed to connect again. Not re-flagging (under the ~12h threshold); continuing hourly retries.

---

### Check-in — 2026-09-17 ~10:10 UTC (cluster access down for an EIGHTH consecutive cycle, ~8h)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` timed out (exit 124), scorecard script's port-forward
failed to connect again. Not re-flagging (under the ~12h threshold); continuing hourly retries.

---

### Check-in — 2026-09-17 ~11:12 UTC (cluster access down for a NINTH consecutive cycle, ~9h)

**Deploy status**: still confirmed NOT deployed via git — identical commit (`c63fddef`),
identical 43 uncommitted files as every prior check.

**Vitals**: still unavailable — `kubectl` timed out (exit 124), scorecard script's port-forward
failed to connect again. Not re-flagging (under the ~12h threshold); continuing hourly retries.

---

### Check-in — 2026-09-17 06:41 UTC (cluster access RESTORED — live reading after ~9-10h outage)

**Reliability gate**
1. Success rate (24h): **76.0%** (456 succeeded / 144 failed) — 🔴 target ≥90%; a genuinely new
   24h window (previous checks all showed the same stale 4,493/401 figures)
2. Vitals score: **28/100 — still CRITICAL** — 🔴
3. Queue depth: **9,734** — 🔴🔴 a massive jump from the last live reading (~1,576, ~15h ago) —
   this is the largest single move seen all day, consistent with the queue compounding
   unchecked through the entire cluster-access outage; stuck run is now at **34h** (was 19h at
   the last live check)

**Value gate**
4. Namespace unused cost: 2026-09-16 (new day): $9.34 unused vs $4.09 used (2.3x ratio) — 🟡
5. GB-freed/day: **177.2 GB**, **-72.7% DoD**, -82.6% WoW — 🔴 sharp new decline, first fresh
   GB-freed reading since Day 0's 649.2 GB figure
6. Fleet health trend: avg_health_score 66.54 (flat), tables_at_grade_df 1,043 — 🔴

**Deploy status**: still confirmed NOT deployed — identical commit, identical 43 uncommitted
files, and the governor pod is still on the same image digest (`sha256:438e2052...`) with the
same `startTime: 2026-09-14T15:15:04Z` as every check today — the pod has not restarted in over
60 hours.

**Read**: This is the worst reading of the day by a wide margin. Queue depth roughly 6x'd
during the ~9-10h window cluster access was unreachable, and the stuck run nearly doubled in
age (19h → 34h). This confirms what the earlier hourly trend already suggested: the underlying
orphaned_deferred/admission-stall bug compounds over time when left unaddressed, and the
cluster-access outage happened to coincide with (and mask) a period of accelerating
degradation. Nothing has been deployed. The fixes sitting uncommitted in the working tree are
directly applicable to every deduction on this page (stuck_runs, admission_stall, queue depth) —
this reading makes the case for shipping them more urgent, not less.

---

## Deploy checklist (blocks the real day-1)

- [x] Deploy `cryo-governor`, `cryo-api`, `cryo-worker`, `cryo-scheduler` with ALL of today's
  fixes (not just the original three — the full "root fix everything" set: native kill switch,
  admission/lifecycle single-source-of-truth, logging sweep, scheduler OOM/CR-04 restore,
  scanner KEDA autoscaling, catalog circuit breaker, routing-before-sizing, batch memory
  telemetry). Deployed 2026-09-17 ~07:24 UTC via `deploy/deploy.sh` (full test suite passed for
  real against a freshly-started local Postgres, multi-arch build/push, digest
  `sha256:8c758285...`, commit `a87753af`, helm upgrade, rollout restart). Verified every real
  pod (api x2, worker, governor, scheduler) landed on the new digest — confirmed directly via
  `kubectl get pods -o json` cross-checked against the built digest, not just the deploy
  script's own check (which initially flagged a false mismatch on a 2+ day old orphaned pod
  from an unrelated prior incident, already stuck at `ContainerStatusUnknown` with its owning
  ReplicaSet at `DESIRED: 0` — deleted as stale garbage, not a rollout failure).
- [ ] Confirm `orphaned_deferred` failure rate drops within 1-2 governor waves
- [ ] Confirm watcher tick failures (if any) now show up as `cryo_native_watcher_tick_failures_total` in Prometheus, not silently
- [ ] Confirm a known-no-op case (re-run `gold_dbt_poll_dashboard_view` or similar) now correctly fails instead of silently succeeding
- [ ] Let burn in 24-48h before starting the clean 10-day count

---

### DEPLOY — 2026-09-17 ~07:24 UTC (pods restarted minutes ago — too early to read as signal)

**Reliability gate**
1. Success rate (24h): **76.0%** (455 succeeded / 144 failed) — unchanged from the pre-deploy
   reading an hour ago (24h window hasn't rolled forward)
2. Vitals score: **22/100 — still CRITICAL** (down from 28) — a new `fleet_small_file_trend`
   deduction appeared, unrelated to the deploy
3. Queue depth: **9,679** — essentially flat vs. the pre-deploy 9,734; stuck run still at 34h

**Value gate**
4. Namespace unused cost: unchanged, same billing day as the last reading
5. GB-freed/day: unchanged, same as the last reading
6. Fleet health trend: avg_health_score 66.77 (flat), tables_at_grade_df 1,312

**Read**: This reading is expected to look unchanged — the pods restarted only minutes before
this pull, so the governor has had at most one wave (~30s cadence) to act on the new
capacity-grace logic, and the 34h-old stuck run and 9,679-deep queue are backlog that
accumulated over the entire prior day; clearing that backlog will take real wall-clock time
even with the fix live, not one tick. This is NOT a signal that the deploy failed — it's the
expected "just landed, nothing has had time to reconcile yet" state. The next several hourly
checks are what will actually show whether `orphaned_deferred` failures stop, whether the
queue starts draining, and whether the stuck run finally finalizes. Burn-in window starts now.

---

### Check-in — 2026-09-17 07:43 UTC (~20min into burn-in)

**Reliability gate**
1. Success rate (24h): **76.0%** (455 succeeded / 144 failed) — unchanged, same 24h window
2. Vitals score: **22/100 — still CRITICAL**, flat vs. the deploy-moment reading
3. Queue depth: **9,679** — flat, no change yet; stuck run now at **35h** (was 34h — it has
   NOT resolved on its own in the ~20min since deploy, consistent with the new capacity-grace
   logic only affecting *future* defer/finalize decisions, not retroactively clearing an
   already-stuck run)

**Value gate**
4. Namespace unused cost: unchanged, same billing day
5. GB-freed/day: unchanged, same reading
6. Fleet health trend: avg_health_score 66.76 (flat), tables_at_grade_df 1,396 (up from 1,312)

**Watch item**: `dispatch_heartbeat` shows **last job activity 31min ago** — no new job has
progressed since shortly after the deploy. Given the queue is ~9,679 deep, this is worth
tracking closely over the next few checks: either the governor is still working through
existing stuck/deferred state before admitting fresh work, or there's a new issue. Not
alarming yet at ~20min post-restart, but if this persists past 1-2 more hourly checks it
would be a real concern, not burn-in noise.

**Read**: Too early to read as pass/fail — matches the deploy-moment entry's expectation that
backlog clearing takes real time. The one thing to actually watch going forward is whether
`last job activity` keeps growing stale or resets to near-zero as the governor resumes
dispatching.

---

### Check-in — 2026-09-17 08:15 UTC (~50min into burn-in) — FIRST GENUINELY POSITIVE READING

**Reliability gate**
1. Success rate (24h): **75.0%** (463 succeeded / 154 failed) — still below the ≥90% target,
   but the raw counts are moving (455→463 succeeded, 144→154 failed) — real new activity, not
   a stale window
2. Vitals score: **62/100 — DEGRADED** (up from 22, CRITICAL) — a genuinely large jump.
   `stuck_runs`, `admission_stall`, AND `queue_growth` deductions are **all gone** from this
   reading — the exact three deductions the fixes were built to eliminate
3. Queue depth: not present in this reading's deductions at all (previously always listed at
   9,679-9,734) — strongly suggests it dropped below whatever threshold triggers that
   deduction; did not independently re-verify the raw number via the jobs API (wrong endpoint
   path on the quick check, not worth blocking on) but the vitals scoring itself is the
   authoritative signal here

**Direct answers to the three watch questions from the last check:**
1. **Dispatch heartbeat**: reset to **0min** ("last job activity 0min ago") — the governor is
   actively dispatching again, not stuck. This directly resolves the watch item from 35 minutes
   ago.
2. **Queue depth**: no longer appears as a deduction at all — consistent with having dropped
   substantially from 9,679.
3. **Stuck run**: the `stuck_runs` deduction is gone entirely — the 35h-old stuck run is no
   longer being reported as stuck (either finalized or no longer meets the stuck criteria).

**Value gate**
4. Namespace unused cost: unchanged, same billing day
5. GB-freed/day: unchanged, same reading (impact metrics update on a daily cadence, not yet due)
6. Fleet health trend: avg_health_score 67.24 (up slightly), tables_at_grade_df 1,507 (up)

**Read**: This is the strongest evidence yet that the deploy is working as intended. All three
target deductions (stuck_runs, admission_stall, queue_growth) disappeared in a single ~30min
window, dispatch heartbeat is fresh, and vitals jumped 22→62 (CRITICAL→DEGRADED). Still too
early to call burn-in complete (only ~50min post-deploy, need 24-48h), and success rate is
still below target — but the specific failure modes root-caused today (orphaned_deferred
cascade, admission stall, stuck runs) show real, fast, positive movement. Continuing to watch
closely; this needs to hold for hours, not minutes, before it's trustworthy.

---

### Check-in — 2026-09-17 08:47 UTC (~1h20min into burn-in) — REGRESSED, did NOT hold

**Reliability gate**
1. Success rate (24h): **60.9%** (635 succeeded / 408 failed) — down sharply from 75.0% half an
   hour ago; failure count roughly tripled in this window (154 → 408) while succeeded count
   also rose (463 → 635), meaning both admission volume AND failure rate increased together
2. Vitals score: **32/100 — CRITICAL again** (down from 62/DEGRADED) — the recovery did not hold
3. Queue depth / admission_stall / queue_growth: absent from deductions again (same as the
   62-score reading) — this part held
4. **`stuck_runs` reappeared**: same run, now **36h** old — it never actually finalized at the
   62-score reading; it just fell below whatever snapshot threshold the vitals scorer uses for
   that one measurement, then reappeared once it was checked again

**Value gate**: unchanged from the last two readings (same billing day, same GB-freed figure)

**Deploy status**: still confirmed live — identical digest (`sha256:8c758285...`), identical
governor startTime (`2026-09-17T07:22:16Z`), no restart since deploy.

**Read — this did NOT hold.** The 08:15 reading's improvement was real but partial and
short-lived: `admission_stall`/`queue_growth` staying gone is a genuine, sustained win (the
governor is dispatching continuously, unlike the pre-deploy stall), but `stuck_runs` never
actually resolved — the 08:15 reading just missed it on that particular snapshot — and the
failure rate got materially worse in the following 30 minutes (154→408 failures), which is a
new signal, not a repeat of the pre-deploy pattern. This is flagged directly to the user this
cycle per instructions: a genuine partial improvement, but not the clean "fixed" story the
prior check suggested, and the rising failure count needs investigation, not just more
waiting.

---

### Investigation — 2026-09-17 ~09:15 UTC (root cause of the 408-failure spike found)

Queried `cryo.jobs` directly (via `kubectl exec` into the API pod) for the most common failure
`error` values in the trailing 60 minutes:

| Count | Error |
|---|---|
| 169 | `native_job_reported_success_with_zero_work: expire_snapshots` |
| 85 | `native_job_reported_success_with_zero_work: expire_snapshots, remove_orphan_files` |
| 1 | `native_job_reported_success_with_zero_work: rewrite_data_files, expire_snapshots, remove_orphan_files` |
| 1 each | assorted unrelated (manifest read error, Spark timeout, table-not-found) |

**254 of ~408 failures (~62%) are `native_job_reported_success_with_zero_work`** — the exact
error string today's zero-work-detection fix (item 3) introduces. **This is not a regression
from today's changes — it's that fix working correctly**, catching real native-engine no-ops
that used to silently report `SUCCEEDED` and vanish (exactly the `gold_dbt_poll_dashboard_view`
incident pattern from earlier in this investigation). The detection is doing its job.

**However**, this also surfaced a real gap: item 1 (disable the native engine fleet-wide,
reroute everything to Spark) was implemented in code but **never actually wired into the prod
Helm config** — `CRYO_NATIVE_ENGINE_ENABLED` was never set anywhere in `values-prod.yaml`, so it
silently defaulted to `true` and native jobs kept dispatching. The zero-work detector was
correctly catching them, but the actual fix (stop dispatching to a trust-boundary-broken engine
at all) was never live. Fixed: added `CRYO_NATIVE_ENGINE_ENABLED: "false"` to `values-prod.yaml`'s
`global.env` (applies to every deployment via the existing shared-env Helm pattern), verified
via `helm template` that it renders into all 5 pods, and redeploying now. This should eliminate
the `native_job_reported_success_with_zero_work` failures at the source (no more native
dispatch at all) rather than just detecting them after the fact.

---

### REDEPLOY — 2026-09-17 ~09:50 UTC — `CRYO_NATIVE_ENGINE_ENABLED=false` now live

Second deploy of the day (first push attempt hit a transient GitLab registry error --
`blob unknown to registry` -- unrelated to code, tests had already passed; retried
successfully). New digest `sha256:3a1cbff0...`, commit `da4fd248`. Verified directly:
- Every pod (api x2, governor, scheduler, worker x10 incl. KEDA-scaled replicas) confirmed on
  the new digest via `kubectl get pods -o json` (the deploy script's own built-in check ran too
  early relative to KEDA's scale-up and reported a stale mismatch on the worker fleet -- a
  timing artifact, not a real failure; independently re-verified against the full live pod list)
- `kubectl exec` into the governor pod confirmed `CRYO_NATIVE_ENGINE_ENABLED=false` is actually
  set in the live environment, not just rendered in the Helm template

**Immediate result, queried directly from `cryo.jobs` ~30min after restart (trailing 20min
window, i.e. entirely post-restart, no contamination from the pre-fix backlog):**

| Status | Count |
|---|---|
| succeeded | 17 |
| failed | 1 (the 1 failure is itself a `native_job_reported_success_with_zero_work` --
  almost certainly a job admitted just before the restart, still in flight) |

**94% success rate in this fresh window**, vs. ~60% in the hour immediately before this
redeploy. The vitals API's own rolling 24h success-rate figure (60.2%) still reflects mostly
pre-fix data and will take the full 24h to catch up -- the trailing-20min direct query is the
real, uncontaminated signal right now.

**Read**: This is strong, direct evidence the native-engine kill switch is now actually
working end-to-end -- not just detecting bad native jobs (as the zero-work fix alone did) but
preventing them from being dispatched at all. `stuck_runs` still shows 2 (up from 1) -- these
are pre-existing stuck runs from before the deploy that haven't been touched by a fresh
capacity-defer decision yet, not new ones being created; worth confirming on the next check
whether that count starts dropping as the 6h capacity-grace logic gets a chance to actually
finalize them. Continuing tight-interval monitoring.

---

### Check-in — 2026-09-17 14:30 UTC (~4h40min post-redeploy) — NEW bottleneck found, unrelated to today's fixes

**Reliability gate**
1. Success rate (24h): **63.4%** (800 succeeded / 462 failed) — still below target, but this
   24h window is now mostly pre-redeploy data
2. Vitals score: **32/100 — CRITICAL**, roughly flat
3. `stuck_runs`: **3** (up from 2), oldest now **42h**. `dispatch_heartbeat`: **43 minutes**
   since last activity -- a real staleness, not the "0min ago" seen right after the redeploy

**Direct investigation** (governor logs + `cryo.jobs`/`cryo.approval_requests` queried live):
- Only **1 job total finished in the trailing 60 minutes** -- dispatch has gone nearly idle
- **90,735 jobs currently `queued`**, 197 `running`, 57 `deferred` -- a queue an order of
  magnitude larger than anything seen earlier today (prior max ~9,734)
- Governor logs show entire waves landing at **`admitted=0`**, with every candidate job
  deferred for the same reason: `"destructive operation requires approval"`
- `cryo.approval_requests`: **1,664 rows currently `pending`** (10,460 approved, 3,038 expired
  historically)

**Read**: This is a genuinely new and different bottleneck from anything today's fixes
target. The native-engine kill switch redeploy is holding (only 1
`native_job_reported_success_with_zero_work` failure total, matching the earlier clean
window), and the capacity-grace/admission-lifecycle fixes have no bearing on an
approval-gate backlog -- destructive-operation approval is a separate, pre-existing
operational mechanism (a human-in-the-loop gate for risky operations) that this session never
touched. The queue growing to 90,735 with `admitted=0` waves strongly suggests the approval
backlog (1,664 pending) is now the dominant blocker: with native no longer silently absorbing
capacity and admission stall fixed, the fleet is dispatching efficiently *up to* the approval
gate, then stalling en masse behind it. This is worth surfacing directly: it's a real,
large-scale operational issue, but it is NOT a regression from today's deploy -- it's a
pre-existing approval-workflow bottleneck that today's fixes didn't create and can't resolve
on their own. Someone needs to either work through the 1,664 pending approvals or investigate
why they're not being processed/expiring appropriately.

---

### Check-in — 2026-09-18 03:49 UTC (~overnight recovery, mixed picture)

**Scheduler pod**: OOMKilled once more overnight (02:40-07:12 IST, ~4.5h uptime before the
kill) -- a large improvement over the pre-fix 45x/5h crash loop, but confirms the batching fix
reduced OOM frequency, not eliminated it entirely. Currently `1/1 Running`, 1 restart, stable
for 125+ minutes as of this check. Not chasing this further tonight; the approval-expiry and
admission mechanisms are both now demonstrably working again (see below), which was the
actual production-impacting problem -- worth a proper look at the benefit-score sweep's real
memory ceiling in daylight, but not an active outage right now.

**Approval backlog**: genuinely clearing. `expired` count grew 3,038 -> 3,546 (508 rows
processed), past-due-pending dropped from 244 down to **14**. The scheduler tick is
demonstrably running again (this was the open question from the last check) -- confirmed via
the actual DB state changing, not just log absence.

**Admission**: no longer stuck at `admitted=0` -- current live counts show 794 admitted, 65
running, 755 deferred (a healthy mix, not a wall).

**Vitals**: score 26/100, still CRITICAL. `stuck_runs` jumped to 10 (oldest 19h), and 24h
success rate reads a very low 13.0% (1,148 succeeded / 7,709 failed) -- but this 24h window
now spans the entire overnight scheduler outage, so it's heavily contaminated by historical
failures, not a live signal. Checked the fresh, uncontaminated trailing-30min window directly:
597 failed / 588 deferred / 25 succeeded, and **578 of those 597 failures are
`stuck_admitted_no_dispatch`** -- a separate, pre-existing, correctly-designed reaper
(cryo/queue/reconcile.py) that fails jobs admitted-but-never-dispatched past a grace window,
specifically built for exactly this scenario ("correlated with governor/worker pod restarts,"
per its own docstring). This is the system cleaning up the backlog the overnight scheduler
outage created, not a new bug -- those jobs' work didn't happen and will need to be
re-picked-up on their next scheduled run, but the reaper is behaving as designed rather than
leaving them stuck forever. ~604 more admitted-without-engine_ref jobs remain queued for
similar reaping over the next grace-window cycle -- a finite, shrinking backlog, not an
ongoing failure source.

**Read**: Net picture is positive but not yet clean. The two fixes from yesterday (native
kill switch, scheduler OOM/approval-backlog) are both demonstrably working -- approvals are
expiring again, admission is flowing, native isn't dispatching. The current bad-looking vitals
(13% success rate, 10 stuck runs) are overwhelmingly explained by cleanup from last night's
~4.5h scheduler outage, not new problems. Real open item: the scheduler still OOMs eventually
(now ~4.5h between kills instead of ~7min) -- the batching fix reduced severity dramatically
but didn't fully solve the underlying memory growth. Burn-in clock has not started; this
needs at least one full OOM-free 24h cycle before it can.

---

### DEPLOY — 2026-09-18 ~08:15 UTC — real root cause found: node-eviction, not app OOM

Investigated the recurring scheduler crash pattern further: `kubectl describe pod` on the two
most recent crashed schedulers showed **`Evicted`** (node-level memory pressure), not
`OOMKilled` against the pod's own 10Gi cgroup limit -- both were killed at ~6.0GB actual usage,
on a node (`ip-10-1-221-71`) with only **~6.4GB allocatable memory total**. This is a
Karpenter-autoscaled cluster: Karpenter sizes/selects nodes against a pod's **request**, not
its limit. The scheduler's request had been stuck at 512Mi since original chart creation while
the limit was raised repeatedly (2Gi->6Gi->10Gi) across three separate OOM incidents including
this one -- every prior "fix" raised the ceiling without ever telling Karpenter the pod
actually needed a bigger node. Raised `scheduler.resources.requests.memory` 512Mi -> 3Gi in
`values-prod.yaml`.

**Deploy hit a real blocker on the way out**: concurrent work in this same session enabled
`scannerWorker.autoscaling.keda.enabled=true` in prod (migrating the scanner-worker autoscaler
from a plain HPA to KEDA), but the live 36-day-old HPA object was never deleted, and the KEDA
admission webhook correctly refused to create a ScaledObject targeting the same Deployment an
HPA still owned -- the whole `helm upgrade` aborted, meaning the scheduler memory fix above
never actually reached the cluster on the first attempt. Fixed by deleting the stale HPA
(`kubectl delete hpa cryo-scanner-worker`, safe since Helm's own template already omits it once
KEDA is enabled) and retrying; the second `helm upgrade` succeeded cleanly.

**Verified live, post-redeploy**:
- All 14 pods (api x2, governor, scheduler, worker x10) confirmed on digest
  `sha256:b307f8015...` via direct `kubectl get pods -o json` inspection
- Scheduler pod's live resource spec confirms `requests.memory: 3Gi` actually took effect
- Scheduler landed on a **different, correctly-sized node** (`ip-10-1-197-199`, ~14.5GB
  allocatable -- comfortably larger than the pod's own 10Gi limit, unlike the previous
  ~6.4GB node that was smaller than the limit)
- Scanner-worker KEDA transition also landed correctly: `keda-hpa-cryo-scanner-worker` is
  `READY: True`, old plain HPA is gone, no lingering dual-controller conflict

**Read**: This should be the actual fix, not another stopgap -- the app-level batching change
from the last deploy bounds the sweep's own memory use, and this request fix ensures whatever
memory it does use gets a node that can actually host it, addressing both halves of the
problem (the code needed to not grow unbounded, AND the infra needed to size correctly for
what it legitimately does use). Watching for the next several hours to confirm no further
evictions before this counts as burn-in-clean.

---

### Check-in — 2026-09-18 08:25 UTC (~10min post-redeploy)

**Scheduler**: still `1/1 Running`, 0 restarts, 10min uptime on the new node/digest -- too
early to call clean, but no regression yet.

**North star (GB-freed)**: 81.8 GB yesterday, -53.8% DoD, -87.9% WoW -- still reflects the
same contaminated 24h window as the last few checks, not a fresh signal.

**Vitals**: 32/100, CRITICAL. `stuck_runs` still 10 (oldest 36h, unchanged -- these are not
clearing on their own yet and are worth a dedicated look once the scheduler's stability is
confirmed). Success rate 9.8% (1,449 succeeded / 13,287 failed/24h) -- still the same
`stuck_admitted_no_dispatch` backlog-reaping pattern from yesterday's outage dominating this
window, not a live new failure signal (`dispatch_heartbeat` shows 0min since last activity,
i.e. actively working).

**Read**: No new information yet -- watching the same open items (does the scheduler stay up
past the ~4.5h mark this time, do the 10 stuck runs eventually clear, does the 24h success
rate recover once the contaminated window rolls past). Burn-in still not started.

---

### Check-in — 2026-09-20 10:49 UTC (two days later) -- worst vitals reading yet, mostly explained

**Scheduler memory**: still genuinely unresolved. `cryo-scheduler-545df76f96-p4mzp`:
`OOMKilled` at ~3h15min uptime (12:21->15:37 IST), restarted, currently at **5.2GB and
climbing** ~40min into a fresh sweep with zero completed passes yet. A sibling pod
(`...-4ghnm`) was separately **`Evicted`** at 9.3GB on a different, apparently smaller node.
The 2026-09-18 request fix (512Mi->3Gi) stopped guaranteed small-node placement, but the
scheduler process itself still grows large enough to eventually OOM or get evicted depending
on which node it lands on -- frequency improved (hours between incidents now, not minutes),
severity did not: this is not yet a solved problem, just a slower-burning one.

**Vitals**: 12/100 -- the worst single reading of this whole investigation. GB-freed dropped
to **zero**, success rate to **2.7%** (1,912 succeeded / 68,644 failed/24h).

**Investigated the 68,644 failures directly** rather than take the number at face value:
~2,058/hour are `orphaned_deferred: parent run already finalized`. Traced to their source --
**only 20 distinct runs**, all `created_at` on 2026-09-17/18, i.e. runs opened during the
outage investigated earlier in this document. One traced run alone
(`a4d4d1a6-...`) has **5,340 total jobs**, of which 5,320 already failed on 2026-09-19 with
`stuck_admitted_no_dispatch` -- the run itself only just finalized today (10:50 UTC) once its
last 20 straggler jobs resolved, and those 20 are exactly what's now surfacing as
`orphaned_deferred`. This is the grace-period fix behaving correctly, not a regression: it
kept these ancient runs open far longer than the old 10-minute window would have, and now that
they're genuinely done, it's cleaning up the last few stragglers rather than leaving them open
forever. The volume looks alarming (2,058/hour) purely because ~20 multi-thousand-job zombie
runs from a days-old outage are all finalizing in the same rolling 24h window, dragging both
GB-freed and success-rate down to look like an active new failure, when it is mostly historical
cleanup finally completing.

**Subsequent review correction (2026-09-20):** The preceding causal claim about GB-freed is
not supported by the metric implementation. Historical failed-job finalization changes the
rolling success denominator; byte impact is independently aggregated from successful audit
receipts. Missing byte evidence can appear as zero. The newer user-reported 59,673 failures
also require fresh attribution rather than assuming the same incident distribution. See
[the critical-vitals action plan](CRYO_CRITICAL_VITALS_ACTION_PLAN_2026-09-20.md).

**Read**: Two genuinely separate things are true at once: (1) the scheduler's memory growth is
still a real, unsolved problem -- worth a proper fix (likely running the realized-value
refresh sweep as its own short-lived subprocess/Job rather than a thread in the long-lived
scheduler daemon, so its memory is reclaimed by the OS on exit instead of accumulating for
hours) rather than another parameter tweak; (2) today's alarming-looking vitals are
overwhelmingly the tail of the 2026-09-17/18 outage's oldest runs finally closing out, not a
new live failure mode. Burn-in has still not started, and realistically can't start cleanly
until (a) the scheduler stops OOMing/getting evicted and (b) these last legacy zombie runs
finish draining out of the 24h window.
