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

**Proposed next step** (not yet started, needs its own scoping pass): a bounded "apply
recommendations" consumer — start with the lowest-risk `tier1`/`"action": "apply"` property
sets (pure Iceberg table-property `ALTER TABLE`s, no data rewrite, fully reversible), gated
behind an explicit opt-in per table/preset before ever going fleet-wide-automatic. Do **not**
wire `write.sort-order` auto-apply first — that implies an actual rewrite, not just a property
flip, and deserves the same "test before default" caution the scheduler itself got this session.

---

## 1. Blocked, waiting on a decision — retention fix

**Status: code done, tested, committed, pushed. Blocked at the docker build/deploy step by the
permission classifier (consequential production action, correctly gated).**

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

## 2. In progress — 24TB table compaction retry

**Status: running, past the point where the previous attempt failed.**

- Run `e085420b`, retrying `reports.gold_dbt_video_stats_reports_unified` and
  `unified_bq.gold_dbt_pw_bq` (24.3TB, 214K files) on the native engine after the original run
  (`54f0a8fa`) failed both with a generic `native_job_failed`.
- Previous attempt failed at 120.4 minutes. This retry passed that mark cleanly and is still
  running — a meaningfully different signal than a repeat of the same crash, not yet a result
  either way.
- History for `unified_bq.gold_dbt_pw_bq` specifically: only ever succeeded once, via a 62-hour,
  28-chunk Spark-on-EMR run (577K → 81.6K small files). Every native-engine attempt since
  (3 in a row before this one) has failed with 3 *different* errors (`argument list too long`,
  `native_job_missing_before_result_collection`, generic `native_job_failed`) — a real pattern
  suggesting this specific table may be past what the native engine can handle at this scale,
  not a transient issue. If this retry also fails, the honest next move is routing it to Spark
  like the one run that actually worked, not a 5th native retry.

## 3. Scoped, deliberately deferred (not forgotten)

| Item | Why deferred |
|---|---|
| Native engine's `optimize_manifests` silently no-ops (`iceberg-go` doesn't implement `rewrite_manifests` execute — confirmed in `engine-go/internal/ops/manifests.go`), but the overall job still reports `"succeeded"` | Fix requires changing Go-engine-level job-status aggregation (mark `"partial"` when a sub-op reports unavailable) — needs its own Go rebuild + test cycle, shouldn't be bundled into the retention-fix deploy in flight |
| Report file-count reduction and byte-count reduction as separate before/after metrics (a table can drop 90% of files while barely changing bytes — currently only the byte number is prominent, which undersells real request-cost wins) | Low-risk, quick — queued as the *next* change after the retention fix is deployed and verified, deliberately not bundled into the same deploy |
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

## Priority order, if picking one thing

1. **#0** — scope and build a bounded "apply recommendations" consumer. Highest leverage,
   because the hard part (generating good recommendations) is already done and running.
2. **#1** — get the retention fix actually deployed; it's done, tested, and sitting idle.
3. **#2** — let the compaction retry finish, then decide native-retry-again vs. Spark-route
   for `unified_bq.gold_dbt_pw_bq` based on the result.
4. Everything in **#3** and **#4** afterward, in the order listed.
