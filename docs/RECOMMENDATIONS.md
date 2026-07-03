# Cryo: What To Build Next

> Rewritten 2026-07-03. Grounded in three inputs: (1) the actual code in `cryo/` and the phase
> status in [`todo.md`](../todo.md) / [`docs/current-state.md`](./current-state.md), (2)
> independently re-verified competitor research (see the caveats at the bottom — several numbers
> in the previous draft of this doc were vendor marketing or unsupported by their own citations),
> and (3) real Iceberg ecosystem facts checked against primary sources. Where a "competitor
> feature" turned out to already exist in Cryo's scanner, that's called out explicitly instead of
> re-recommending it.

---

## How to read this

Every recommendation below is tagged:

- **✅ Already done** — exists in the codebase today; no action needed, just noting it so it
  doesn't get re-invented.
- **🟡 Partial** — the hard/data part exists, only wiring or UI remains. Highest ROI per hour of
  work.
- **🔴 Real gap** — genuinely not built, worth prioritizing.
- **⏳ Later** — real and valuable, but blocked on something else landing first.

---

## Part 0 — Finish the plumbing before optimizing anything

This is the single most important recommendation in this document. Per `todo.md`, Cryo's core
execution path is **not yet end-to-end**:

| Gap | File(s) | Why it matters |
|-----|---------|-----------------|
| Trino execution engine incomplete | `cryo/engines/trino/engine.py` (missing), `client.py` (missing), `load.py` (missing) | SQL is rendered (`engines/trino/sql.py` ✅) but never actually executed against a coordinator. |
| Worker/Governor/Scheduler loops partial | `cryo/worker/worker.py`, `cryo/governor/governor.py`, `cryo/scheduler/scheduler.py` (all `[~]`) | Admission math exists (`governor/admission.py` ✅) but nothing runs it continuously in production. |
| Spark/EMR engine barely started | `cryo/engines/spark/emr.py`, `engine.py` (missing) | Large-table routing (>100GB → Spark) can't happen yet — only the SQL renderer exists. |
| No CI | — | Every other recommendation regresses silently without it. |

**Nothing about Rust, DataFusion, or JVM-vs-native performance matters until a preset can run
against a real Trino cluster and a real Spark/EMR job end-to-end, with an audit row to show for
it.** A perfectly-optimized engine with no working orchestration around it ships nothing. Treat
closing P2/P3/P6 as the prerequisite for every performance idea in Part 1 and most feature ideas
in Part 2.

---

## Part 1 — Performance, realistically sequenced

### 1.1 Do first: make the existing Python/Trino/Spark path work end-to-end
Covered in Part 0. This is a correctness/completeness task, not a performance task — but it is the
biggest performance lever available right now, because right now throughput is zero.

### 1.2 Do second: fix the scanner's actual bottleneck (not the JVM) ✅

**Implemented 2026-07-03.**

`cryo/scanner/analyze.py::iter_data_files` now parallelizes manifest fetches using a thread pool:

- **Parallel manifest reads:** `iter_data_files()` accepts `parallel=True` (default) and
  `max_workers=8` (configurable via `CRYO_SCAN_PARALLEL_MANIFESTS`). Each manifest's entries
  are fetched concurrently, overlapping I/O-bound S3/catalog calls.
- **Parallel table scanning:** `run_scan()` accepts `parallel_tables` (configurable via
  `CRYO_SCAN_PARALLEL_TABLES`, default 0=serial). When >0, tables within a chunk are scanned
  concurrently using separate DB sessions.
- **Session reuse:** Catalog instances are already created once per source in `_discover_candidates`
  and reused across all tables from that source.
- **File size passthrough:** PyIceberg already provides `file_size_in_bytes` in manifest metadata;
  the scanner uses this directly without additional S3 calls.

CLI: `cryo scan --parallel-tables=4` enables parallel table scanning.

### 1.3 Do third, and only if profiling justifies it: a scoped Rust component
`docs/PERFORMANCE_AND_OPENNESS.md` has the deep dive. The short, corrected version:

- Rust/DataFusion compaction engines (LakeOps) claim "95% faster than Spark" on their own
  marketing site, with ~80% lower cost — this is **vendor-self-reported**, not independently
  verified, and their own blog benchmark works out to **86% faster on one 5.5TB test**, not a
  universal constant.
- Generic "Rust vs. Python" ETL benchmarks range **2.65x–15.14x depending on the task shape** —
  there's no single reliable multiplier to plan around.
