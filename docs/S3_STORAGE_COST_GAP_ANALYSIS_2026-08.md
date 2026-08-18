# S3 storage cost gap analysis — Cryo effectiveness & fix backlog

**Date:** 2026-08-18  
**Author:** DE cost investigation (Cursor agent session)  
**Status:** Research only — no code or prod config changed by this document  
**Audience:** Cryo maintainers, data-platform, cost owners

> **Validated 2026-08-18 (Claude Code session)** — every claim in this doc was checked against live code, the live cluster, and a fresh Grafana pull rather than taken on trust. Most held up (§12's namespace cost numbers matched an independent pull almost to the dollar; G1, G2, G3, G7, G11, U3, U4 all confirmed). **Three items were wrong or materially stale and have been corrected in place** rather than left misleading: **G4** (`default-native-deep` already has cron/mode/ops configured — the real gap is `enabled: false`), **G8** (S3 request-log ingest is already enabled on all 7 real prod buckets, not just `pw-data-cdp-prod-temp`), and **G9/U5** (the $100/day cost ceiling likely never fires at all, due to a 24–48h CUR-lag bug, not that it "trips often" as originally written). Jira tickets filed for the real, currently-open issues found — see each corrected section for the ticket number. Don't act on a claim that contradicts the correction notes below it.

This doc answers: *Cryo is running snapshot expiry, orphan cleanup, and compaction on our S3 Iceberg buckets — why is CDP S3 storage still ~$8k/month and climbing on hot tables?* It consolidates evidence from this repo, Grafana CUR (`cost-db`), Slack `#de-daily-cost-updates`, and the separate Trino infra report at `~/deployment_repo/TRINO_COST_OPTIMIZATION_REPORT.md`.

---

## 0. Executive summary

| Question | Answer |
|----------|--------|
| Is Cryo broken? | **No** — it is a live control plane on `data-platform-cluster` (`cryo` namespace) maintaining the `cdp` catalog (~12.9k tables in prod RDS). |
| Is Cryo winning on S3 storage? | **Partially** — maintenance runs are real (thousands of succeeded jobs in Aug), but **S3 Standard storage on ~316 TB is mostly legitimate bytes**, not a billing bug. Reclaimable waste is **snapshot-pinned duplicates, orphans, and unmaintained hot tables** — not everything Cryo scans. |
| Main gap | **Coverage + cadence + byte-deletion path**, not missing features: tables without an effective preset never get scheduled; `expire_snapshots` alone does not delete S3 objects until `remove_orphan_files` (DEEP mode) runs; one-off deep runs do not hold on tables writing hundreds of snapshots/week. |
| Trino vs S3 | **Different cost lines.** Trino `trino-db` EKS spend (~$2.5k/week) is mostly **unused pod capacity** (coordinator/gateway over-request). Cryo can reduce Trino **used** cost indirectly via compaction (fewer scanned bytes) — see §8. S3 **storage** is TimedStorage on objects Cryo must expire/orphan or operators must delete/tier. |
| **Cryo namespace unused %** | **~73%** of `cryo` namespace EKS bill is **unused** split cost (request − actual), mostly **untagged Spark/native maintenance Jobs** — see **§12**. Not an S3 problem; separate from §4 S3 gaps. |

**Highest-leverage Cryo fixes (this repo), corrected 2026-08-18:**

1. Give `unified_bq.gold_dbt_pw_bq` (and similar named hot gold tables) a **real recurring cadence** — it already has a dedicated enabled preset, but no cron, and is still grade F / 189,844 files / 24.5TB right now. → **PDAE-8542**
2. Decide `default-native-deep`'s role: it's fully configured (cron `0 20 * * *`, deep mode, full operations) but sits `enabled: false` — either turn it on as the fleet catchall or retire it deliberately. → **PDAE-8540**
3. **Alert** on `cryo_scheduler_unmatched_tables{needs_compaction="true"}` (metric exists; no Prometheus rule today). → **PDAE-8543**
4. **Rank maintenance by Trino scan cost × fragmentation**, not health grade alone (recommendation from Trino report §9 — scheduling change inside Cryo).
5. Fix the **cost ceiling's CUR-lag bug** — it checks "today," which structurally reads $0.00 until the day is already over, so the $100/day safety breaker very likely never fires despite real spend running ~$210/day. → **PDAE-8541**
6. ~~Enable S3 access-log ingest on bronze/gold buckets~~ — **already enabled on all 7 real prod buckets, verified live. No action needed.**

---

## 1. Scope and data sources

### 1.1 In scope (CDP prod S3)

Seven buckets tracked in org cost dashboards and `~/powerbi_issues` cost skill:

- `pw-data-cdp-prod-gold-layer`, `gold-layer-audit`
- `pw-data-cdp-prod-silver-layer`, `silver-layer-audit`
- `pw-data-cdp-prod-bronze-layer`, `bronze-layer-audit`
- `pw-data-cdp-prod-temp`

**Aug 2026 CUR mix (30d, Grafana `cost-db` uid `ef75vmxq6mnswf`):**

| Category | 30d USD | ~$/day | Share |
|----------|---------|--------|-------|
| Storage (`TimedStorage-*`) | $8,132 | $271 | **66%** |
| GET/HEAD (Tier-2) | $3,305 | $110 | 27% |
| PUT/LIST (Tier-1) | $916 | $31 | 7% |
| Other | ~$18 | — | <1% |

**CloudWatch bytes (Aug snapshot):** ~**316 TB** total — gold ~121 TB, silver ~82 TB, bronze ~75 TB, temp ~27 TB (flat; not ephemeral).

### 1.2 Out of scope / separate reports

| Topic | Where |
|-------|--------|
| Trino namespace **unused** EKS cost (coordinator/gateway requests, KEDA) | `~/deployment_repo/TRINO_COST_OPTIMIZATION_REPORT.md` §1–2, §10 |
| Power BI gateway duplicate-query fan-out | `~/powerbi_issues/` (orthogonal to Cryo S3 maintenance) |
| Cryo's **own** EKS bill (~$210/day namespace; $100/day ceiling) | `cryo/reporting/cost_ceiling.py`, `values-prod.yaml` |

### 1.3 Internal Cryo docs already covering S3 economics

| Doc | Role |
|-----|------|
| [`docs/s3-cost-investigation-2026-08.md`](s3-cost-investigation-2026-08.md) | Gold/bronze CUR breakdown, `gold_dbt_pw_bq` mechanism, IT vs deletion |
| [`docs/s3-request-cost-tracking.md`](s3-request-cost-tracking.md) | Per-table GET/PUT ingest design; **1/11 buckets enabled** |
| [`docs/open-items-2026-08-10.md`](open-items-2026-08-10.md) | Property autopilot, backlog retention, native routing |
| [`docs/TABLE_DEDUPLICATION_DESIGN.md`](TABLE_DEDUPLICATION_DESIGN.md) | ~17.97 TB in `_temp`/`_backup` naming patterns — **no auto-drop shipped** |

---

## 2. What Cryo is designed to do (verified in code)

Architecture: Scanner → Postgres stats → Scheduler (preset cron) → Governor → Native Go / Spark K8s Jobs → Trino catalog Iceberg procedures.

**Maintenance operations** (`cryo/planner/planner.py`):

| Operation | LIGHT | STANDARD | DEEP |
|-----------|-------|----------|------|
| `expire_snapshots` | ✓ | ✓ | ✓ |
| `remove_orphan_files` | — | — | **✓ only** |
| `optimize` (compact) | — | ✓ | ✓ |
| `optimize_manifests` | — | — | **✓ only** |

**Critical planner note (lines 127–139):** STANDARD mode **explicitly skips** orphan cleanup and manifest rewrite; plan notes say so. Physical S3 deletion for dereferenced files requires **DEEP** runs including `remove_orphan_files`.

**Official Iceberg alignment:** expire first, then orphan cleanup — matches [Apache Iceberg Maintenance](https://iceberg.apache.org/docs/latest/maintenance/) and [AWS Prescriptive Guidance — Optimizing storage](https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/best-practices-storage.html).

**Prod deployment** (`deploy/helm/cryo/values-prod.yaml`, Aug 2026):

- Image digest `sha256:d95d4913…` on `data-platform-cluster`, namespace `cryo`
- `CRYO_SCAN_ENGINE=go`, `CRYO_PROPERTY_AUTOPILOT_ENABLED=true`, `CRYO_ROI_GATE_ENABLED=true` (shadow), `CRYO_COST_CEILING_ENABLED=true` ($100/day on **cryo** namespace only)
- Native K8s Jobs + Spark fallback; `default-native-deep` uses adaptive routing to `[native-datafusion, spark-k8s-fallback]` (Trino excluded as maintenance target)

---

## 3. Evidence Cryo is running (not idle)

### 3.1 Aug 2026 maintenance burst (Cryo API + fleet health)

From `~/deployment_repo/TRINO_COST_OPTIMIZATION_REPORT.md` §0 (pulled from Cryo `/api/v1/runs`, `/api/v1/dashboard/fleet-trends`, 2026-08-17):

| Date | Succeeded maintenance jobs* | Fleet avg health | Tables D/F | trino-db daily cost |
|------|----------------------------|------------------|------------|---------------------|
| Aug 3 | 170 | 55.5 | 8,406 | $796.57 |
| **Aug 4** | **8,804** | 56.3 | 8,061 | $753.90 |
| Aug 6 | 397 | **69.2** | **3,835** | $535.40 |
| Aug 9 | 7 | 69.1 | 4,113 | $315.76 |

\*Succeeded `expire_snapshots` / `remove_orphan_files` / `optimize` / `optimize_manifests` only — scan-only jobs excluded.

**Interpretation (Trino report's own caution):** Fleet health jump (56→69, D/F count halved) is **real and dated** alongside heavy maintenance. Trino **used** daily cost fell in the same window — **consistent** with less scan overhead after compaction, but **not proven causation** without per-table before/after CPU-seconds on compacted tables. **Do not** cite Trino **unused** cost drop (~55% Aug 8–10) as Cryo — that line moves with pod requests/replica count, which Cryo does not control.

### 3.2 Shipped fixes (Aug 10–12 deploy)

Documented in `values-prod.yaml` changelog and `docs/open-items-2026-08-10.md`:

- Backlog-aware `expire_snapshots` retention (`total_snapshots` rules)
- Property autopilot (metadata `delete-after-commit`, etc.)
- Failure-aware adaptive routing for `gold_dbt_pw_bq` native failures → Spark
- Fallback dispatch outcomes persisted to audit log (`d408fed`)

---

## 4. Verified gaps — why S3 storage still doesn't fall

Each item: **claim → evidence → fix direction**.

### G1. Fleet coverage — no default preset

**Claim:** Tables without `effective_preset` are never cron-scheduled.

**Evidence:**

- Migration `migrations/versions/20260707_0020_unset_native_default_preset.py`: all presets `is_default = false`; tables without assignment/rule get `provenance = "none"`.
- `cryo/scheduler/rules.py` — `effective_preset()` returns `None` when no match.
- `cryo/config.py` lines 405–416: **Real incident 2026-08-12** — **1,267 active tables** with `needs_compaction=True` matched no enabled preset (includes 24 TB table pinned to a **disabled** preset). Metric: `cryo_scheduler_unmatched_tables`.
- `deploy/prometheus/rules/cryo-safety.yml`: **no alert** on unmatched tables (grep confirmed).

**Fix:** Preset rules for bronze schemas (`pw_bq`, `ai_ncert`, `admin_logs`, …); re-pin or fix stale assignments; optional fleet-wide catch-all rule with guardrails; Prometheus alert when `needs_compaction=true` unmatched > threshold.

---

### G2. Wrong cohort for biggest $ tables — dark tables ≠ hot writers

**Claim:** Current dark-table campaign does not target actively written cost drivers.

**Evidence:**

- `cryo/telemetry/dark_tables.py`: surfaces tables with **sustained zero queries** (`query_count_30d=0`, 3 daily confirmations). Does **not** delete — candidates only.
- `docs/s3-cost-investigation-2026-08.md` §2.4: `unified_bq.gold_dbt_pw_bq` and `mview.dbt_gold_user_cohort_base_facts_free` are **actively written and queried** — **not** in dark-table cohort. Wave 2 / PDAE-8276.
- Slack Aug 2026: bronze API WoW spikes on **new ingest tables** (pw_bq.*) — same pattern: hot, not dark.

**Fix:** Separate **hot-table retention preset** (recurring DEEP) from dark-table archival; do not expect dark-table UI to fix gold_dbt_pw_bq or bronze ingest.

---

### G3. Cadence — one-off maintenance does not hold

**Claim:** Single deep runs are erased by high commit rates.

**Evidence:**

- `docs/s3-cost-investigation-2026-08.md` §2.4: After PDAE-8269 pilot (~257.6 GB compacted via Spark), `gold_dbt_pw_bq` returned to **612k files, 661 snapshots**, **378k–476k S3 PUTs/day**.
- Gold `TimedStorage` daily cost climbed ~50–80% over 30d despite ~7-day sawtooth (partial cleanup not keeping pace).
- Iceberg spec: each commit creates a snapshot; files stay until expired ([Maintenance — Expire Snapshots](https://iceberg.apache.org/docs/latest/maintenance/)).

**Fix:** Recurring schedule sized to `commits_per_day` (streaming-aware retention already in `planner._expire_params`); multiple DEEP runs/day for top offenders; not weekly-only cron.

---

### G4. `default-native-deep` is fully configured but disabled

**Corrected 2026-08-18** — the original claim ("manual-only, no cron") is **stale**. Verified live via `GET /api/v1/presets`:

```json
{
  "name": "default-native-deep",
  "schedule_cron": "0 20 * * *",
  "mode": "deep",
  "operations": ["expire_snapshots", "remove_orphan_files", "optimize"],
  "enabled": false
}
```

The seed migration's `NULL` cron / `manual runs only` (as originally cited from `migrations/versions/20260706_0011_default_native_preset.py`) was superseded by a later prod-DB preset-API edit — cron, mode, and full deep operations are all correctly set. **The real, current gap is `enabled: false`.** This preset is the fleet-wide safety net for any table that doesn't resolve to a more specific preset via `effective_preset()` — with it disabled, tables falling through to it get no recurring maintenance at all, silently. Current live coverage is instead coming from a set of narrower, explicitly-enabled `claude-*`-named presets (`claude-cold-table-fleet-optimization`, `claude-spark-run-unified-bq-gold-dbt-pw-bq`, `spark-large`, `spark-medium`) built during earlier live-testing sessions — real, working, but not a substitute for the intended default-catchall semantics.

**Fix:** Decide deliberately whether `default-native-deep` should be enabled as the fleet-wide catchall (with appropriate guardrails/cooldown so it doesn't fight the narrower presets for tables they already cover), or whether the narrower preset set is now the intended permanent design and this preset should be retired/documented as superseded. Either is fine — leaving it configured-but-silently-disabled is the actual problem. → **PDAE-8540**

---

---

### G5. STANDARD / LIGHT runs do not delete S3 bytes

**Claim:** Expire without orphan leaves objects on S3.

**Evidence:** `cryo/planner/planner.py` lines 66–67, 127–139 (quoted in §2).

**Fix:** Hot cost tables must use **DEEP** preset mode (or ensure Spark/native path runs `remove_orphan_files`). Audit log should show both operations on reclaim runs.

---

### G6. Orphan byte estimates often Tier-0/1 only

**Claim:** Full S3 LIST for orphans runs weekly by default, not every scan.

**Evidence:**

- `cryo/config.py`: `cost_full_scan_every_hours` default **168** (weekly).
- `docs/s3-request-cost-tracking.md`: Tier-2 LIST cadence-gated; orphan projections often 0 until full scan.

**Fix:** `cost_full_scan` on top-N TB tables; or weekly DEEP maintenance (orphan step does LIST under table prefix).

---

### G7. Native engine blocks OPTIMIZE on many bronze CDC tables

**Claim:** Tables with delete files may get expire+orphan only, not compact.

**Evidence:** `cryo/engines/native/routing.py` — `NATIVE_DELETE_FILES_DETAIL` gates native OPTIMIZE; Spark fallback required (`native_admit.py`).

**Fix:** Ensure Spark fallback in engine pool for MOR tables; monitor audit for `optimize` vs expire-only runs on bronze.

---

### G8. ~~S3 request-cost ingest mostly off~~ — corrected: already enabled on all real prod buckets

**Corrected 2026-08-18** — this claim is **wrong as of now**, not just stale. Verified live via direct DB query (`S3BucketLogConfig.bucket, .enabled, .logging_status`):

```
pw-data-cdp-prod-bronze-layer        enabled=True  logging_status=enabled
pw-data-cdp-prod-bronze-layer-audit  enabled=True  logging_status=enabled
pw-data-cdp-prod-silver-layer        enabled=True  logging_status=enabled
pw-data-cdp-prod-silver-layer-audit  enabled=True  logging_status=enabled
pw-data-cdp-prod-gold-layer          enabled=True  logging_status=enabled
pw-data-cdp-prod-gold-layer-audit    enabled=True  logging_status=enabled
pw-data-cdp-prod-temp                enabled=True  logging_status=enabled
```

All seven real prod CDP buckets are enabled with `logging_status=enabled`, not just `pw-data-cdp-prod-temp` as originally claimed (that claim traces to `docs/s3-request-cost-tracking.md`'s own "known issues" section, which is itself stale — this was fixed after that doc was written). The table does have duplicate rows per bucket (some `enabled=False, logging_status=unknown` — likely leftover dev-source or pre-fix rows), which is worth a cleanup but is not the coverage gap the original claim described.

**No action needed on ingest coverage.** If per-table GET/PUT cost attribution still looks incomplete in practice, the next real question is whether the *ingest pipeline* (not the enable flag) is actually populating `S3RequestCostDaily` correctly — that's a different, unverified claim and would need its own investigation, not assumed from the enable flags being off.

---

### G9. Cost ceiling — corrected: likely *never* fires, not "trips often"

**Original claim (wrong direction):** $100/day pause stops maintenance often since trailing spend (~$210/day) is well over the ceiling.

**Corrected 2026-08-18, live evidence:** `fetch_todays_namespace_cost()` (`cryo/reporting/cost_ceiling.py`) queries Athena CUR for **`current_date` ("today")**. AWS CUR data lags 24–48h — the function's own docstring already documents this ("querying 'today' can legitimately return zero/partial rows before AWS has finished billing the day... not treated as an error"). Live scheduler logs confirm the practical consequence:

```
Cost ceiling check: cryo namespace $0.00 today (used $0.00 + unused $0.00) vs $100.00 ceiling
```

— repeated on every check, because "today" never has committed CUR rows by the time the check runs. **The ceiling is almost certainly never tripping in practice**, not "tripping often" as originally claimed — it's comparing $100 against a number that structurally reads $0 until the day is already over, at which point a new "today" starts. Independently confirmed the real trailing spend is genuinely ~$209–212/day (7-day Grafana pull, 2026-08-11 to 08-17: $388.23 used + $1,077.32 unused = $209.36/day avg) — well over $100, yet the ceiling has not shown a "breached" log line.

This is a more serious finding than the original: the safety mechanism intended to cap Cryo's own spend is very likely non-functional by construction, not merely miscalibrated.

**Fix:** Check yesterday's (fully-billed) CUR day instead of today's, or maintain a rolling trailing-N-day average and compare that against the ceiling — either avoids ever comparing against a day that structurally can't have data yet. → **PDAE-8541**

---

### G10. Property recommendations were unapplied for years

**Claim:** Metadata buildup on streaming tables until autopilot Aug 2026.

**Evidence:** `docs/open-items-2026-08-10.md` §0 — recommendations generated, zero consumers until `cryo/scanner/property_autopilot.py` (`a6f1759`); autopilot ON in prod Aug 10.

**Fix:** Monitor autopilot audit actor; extend to sort-order only via dedicated compaction (not autopilot — per open-items).

---

### G11. ROI gate in shadow — does not skip low-value jobs yet

**Evidence:** `cryo/config.py` — `roi_gate_shadow_mode` default `true`; `values-prod.yaml` enables gate but not shadow override (shadow remains).

**Fix:** Review shadow logs; enable enforcement after BCR thresholds validated.

---

### G12. Intelligent-Tiering is not a fast storage cut

**Claim:** IT on bronze did not materially move Aug storage $.

**Evidence:** `docs/s3-cost-investigation-2026-08.md` §2.2 — only **~$43/30d** in true IA tier vs ~$4.6k storage on bronze; gold had **zero** `INT-*` usage types in 60d CUR.

**Fix:** Enable IT on gold (zero-risk, slow); **do not** treat as substitute for expire+orphan. AWS: [S3 Intelligent-Tiering](https://aws.amazon.com/s3/cost-optimization/).

---

### G13. ~17 TB duplicate/temp naming patterns — outside maintenance path

**Evidence:** `docs/TABLE_DEDUPLICATION_DESIGN.md` — 557 tables, **17.97 TB** matching `_temp`/`_backup` patterns; design doc, not shipped automation.

**Fix:** Dedup campaign or lifecycle on known prefixes; Cryo won't touch unless tables assigned.

---

### G14. `pw-data-cdp-prod-temp` bucket — 27 TB non-Iceberg waste

**Evidence:** CloudWatch ~27 TB steady; mostly storage (86% of bucket CUR); not fixed by table maintenance.

**Fix:** S3 Lifecycle on bucket/prefix (platform); independent of Cryo.

---

## 5. Root-cause model

```
                    ┌─────────────────────────────────────┐
                    │  Legitimate data growth (medallion) │
                    │  ~316 TB × ~$25/TB-mo ≈ $8k/mo floor│
                    └─────────────────┬───────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
  Snapshot-pinned              Orphan files                 Small files
  superseded files             (failed writes)              (GET/LIST API $)
        │                             │                             │
        └──────────────┬──────────────┴──────────────┬──────────────┘
                       ▼                             ▼
              expire_snapshots              optimize (DEEP/STANDARD)
                       │                             │
                       └──────────► remove_orphan_files (DEEP only)
                       │
                       ▼
              Only if table has effective preset + recurring schedule
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
   ~1,267+ unmatched            Hot tables (gold_dbt_pw_bq)
   needs_compaction              outpace weekly cron
```

---

## 6. Prioritized fix backlog (Cryo repo + ops)

| P | Item | Owner | Est. impact | Effort |
|---|------|-------|-------------|--------|
| **P0** | `gold_dbt_pw_bq` still grade F / 189,844 files / 382 snapshots / 24.5TB despite an enabled but cron-less preset — needs real recurring cadence, not sporadic manual re-triggers (G3) | Cryo + DE | High S3 storage + API on gold/bronze | Medium — **PDAE-8542** |
| **P0** | Decide `default-native-deep`'s role: enable as fleet catchall or retire — it's fully configured (cron, mode, ops) but sits `enabled: false` (G4, corrected) | Ops | Fleet-wide maintenance | Low — **PDAE-8540** |
| **P0** | Prometheus alert: `cryo_scheduler_unmatched_tables{needs_compaction="true"}` | Cryo | Prevents silent coverage gaps | Low — **PDAE-8543** |
| **P0** | Cost ceiling checks "today's" CUR data, which structurally reads $0.00 due to 24–48h billing lag — the $100/day safety breaker likely never fires (G9, corrected) | Cryo | Restores a real spend safety net | Low — **PDAE-8541** |
| **P1** | Implement **scan-cost × fragmentation** scheduling priority (Trino report §9) | Cryo | Trino used $ + targeted S3 API | Medium |
| ~~P1~~ | ~~Enable S3 log ingest: bronze, gold, silver buckets~~ — **already enabled on all 7 real prod buckets, verified live (G8, corrected)** | — | — | Done, no action |
| **P1** | Jira: scheduled retention for `gold_dbt_pw_bq` (s3-cost-investigation §4) — same underlying gap as the P0 item above | DE | ~$800–1,400/bucket Aug projection | Medium |
| **P2** | ROI gate: exit shadow after log review | Cryo | Less wasted Cryo compute | Low |
| **P2** | `cost_full_scan` on top 50 TB tables | Cryo | Better orphan projections | Low |
| **P2** | Intelligent-Tiering on gold bucket | Platform | Slow storage $ | Low |
| **P3** | TABLE_DEDUPLICATION design → execution | DE | Up to ~17 TB class | High |
| **P3** | Temp bucket lifecycle | Platform | ~$500/mo class | Low |
| **P3** | AWS Glue table optimizers as backstop for uncovered tables | Platform | Coverage | Medium |

**Not Cryo fixes (Trino report — separate track):**

- Coordinator/gateway **request** trims (~$175–180/week safe) — `values-files/data-platform-cluster/`
- Worker memory p95 at 80–87% of request — **no safe worker bin-packing** (Trino report §10 retraction)
- Resource groups disabled — structural Trino admission control

---

## 7. Verification checklist (run after fixes)

```bash
# 1. Unmatched tables (Prometheus or Cryo API)
#    cryo_scheduler_unmatched_tables{needs_compaction="true"}  → trending down

# 2. Prod preset
#    SELECT name, schedule_cron, mode, operations, enabled
#    FROM cryo.presets WHERE name = 'default-native-deep';

# 3. Recent DEEP runs on hot tables (Cryo audit / UI)
#    - expire_snapshots + remove_orphan_files both present
#    - actual_bytes_freed > 0 on orphan steps

# 4. gold_dbt_pw_bq stats (scanner or Trino $snapshots / $files)
#    - total_snapshots stable/down over 7d (not re-climbing to 600+)
#    - file count not re-growing to 600k+ without compaction cadence

# 5. S3 CUR (Grafana cost-db, per bucket)
#    - gold TimedStorage-ByteHrs daily flat/down vs §2.3 before-state
#    - bronze API Tier-1/Tier-2 on flagged tables down after maintenance

# 6. Cryo property autopilot
#    - audit actor cryo-property-autopilot on metadata properties

# 7. Trino used cost (optional — mechanism check)
#    - per-table cpu_time_millis / total_bytes before/after compaction
#      on tables Cryo compacted (Trino report §0 next step)
```

---

## 8. Cross-link: Trino cost report — what applies to Cryo

Source: `~/deployment_repo/TRINO_COST_OPTIMIZATION_REPORT.md` (2026-08-17).

| Trino report section | Relevance to Cryo / S3 |
|---------------------|------------------------|
| §0 Addendum | Cryo **did** run ~9.5k succeeded maintenance jobs Aug 3–8; fleet health improved; Trino **used** cost correlated in time — supports compaction value, not S3 storage directly |
| §0 caution | Unused cost drop Aug 8–10 **not** Cryo; don't merge narratives |
| §9 bottom line | **"Prioritize Cryo compaction by Trino scan cost, not just health grade"** — direct Cryo product recommendation; implement in scheduler priority |
| §10 | Worker bin-packing **retracted** — Trino infra sizing separate from Cryo |
| §5 kill DAGs | Query-level control already strong — complements but does not replace Iceberg maintenance |
| Cost proportional to **scanned bytes / CPU-seconds**, not wall clock (§10) | Validates scan-cost-based Cryo scheduling thesis |

**Cryo does not control:** Trino Helm values, KEDA min/max replicas, gateway CPU requests, spot vs on-demand node placement.

**Cryo does control:** Which Iceberg tables get expire/orphan/compact, how often, and with which engine — that drives **S3 object count and snapshot-retained bytes**, and secondarily Trino scan cost.

---

## 9. External references (frameworks & official docs)

| Topic | Link |
|-------|------|
| Iceberg maintenance (expire, orphan, compact) | https://iceberg.apache.org/docs/latest/maintenance/ |
| Iceberg Spark procedures | https://iceberg.apache.org/docs/latest/spark-procedures/ |
| AWS Iceberg storage optimization | https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/best-practices-storage.html |
| Athena VACUUM (compute-free snapshot+orphan) | https://docs.aws.amazon.com/athena/latest/ug/vacuum-statement.html |
| AWS Glue table optimizers | https://docs.aws.amazon.com/glue/latest/dg/table-optimizers.html |
| S3 Intelligent-Tiering | https://aws.amazon.com/s3/cost-optimization/ |
| Yelp −33% storage (usage-driven retention) | https://engineeringblog.yelp.com/2026/05/partition-access-visualizations.html |
| LakeOps — Iceberg S3 cost sequence | https://lakeops.dev/blog/reduce-aws-s3-cost-iceberg |
| Data lake 3 levers (compact → lifecycle) | https://algoscale.com/go/blog/data-lake-cost-optimization/ |

---

## 12. Cryo namespace (`cryo`) — high unused EKS cost

**Namespace:** `cryo` on cluster `data-platform-cluster` (not `trino-db`).  
**Grafana:** Namespace Cost dashboard uid `de05f8e7cjpj4ea` — panels “Namespace Total Cost” / “Unused Cost” ([`cryo/reporting/cost_ceiling.py`](../cryo/reporting/cost_ceiling.py) reverse-engineered this query shape).  
**CUR datasource:** Athena uid `fe0bimj2kv9xca`, table `cid_cur.cur` — columns `split_line_item_split_cost` (used) and `split_line_item_unused_cost` (unused).

### 12.1 What “unused cost” means (not a Grafana bug)

AWS EKS split-cost billing allocates each pod’s cost into:

- **Used** — proportional to actual CPU/memory consumption vs request
- **Unused** — the remainder of **reserved** capacity (Kubernetes `requests`) that the process did not consume

High unused % means **pods are over-requested relative to actual usage**, or **many pods run briefly with full requests** while doing little work. This is the same mechanism described for `trino-db` in `~/deployment_repo/TRINO_COST_OPTIMIZATION_REPORT.md` §1–2. It is **real AWS spend**, not a display error.

### 12.2 Grafana numbers pulled 2026-08-18

**7-day total** (`resource_tags_aws_eks_namespace = 'cryo'`, days before `current_date`):

| Metric | USD |
|--------|-----|
| Used | **$394** |
| Unused | **$1,080** |
| **Total** | **$1,484** |
| **Unused %** | **72.8%** |

**~$212/day** average — aligns with `cryo/reporting/cost_ceiling.py` note (~$210.82/day trailing 14d when cost ceiling was added Aug 12).

**By deployment** (7d):

| Deployment | Used | Unused | Total | Unused % |
|------------|------|--------|-------|----------|
| **(untagged — ephemeral Jobs)** | $374 | **$1,043** | **$1,417** | **73.6%** |
| `cryo-worker` | $10 | $21 | $31 | 68% |
| `cryo-scanner-worker` | $7 | $11 | $17 | 61% |
| `cryo-api` | $2 | $4 | $6 | 68% |
| `cryo-governor` | $1 | $2 | $3 | 53% |
| `cryo-web` | $1 | $1 | $2 | 61% |
| `cryo-scheduler` | $0.4 | $1.0 | $1.4 | 69% |
| `cryo-redis` | $0.5 | $0.3 | $0.8 | 35% |

**~95% of namespace dollars** sit in the **untagged** row — not in long-running Deployments. Tagged control-plane pods are a **small** slice (~$58/week total) but still ~**65% unused** among themselves.

**14-day daily trend** (namespace total):

| Day | Used | Unused | Unused % |
|-----|------|--------|----------|
| 2026-08-05 | $44 | $188 | 81% |
| 2026-08-06 | $87 | $294 | 77% |
| 2026-08-11 | **$305** | **$752** | 71% |
| 2026-08-15 | $23 | $163 | 88% |
| 2026-08-17 | $9 | $21 | 70% |

Spikes line up with **large maintenance bursts** (Aug 3–4 Cryo job wave per §3.1; CUR may lag 1–2 days — Aug 11 is the largest single day in this window).

### 12.3 Root causes (verified in repo — not mysterious billing bugs)

#### U1. Ephemeral Spark / native maintenance Jobs dominate spend and lack deployment tags

**Evidence:** CUR groups untagged workload as `resource_tags_aws_eks_deployment = ''`. [`deploy/helm/cryo/values-prod.yaml`](../deploy/helm/cryo/values-prod.yaml) lines 320–328 document explicitly:

> *Ephemeral Jobs don't carry `eks:workload-name` tags, so this cost was invisible in the normal per-Deployment cost breakdown … dominant driver of cryo namespace's `unused_cost` (87.9% of a $1,412/30d untagged bucket).*

Each maintenance table dispatch creates a **short-lived K8s Job** (native Go engine or Spark fallback via [`cryo/engines/spark/k8s.py`](../cryo/engines/spark/k8s.py)). AWS bills the **full requested** driver + executor CPUs/memory for the Job lifetime, even when Spark only uses ~5–15% of what was requested.

**Aug 2026 Spark request shape** (after partial right-sizing Aug 6):

- Driver: 500m CPU, 1024 MiB
- Executors: 2 × (1000m CPU, 2048 MiB)
- **Per Job reserved:** ~2.5 CPU, ~5 GiB memory before any work runs

During fleet bursts (e.g. **8,804 succeeded jobs on Aug 4**), hundreds or thousands of Jobs can overlap → many nodes, mostly **idle CPU/memory inside each Job’s request envelope** → **high unused %**.

**Not a code bug** in CUR/Grafana — **expected** when burst parallelism × per-Job requests ≫ actual utilization. It **is** an architecture/capacity design issue.

#### U2. Spark requests were historically much larger (fixed partially Aug 6)

Same `values-prod.yaml` comment: prior defaults were driver **1000m / 2048 MiB**, executor **2000m / 4096 MiB** (~6–15% utilization observed). Halving requests reduced waste but **did not eliminate** unused cost on Jobs.

#### U3. `cryo-scanner-worker` — provisioned for scan bursts, idle between bursts

**Evidence:**

- [`deploy/helm/cryo/templates/scanner-worker-hpa.yaml`](../deploy/helm/cryo/templates/scanner-worker-hpa.yaml) lines 1–7: fixed **8 replicas** historically caused **~73% unused** on the cost dashboard (2026-08-12).
- [`values-prod.yaml`](../deploy/helm/cryo/values-prod.yaml): HPA enabled `minReplicas: 3`, `maxReplicas: 8`, but file still sets `replicas: 8` as legacy comment/floor — when HPA is on, Deployment omits static `replicas` and HPA manages count.
- Pods poll `cryo:scan:pending` every 5s; queue **empty most of the time** between fleet scans and ad-hoc UI triggers.

**Aug 16 mitigation:** memory requests cut 512Mi → 256Mi (observed ~103 MiB actual). **Confirmed 2026-08-18, live:** HPA is active and currently sitting at 3/8 replicas (`kubectl get hpa -n cryo` → `cpu: 0%/70%, memory: 40%/80%, 3 3`) — the "remaining" verification this section originally called for is done and passes. Still worth doing: **KEDA redis scaler** on queue depth (noted as follow-up in HPA template comments) for faster reaction than CPU/memory-based HPA.

#### U4. Long-running Deployments were over-requested until Aug 16

| Component | Old request (chart default) | Observed usage (Aug 16 notes) | New request |
|-----------|----------------------------|-------------------------------|-------------|
| `cryo-api` | 768 MiB | ~136 MiB | 256 MiB |
| `cryo-governor` | 1 GiB | ~178 MiB | 384 MiB |
| `cryo-scanner-worker` | 512 MiB | ~103 MiB | 256 MiB |
| `cryo-web` | 128 MiB | 7–24 MiB | 64 MiB |

**Evidence:** inline comments in `values-prod.yaml` lines 192–299 citing **~82–83% unused memory** per deployment on the cryo-namespace dashboard before cuts.

`cryo-worker` (2 replicas, 500m / 1 GiB request) still shows **~68% unused** in CUR — also runs concurrent Spark K8s dispatch paths ([`native_admit.py`](../cryo/governor/native_admit.py) — up to `_DEFAULT_FALLBACK_DISPATCH_CONCURRENCY=6`); Aug 16 raised worker **limit** to 6Gi after OOM bursts without lowering requests.

`cryo-scheduler` (512 MiB request, 6 Gi limit): runs benefit-score updater, S3 log ingest, telemetry daemons — [`values.yaml`](../deploy/helm/cryo/values.yaml) documents OOM history on 2Gi/4Gi; request may still exceed steady RSS (**~69% unused** in 7d CUR).

#### U5. Cost ceiling — corrected: structurally reads $0, very likely never fires

**Corrected 2026-08-18** — see §4 G9 for the full evidence; this restates it in namespace-cost terms. [`CRYO_COST_CEILING_ENABLED`](../deploy/helm/cryo/values-prod.yaml) = $100/day on **used + unused** cryo namespace spend, checked against **`current_date`**. Live scheduler logs show `$0.00 today` on every check, because AWS CUR data lags 24–48h behind real time and `fetch_todays_namespace_cost()` queries "today" specifically — a day that structurally has no committed CUR rows yet. Trailing real average is genuinely **~$209–212/day** (confirmed via fresh Grafana pull), well over the $100 ceiling, yet no "Cost ceiling breached" log line was found. The original claim (ceiling "trips often") has the direction backwards — the more likely reality is the safety breaker **never trips**, silently. → **PDAE-8541**

#### U6. Not causes (ruled out)

| Hypothesis | Verdict |
|------------|---------|
| Grafana mis-labeling `cryo` vs `trino-db` | Queries filter `resource_tags_aws_eks_namespace = 'cryo'` explicitly |
| Double-counting HA Prometheus | Trino report had Mimir dedup issues; CUR split cost is billing-side, not Prometheus |
| Redis / web driving most waste | `<$2/week each in 7d breakdown` |
| S3 storage leaking into namespace cost | EKS split cost only — S3 TimedStorage is separate CUR product code |

### 12.4 Relationship to S3 storage goals (§4)

High Cryo **unused EKS % does not mean Cryo fails to delete S3 bytes** — but it means **maintenance is expensive to run**:

- Aug bursts that improved fleet health (§3.1) **coincided with ~$1k+/day namespace spikes** (unused-heavy).
- ROI gate is enabled but **shadow mode** (`roi_gate_shadow_mode` default `true` in [`cryo/config.py`](../cryo/config.py)) — does not yet skip low-value jobs that add unused Job capacity.

Optimizing S3 storage **requires** running maintenance; optimizing Cryo **requires** cheaper maintenance execution — same product, two cost lines.

### 12.5 Fix backlog — Cryo namespace unused cost

| P | Action | Est. impact | Status |
|---|--------|-------------|--------|
| **P0** | **Tag native + Spark Jobs** (`eks:deployment` or custom cost tag) so CUR/Grafana split untagged bucket | Visibility; ops can tune per engine | Not shipped — **PDAE-8544** |
| ~~P0~~ | ~~Verify scanner-worker HPA at min 3 replicas when queue empty~~ — **confirmed live: HPA active, currently at 3/8 replicas, working correctly** | — | Done, no action |
| **P0** | Cost ceiling structurally reads $0.00/day (24–48h CUR lag vs "today" query) — restore a working spend safety net | Prevents runaway namespace spend going undetected | **PDAE-8541** (same root cause as G9) |
| **P1** | **KEDA scaler** on `cryo:scan:pending` depth (template already notes follow-up) | Faster scale-down when idle | Designed, not enabled |
| **P1** | Further **Spark Job sizing** per operation type (`optimize_manifests` vs `expire` only) | Reduce per-Job unused on untagged row | Partial (Aug 6) |
| **P1** | Cap **parallel Spark fallback** concurrency per table / fleet during bursts | Fewer overlapping 5GiB-request Jobs | Tune `native_admit.py` |
| **P2** | **ROI gate**: exit shadow mode after BCR log review (§G11) | Skip jobs where compute > reclaim value | Shadow on |
| **P2** | **Native KEDA** (`native.keda.enabled`) — scale maintenance Jobs to zero between waves | Reduce idle Job capacity | Enabled in prod; validate utilization |
| **P3** | Right-size `cryo-worker` **requests** after dispatch concurrency tuned | Lower steady Deployment unused | Open |
| **P3** | Scheduler: fix benefit-score updater memory (OOM noted in `s3-request-cost-tracking.md`) | Stable scheduler; safer scale-down | Open |

### 12.6 Verification commands

```bash
# Grafana Explore → Athena fe0bimj2kv9xca — same SQL as §12.2

# Live pod requests vs usage (confirm Aug 16 cuts landed)
kubectl top pods -n cryo --containers
kubectl get hpa -n cryo

# Scanner queue depth vs scanner-worker replicas
# Redis LLEN cryo:scan:pending  vs  kubectl get deploy cryo-scanner-worker -n cryo

# Cost ceiling state
# MaintenanceControlState / scheduler logs: "Cost ceiling check: cryo namespace $..."
```

---

## 10. Related tickets & paths

| Item | Reference |
|------|-----------|
| Gold deep maintenance pilot | PDAE-8269 (cited in s3-cost-investigation) |
| Hot-table Wave 2 | PDAE-8276 |
| Cryo skill (Grafana + Slack daily refresh) | `~/.cursor/skills/cdp-s3-trino-cost/SKILL.md` |
| CDP cost HTML dashboard | `~/powerbi_issues/reports/cdp_s3_trino_cost_dashboard.html` |
| Trino values files | `~/deployment_repo/values-files/data-platform-cluster/` |
| Cryo prod Helm | `deploy/helm/cryo/values-prod.yaml` |
| Operator runbook | `docs/operator-guide.md` |

---

## 11. Document history

| Date | Change |
|------|--------|
| 2026-08-18 | Initial research doc; cross-checked against Cryo codebase, s3-cost-investigation, open-items, TRINO_COST_OPTIMIZATION_REPORT.md |
| 2026-08-18 | **§12** — Grafana CUR pull for `cryo` namespace unused cost (~73%), root-cause analysis (untagged Jobs, scanner HPA, request oversizing) |

**Next update triggers:** After preset rule expansion deploy; after 7d audit of `gold_dbt_pw_bq` snapshots; after gold TimedStorage CUR flattening post-retention cadence.
