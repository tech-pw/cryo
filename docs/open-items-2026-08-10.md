# Open items — 2026-08-10

> Written to stop losing track across a long live-debugging session. Every item below was
> either found live against production this session or is a direct consequence of something
> found live. Ranked by leverage, not chronology — read top to bottom if you only have time
> for one thing.

---

## 0. The headline finding — read this one first

**Cryo already generates real, specific, well-reasoned per-table optimization recommendations,
automatically, on a schedule, against real production tables — and nothing has ever applied a
single one of them.**

This is not a hypothetical gap. Confirmed live today against `cdp_revenue.gold_batch_subject_schedules_pw`
(`GET /api/v1/tables/{id}/property-recommendations`), generated automatically by the scheduler's
telemetry tick (`scheduler/service.py::_generate_layout_recommendations`, which itself carries a
comment noting it was *already* rescued once from being dead code — `generate_layout_recommendation()`
was "fully implemented... but had no caller anywhere in the app"):

```
write.metadata.delete-after-commit.enabled = true   -- 13 manifests, 441 snapshots
write.metadata.previous-versions-max = 10            -- cap metadata buildup
write.sort-order = sort                              -- 4-column sort, layout_recommendation_id attached
write.merge-mode = merge-on-read                      -- 99.1% delete-file load
```

Every item carries `"action": "apply"` or `"action": "link_layout"`. Grepped the entire
codebase for any consumer of that field — zero matches. The generation pipeline (query-pattern
curation → layout analysis → property recommendation) is real and running. The **application**
step — turning `"action": "apply"` into an actual `ALTER TABLE` — was never built.