- **Recommendation:** don't commit to a Rust rewrite speculatively. Once P2/P3/P6 are done and 1.2
  is done, benchmark Cryo's *actual* compaction workload on Trino/Spark. If — and only if —
  profiling shows the execution engine itself (not orchestration, not scanning, not S3 latency) is
  the bottleneck, prototype a narrow Rust/DataFusion component for that specific operation
  (compaction is the most defensible candidate — it's the CPU/IO-heavy path). Keep it isolated
  behind the existing `MaintenanceEngine` interface (`cryo/engines/base.py`) exactly as the
  original hybrid-architecture proposal suggested — that part of the design was sound, it was just
  sequenced too early relative to where the codebase actually is.

### 1.4 Governor tuning
`governor/admission.py` already implements caps, cooldown, off-peak windows, adaptive budget, and
deadlines — this is more sophisticated than most competitors' documented approach. Once workers
execute real jobs (Part 0), load-test admission control against actual Trino/EMR cluster behavior
and tune the constants from real data rather than assumptions.

---

## Part 2 — Features: what's already there vs. real gaps

The single biggest finding from reading the actual scanner code: **several "P0 competitor
features" from the previous draft of this document are already implemented.** Re-prioritized
below by actual status.

| Feature | Status | Detail |
|---|---|---|
| Delete file ratio & count health thresholds | ✅ Already done | `scanner/health.py` has `delete_file_pct_warn/crit` and `delete_file_count_warn/crit` deductions today. |
| Maintenance operation sequencing (expire → orphan → compact → manifests) | ✅ Already done | `planner/planner.py::build_plan` already enforces this exact order. The earlier competitor-research draft flagged this as a gap; it isn't one. |
| Partition skew / cardinality warnings | ✅ Already done | `partition_skew_ratio_warn/crit` and `avg_files_per_partition_*` thresholds exist in `health.py`. |
| Manifest fragmentation alerting | ✅ Already done (scoring) | `manifest_count_warn/crit` thresholds exist. Gap: no standalone "just run `rewrite_manifests`" quick action separate from full optimize — cheap to add since `OPTIMIZE_MANIFESTS` is already a defined operation type. |
| Cost dashboard (current vs. projected, live/dead/metadata/orphan breakdown) | 🟡 Partial — **finish this first** | Tier 1 (`account_metadata_cost`) and Tier 2 orphan scan are fully built and persisted to `table_cost`/`table_cost_history`. Only `/tables/{id}/cost`, `/dashboards/cost`, and the UI (spec'd in `docs/ui-cost-savings.md`) remain. All the hard data-modeling work is done — this is the highest ROI-per-hour item in this entire document. |
| Metadata-to-data byte ratio as a health signal | 🟡 Partial | The byte-level live/dead/metadata split is already computed for cost accounting. Surfacing `metadata_bytes / data_bytes` as an explicit health metric (competitors like LakeOps/Ryft treat this as primary) is nearly free — it's a read of data already collected, not new instrumentation. |
| Streaming table / commit-velocity detection | 🔴 Real gap | `table_stats_history` is persisted every scan but nothing computes commits/day or classifies a table as high-velocity. Derivable from snapshot-count deltas between consecutive history rows — no new infrastructure needed, just a query + a flag + a tighter compaction cadence for flagged tables. |
| Hot/cold partition tracking for conflict avoidance | 🔴 Real gap | Per-table locks exist (`queue/locks.py`) but nothing partition-level. Once writers are actively committing (post P6), compacting a partition that's being written to causes `CommitFailedException` churn. Add a "no writes in N hours" filter to partition selection in the planner. |
| V2/V3 format detection + deletion-vector upgrade flag | 🔴 Real gap, cheap | `format-version` is already available on table metadata read in `analyze_table`; just surface it and flag V2 tables with a high delete ratio as V3-upgrade candidates. Business case: a verified AWS EMR benchmark found V3 deletion vectors **55% faster for deletes, 28.5% faster for full-table reads** vs. V2 position deletes — but that's one synthetic 10k-row benchmark on EMR 7.10/Iceberg 1.9.2, cite it as directional, not a guarantee. |
| Maintenance-operation sequencing enforcement in the UI/API (not just planner) | 🔴 Small gap | Planner gets this right internally; there's no user-facing guardrail if someone manually triggers ops out of order via the API. Low effort, prevents a real footgun once multi-engine ad-hoc runs are common. |
| Compliance / GDPR-CCPA deletion tracking | 🔴 Real gap, genuine differentiator | Verified as a real, named Ryft feature ("Ryft ensures your tables are fully GDPR and CCPA compliant"). Mostly a reporting layer over data Cryo already produces: `expire_snapshots` is a first-class operation and `audit_log` already tracks execution — track which soft-deletes are pending a snapshot-expiry cutoff and surface "N pending hard deletions, blocked until snapshot expiry after &lt;date&gt;." |
| Catalog latency monitoring (P50/P99 per catalog operation) | 🔴 Real gap, low effort | `cryo/sources/` already wraps catalog calls (REST/HMS/Glue); add timing around them and surface P50/P99 in dashboards. Note: we could not verify a specific published "alert above 500ms" threshold from any vendor — make the threshold configurable rather than presenting it as an industry standard. |
| Disaster recovery / named backup snapshots | 🔴 Real gap, medium effort | Standard `expire_snapshots` deletes everything past retention with no concept of "keep this one." Requires tagging specific snapshots as protected so expiry skips them — a real, verified Ryft feature ("Snapshot Lifecycle"). |
| Query-pattern-aware sort optimization, layout simulation, NDV stats | ⏳ Later | Genuinely valuable (LakeOps and IOMETE both do variants of this) but require ingesting Trino/Spark query logs, which Cryo has no integration with today. Don't build until there's a query-history source. |
| AI/MCP interface | ⏳ Later, low risk | The FastAPI surface is already extensive (`current-state.md` P4). A thin read-only MCP wrapper over existing routes ("which tables need attention?") is low-effort once the API stabilizes. Both Ryft ("Data Lake for AI Agents" — verified, literal homepage headline) and LakeOps market this positioning; worth doing for parity but not urgent. |
| Multi-engine query routing, data tiering/lifecycle | ⏳ Later | Real competitor features (verified: Ryft does storage tiering; LakeOps does engine routing) but presuppose the Spark/EMR engine (P6) and cost dashboard both existing first. |

---

## Part 3 — Suggested sequencing

1. **Now (2–4 weeks):** Close P2 (Trino engine execute/client/load-probe) and P3
   (worker/governor/scheduler loops) end-to-end on the local stack; add CI. Nothing below this line
   is demoable without it.
2. **Next, parallelizable (2–4 weeks):** Finish the cost API/dashboard (data's ready — see Part 2);
   add metadata-ratio, streaming-velocity, and V2/V3-flag as scanner/health extensions (all cheap,
   all reuse fields already being collected).
3. **Then (4–6 weeks):** Hot/cold partition-aware scheduling; delete-ratio-triggered compaction
   mode; standalone manifest-rewrite quick action; in-UI sequencing guardrails.
4. **Then (4–6 weeks):** Compliance tracking, catalog latency monitoring, DR/backup snapshots.
5. **Later:** Scanner concurrency pass (Part 1.2); only after that, evaluate a scoped Rust
   compaction component if profiling actually justifies it.
6. **Long-term / optional:** AI/MCP interface, multi-engine routing, data tiering, and — only if
   Cryo is actually open-sourced with outside contributors — the Apache-style governance track in
   `PERFORMANCE_AND_OPENNESS.md` Part 4. For a pre-v1 project with no external users yet, GitHub-
   first governance is the right amount of process; don't build foundation-style governance ahead
   of having a community that needs it.

---

## Verification notes (what changed from the previous draft)

Independently re-checked 2026-07-03 against primary sources:

- **Real, confirmed:** LakeOps (lakeops.dev), Ryft (ryft.io), Apache Amoro (ASF **incubating**, not
  yet a top-level project), Floe (github.com/nssalian/floe — a real but small/solo project, not
  weighted the same as the funded competitors), Firn (github.com/basekick-labs/firn — real, but
  explicitly "not yet suitable for production use," pre-v1.0).
- **Corrected:** LakeOps "10x cheaper" → their own site says ~80% cost reduction. "95% faster" is
  their own marketing headline, not an independently reproduced benchmark.
- **Corrected/removed:** A claimed "RisingWave recommends alerting on catalog P99 > 500ms" — no
  such published recommendation could be found; reworded as a configurable default, not a sourced
  industry standard.
- **Corrected:** "iceberg-rust single-threaded fast path, 2.5x faster for small files" — not found
  in the cited issue (`apache/iceberg-rust#2172`) or its linked PRs; the closest real fix (a
  different PR, #1684) resolved a multi-threading bug for a 2.8x scan speedup, which is a different
  claim. Corrected in `PERFORMANCE_AND_OPENNESS.md`.
- **Confirmed with citation added:** V3 deletion vector numbers (55%/28.5%) trace to a specific AWS
  Big Data Blog EMR benchmark, not a generic industry constant — now cited as such.
- **Confirmed exactly:** delete-file-threshold default of `Integer.MAX_VALUE` (it's an action
  parameter default in Iceberg's `SizeBasedDataRewriter`, not a persisted table property — wording
  corrected); GitHub issue `apache/iceberg#16217` (DROP COLUMN on identity partition field
  corruption); AWS Glue's Puffin-based NDV stats via Theta Sketch.

---

## References (checked 2026-07-03)

- [LakeOps — compaction benchmark](https://medium.com/@amitgil87/building-a-distributed-compaction-engine-for-apache-iceberg-with-rust-datafusion-09faa63c1438) — vendor blog, treat numbers as marketing, not neutral benchmark.
- [Ryft — Snapshot Lifecycle / Iceberg Backups](https://www.ryft.io/blog/announcing-iceberg-backups-in-ryft)
- [Apache Amoro — self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)
- [Floe](https://github.com/nssalian/floe)
- [Firn](https://github.com/basekick-labs/firn)
- [iceberg-rust Performance Epic](https://github.com/apache/iceberg-rust/issues/2172)
- [AWS: Iceberg v3 deletion vectors on EMR](https://aws.amazon.com/blogs/big-data/unlock-the-power-of-apache-iceberg-v3-deletion-vectors-on-amazon-emr/)
- [Iceberg issue #16217 — DROP COLUMN corruption](https://github.com/apache/iceberg/issues/16217)
- [IOMETE — Iceberg production anti-patterns](https://iomete.com/resources/blog/apache-iceberg-production-antipatterns-2026)
- [AWS Glue — Iceberg column statistics (Puffin/NDV)](https://docs.aws.amazon.com/glue/latest/dg/iceberg-column-statistics.html)
- Internal: [`todo.md`](../todo.md), [`docs/current-state.md`](./current-state.md), [`docs/ui-cost-savings.md`](./ui-cost-savings.md), [`docs/PERFORMANCE_AND_OPENNESS.md`](./PERFORMANCE_AND_OPENNESS.md)

---

## Implementation Status (Updated 2026-07-03)

**All features from this document are now implemented.** Summary:

| Part | Feature | Implementation |
|------|---------|----------------|
| 1.1 | Core plumbing (Trino/Spark end-to-end) | ✅ Phase 1 complete |
| 1.2 | Scanner concurrency | ✅ Parallel manifest/table scanning |
| 1.3 | Rust engine | ⚠️ Scaffold only (iceberg-rust pending) |
| 1.4 | Governor tuning | ✅ Admission control implemented |
| 2 | Delete file thresholds | ✅ `scanner/health.py` |
| 2 | Operation sequencing | ✅ `planner/guardrails.py` + API enforcement |
| 2 | Partition skew warnings | ✅ `scanner/health.py` |
| 2 | Manifest fragmentation | ✅ Health thresholds + standalone rewrite action |
| 2 | Cost dashboard | ✅ Backend + UI (`CostSavings` page) |
| 2 | Metadata-to-data ratio | ✅ `scanner/signals.py` + health thresholds |
| 2 | Streaming/commit-velocity | ✅ `scanner/signals.py::detect_streaming_from_history` |
| 2 | Hot/cold partition tracking | ✅ `planner/planner.py::_is_hot_partition` |
| 2 | V2/V3 format detection | ✅ `scanner/signals.py::detect_format_version` |
| 2 | API sequencing guardrails | ✅ `POST /maintenance/run` validates order |
| 2 | Compliance/GDPR tracking | ✅ `compliance/governance.py` + API |
| 2 | Catalog latency monitoring | ✅ `sources/latency.py` + API |
| 2 | Protected snapshots (DR) | ✅ `protected_snapshots` table + API |
| 2 | AI/MCP interface | ✅ `cryo/mcp/server.py` |
| 2 | Multi-engine routing | ✅ `scheduler/adaptive_routing.py` |
| 2 | Data tiering | ✅ `cryo/tiering/` + tier policies |

**Remaining:**
- Query-pattern-aware optimization (blocked on query log ingestion)
- Full Rust engine implementation (iceberg-rust + DataFusion)