There is a second, parallel system (`cryo/optimizer/pto/`, "Shadow-only Predictive Table
Optimizer") with real candidate-space scoring, holdout evaluation, and counterfactual
experiment machinery — genuinely sophisticated — that's *also* never wired into anything beyond
a manual per-table API call, and is currently blocked from writing its own recommendations on
tables `layout_analyzer` already claimed (confirmed live: `POST /pto/recommend` on a real table
returned `"abstained", "layout_recommendation_owned_by_other_analyzer"`).

**Why this matters more than anything else in this doc**: this is very likely the direct,
concrete answer to "why doesn't Cryo get smarter on its own" — it already does the *thinking*
part continuously; it's missing the *acting* part. Closing this gap is higher leverage than any
new research feature below, because the recommendation-generation side is already paid for.

**Update, same day**: built. `cryo/scanner/property_autopilot.py` (commit `a6f1759`) is that
consumer — narrowly scoped to `tier1`/`action=="apply"`/`not needs_confirmation` items only,
leaning on `apply_table_properties()`'s own `TIER1_APPLYABLE_KEYS` allowlist as the real safety
boundary, wired into the scheduler's telemetry tick, **off by default**
(`Settings.property_autopilot_enabled`). 6 new tests, full suite still green. Deliberately does
**not** auto-apply `write.sort-order` — that implies an actual data rewrite, not a property
flip, and deserves its own dedicated caution pass, not a bundled default-on flip.
**Not deployed yet** — same blocked docker-build step as item #1 below; the code existing in
git doesn't make it run in production until that ships.

---

## 1. Deployed and verified live — retention fix + property autopilot

**Status: done.** Image `sha256:ea3f2b3ddef50f74a5af64e78aed5fd786920d706a24e8d0aaf2ea6e2b05aaa9`
(multi-arch, built by the user directly), `values-prod.yaml` updated (`b508bce`), `helm upgrade`
+ `kubectl rollout restart` run by the user (classifier blocked both from this side, same as the
docker build).

Verified, not just trusted:
- All 5 core pods (api×2, governor, scheduler, worker) confirmed on the new digest, `Ready`,
  0 restarts, `kubectl rollout status` reports success on all 4 deployments.
- New `total_snapshots`/`manifest_count` filter fields confirmed working live against real
  data (`total_size_gb < 10 AND total_snapshots > 100` correctly matched
  `admin.iceberg_compaction_scan`, 174 snapshots, 6.56GB).
- Property autopilot deployed but confirmed **off** (`property_autopilot_enabled: false`) —
  ships the capability, not the behavior, as designed.
- Unrelated drift noticed in passing: `cryo-worker` running 1 replica against
  `values-prod.yaml`'s declared 2 — timestamp shows this predates today's deploy, not caused by
  it. Not investigated further; flagging in case it matters later.

Backlog-aware retention takes effect on `default-native-deep`'s next scheduled run
(20:00 UTC). Property autopilot needs an explicit follow-up decision to turn on — see its own
entry above for what "on" means before flipping it.

**Update, same day**: a second build/deploy landed digest `sha256:c3729ccc...`, adding
failure-aware adaptive routing (item #5) on top of the above. Confirmed live the same way —
all 5 core pods on the new digest, all 4 rollouts report success. `adaptive_routing` also
turned on for `default-native-deep` via the preset API (a data change, not part of the
image), `engine_pool` scoped explicitly to `[native-datafusion, spark-k8s-fallback]` only —
Trino-Prod deliberately excluded per explicit instruction, not an intended maintenance
target. The file-count/byte-count reporting change (`fe69b3f`) landed *after* this build and
is not yet in a deployed image.

- Commit `78262a8` on `gitlab-main`: backlog-aware `expire_snapshots` retention (catches tables
  like `admin.iceberg_maintenance_log`, sitting on 499 snapshots at 22.15 commits/day — just
  under the 24.0/day streaming-detection cutoff, so the existing streaming-aware tightening never
  fired) + `total_snapshots`/`manifest_count` as real filterable/rule-matchable fields (so this
  became a genuine condition, not a hardcoded table list).
- Deliberately conservative: `retention_days` floor is 1 day, not 0 — confirmed `expire_snapshots`
  never deletes physical files (that's `remove_orphan_files`' separate job), but couldn't confirm
  nothing time-travels against these specific tables, so kept a full day of margin.
- Full test suite (1500+ tests) passes clean; only 2 pre-existing, unrelated failures
  (`test_fleet_trends_api.py`, a stale hardcoded date now outside its own 30-day window — not
  touched by this change).
- **Next step**: `docker buildx build --platform linux/amd64,linux/arm64 --push` (multi-arch,
  several minutes under emulation) → update `values-prod.yaml` digest → `helm upgrade` →
  `kubectl rollout restart` on scheduler/worker/governor. Needs explicit go-ahead or someone
  running `docs/deployment.md`'s documented sequence directly.

## 2. Resolved — 24TB table compaction retry, and a new bug found in the process

**Status: finished (failed), 3h6m, two genuinely different failures underneath one status.**

Run `e085420b` failed after 3h6m (11:06→14:12). But the two tables failed for *different*
reasons, and only one of them is a real compaction failure:

- **`unified_bq.gold_dbt_pw_bq`** (24.3TB, 214K files): `native_job_failed`, K8s Job status
  genuinely `Failed`. This is the **4th native-engine failure in a row** for this table, each
  with a different error (`argument list too long`, `native_job_missing_before_result_collection`,
  2x generic `native_job_failed`). It has only ever succeeded once — a 62-hour, 28-chunk
  Spark-on-EMR run (577K → 81.6K small files). Verdict: this table is past what the native
  engine can handle at this scale. **Next native retry should not happen** — route it to Spark
  like the one run that actually worked.

- **`reports.gold_dbt_video_stats_reports_unified`** (1.5TB): marked `native_job_no_result`.
  The underlying K8s Job shows `Complete`, 1/1, finished in 41 minutes — which first looked like
  a false-negative (job actually succeeded, Cryo's tracking just didn't notice). **Checked
  empirically via a rescan (`e8d6606e`) and that theory is wrong**: post-run stats are
  byte-for-byte identical to pre-run (11,045 files, 7,853 small files, 1,521.5 GB, same as
  before the run started). The compaction did not happen. Correcting the record rather than
  leaving the wrong theory standing.
- **`unified_bq.gold_dbt_pw_bq`**: rescan confirms pure organic growth during the failed run
  (214,170→215,212 files, 87,449→88,396 small files) — consistent with a genuine failure, no
  surprises there.

**The real, more concerning finding**: a K8s Job can report `Complete` (clean pod exit) while
the maintenance operation it was supposed to run never actually happened. That's a different and
worse problem than "result-collection lost track of a real success" — Cryo currently has **no
way to distinguish** "pod exited 0 after doing its job" from "pod exited 0 having done nothing"
except the eventual multi-hour timeout that happened to land on the right answer here by luck,
not by detection. The `TaintManagerEviction` event around the 100-minute mark (cancelled, pod
not actually killed) is still the best lead on *why* — plausibly the pod's main work aborted
partway through in response to the eviction signal but still exited cleanly afterward — but this
needs a live reproduction or the K8s events (already rotated out here) to confirm before writing
a real fix. **Not yet scoped.**

## 3. Scoped, deliberately deferred (not forgotten)

| Item | Status |
|---|---|
| ~~Native engine's `optimize_manifests` gap~~ | **Done** (`d408fed`). Real root cause: the Go engine's own status aggregation was already correct -- the actual gap was `_dispatch_fallback_operations()` (which genuinely attempts to route `optimize_manifests` to Spark during admission) only ever logging its outcome, never persisting it. Used the already-existing `store.record_audit()`/`AuditRecord` (has a `manifests_rewritten` field) instead of inventing anything new. Covers every exit path (success, failure, engine mismatch, missing config, unresolvable destination) with its own `try/except` so an audit-write failure can never mask the real dispatch outcome. 22 tests (16 existing + 6 new), full suite green. Not deployed yet. |
| ~~Report file-count reduction and byte-count reduction as separate before/after metrics~~ | **Built, not deployed** (`fe69b3f`). New `effectiveness` field on `GET /tables/{id}/runs`: `file_count_reduction_pct`, `small_file_reduction_pct`, `byte_reduction_pct` as independent signed percentages + a headline. 10 tests, full suite green. Landed *after* today's `sha256:c3729ccc...` build/deploy, so it needs its own build cycle — not live yet. |
| Sequence S3 Intelligent-Tiering *after* compaction, never independently (monitoring fee is $0.0025/1,000 objects/month — a bucket full of uncompacted small files could net-negative before compaction ever gets a chance to reduce object count) | Not a Cryo code change — an infra/bucket-lifecycle-policy decision for whoever manages the S3 buckets |

## 4. Research-grounded future projects

### WAIR-style query-pattern-aware reclustering
Verified against the actual paper (`arXiv:2602.23289`, Liu/Zhou/Yan/Zhang, SIGMOD 2026) —
extracted the real algorithm (boundary micro-partitions + Algorithm 2's sliding-window
cost-triggered reclustering), not just a search summary. Real gap: WAIR needs per-file
**utilization** (bytes a query needed ÷ bytes it had to read) — Cryo has per-file min/max and
aggregate query counts, but not this per-query-per-file signal yet.

**Given finding #0 above, this project should be re-scoped**: `cryo/optimizer/pto/` already has
holdout + counterfactual evaluation machinery that's *sophisticated enough to validate WAIR-style
recommendations once built* — the missing piece isn't a new evaluation framework, it's the
per-file utilization data feed. Phase 0 is the same shape either way (Trino query-log → per-file
utilization pipeline) but now clearly plugs into existing infrastructure rather than needing new
scaffolding. **Not yet scoped in detail — do this only after #0 is resolved**, since building a
richer recommendation signal before anything consumes the recommendations already being made is
the wrong order of operations.

### Cost-reduction research (S3 + Trino on Iceberg), full findings
See conversation record for the complete cited writeup. Ranked list, already reflected in
items above where actionable:
1. Sequence tiering after compaction (§3 above).
2. Report file-count vs. byte-count separately (§3 above).
3. Query-pattern-aware sort order (WAIR, above) — high effort, real research project.
4. Fix `optimize_manifests` native no-op (§3 above) — this is the one with a live cost impact
   *today*: every native-routed table with manifest bloat is paying extra Trino planning cost on
   every query, silently, because manifest consolidation has never actually run despite the job
   log saying it did.
5. NDV/Bloom-filter (Puffin) statistics generation as part of `compute_statistics`, if not
   already happening — not yet checked whether this is live or another dormant-generation gap
   like #0.

---

## 5. Built — failure-aware adaptive routing

**Status: committed, tested, pushed (`0cdad5e`). Not yet deployed** (needs the same
build/deploy cycle as items #0/#1).

Directly answers "why does a table that's failed 4 times keep getting sent to the same
engine": `adaptive_routing.py` already had real size/capacity/latency/cost-based destination
scoring, but zero memory of failure history — a 4x-failed table scored identically to a
never-tried one. Also confirmed live: `adaptive_routing` itself is `false` on every preset
actually running today — same "real infrastructure, never turned on" shape as items #0's
`layout_analyzer`/`pto/` finding.

New: `SQLSchedulerStore.consecutive_engine_failures()` (one bulk query per tick, scoped to
the current run's tables only — not fleet-wide, learned from the OOM incident earlier this
session) feeds a per-table failure-streak signal into `rank_destinations()`, which now
excludes (score `-inf`) any destination with `>= max_consecutive_failures_before_exclusion`
(default 2, configurable) consecutive failures on that specific table. Fails open if nothing
else is available. 13 new tests, full suite green.

**Does not fix anything by itself** — `adaptive_routing` is still off. Turning it on for
`default-native-deep` is the actual remaining decision, and should happen deliberately (it
changes routing behavior for the whole fleet-wide catch-all preset), not as a side effect of
this commit landing.

## Priority order, if picking one thing

1. **#0** — scope and build a bounded "apply recommendations" consumer. Highest leverage,
   because the hard part (generating good recommendations) is already done and running.
2. **#1** — get the retention fix actually deployed; it's done, tested, and sitting idle.
3. **#2** — let the compaction retry finish, then decide native-retry-again vs. Spark-route
   for `unified_bq.gold_dbt_pw_bq` based on the result.
4. Everything in **#3** and **#4** afterward, in the order listed.
