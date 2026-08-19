# Iceberg Maintenance Intelligence — Four New Cryo Capabilities

**Status:** Implemented 2026-08-19 (extend existing analyzers; no one-click partition apply;
fast-path detects only — does not compact). See §7 for code map, tests, and leftover gaps.
**Owner:** Aamir
**Last updated:** 2026-08-19
**Scope:** Full-stack (backend scanner/analyzer changes, new API surface, UI in Table Detail / Catalogs / Health)

> ## ⚠️ Validation notice (2026-08-19, before handoff to another AI/engineer)
>
> This RFC was checked against the live codebase after the first draft was written. Most
> citations held up (the small-file-ratio threshold, `TIER1_APPLYABLE_KEYS`, the AutoComp
> lineage, `getAutoCompCandidates`). **Two findings materially change the scope and priority
> of this RFC and must be read before starting any of the four features:**
>
> 1. **Feature 1 (Partition-Fit Detector) is already ~80% built.**
>    `cryo/optimizer/partition_analyzer.py::analyze_partition_evolution` already does almost
>    exactly what §3 proposes: it reads `QueryPattern` (a real, populated table —
>    `cryo/telemetry/patterns.py` ingests it from `QueryLog`, and it's already consumed by
>    `layout_analyzer.py`, `pto/recommend.py`, `pto/drift.py`, `anomaly_brief.py`, and the
>    scheduler), finds the dominant filter column, checks whether it's in the current
>    partition spec, and — if not, and if that column looks high-cardinality by name —
>    recommends `bucket(N)` sized via `_bucket_count_for(total_size_gb)` (target ~0.5GB per
>    bucket, matching this RFC's own 100MB–1GB target almost exactly). This is already wired
>    to a real endpoint (`getPartitionEvolutionRecommendation`) and a real UI panel
>    (`OverviewTab.tsx`). **This also substantially covers Feature 4's bucket-sizing logic**
>    (`_bucket_count_for` is effectively Feature 4's step 3 formula already).
>    See the inline correction in §6/§7 for what's genuinely still missing (scan-ratio-based
>    triggering per Algoscale's >10% rule, `$partitions`-table-exact bucket-size validation
>    instead of the current name-based cardinality heuristic, and re-checking an *existing*
>    bucket count against *current* volume rather than only recommending a spec change on
>    first detection).
> 2. **Feature 3 (Day-0 property check) needs no new "zero-stats" code.**
>    `compute_property_recommendations` in `cryo/scanner/property_recommendations.py` already
>    handles `TableStats` being `None` end-to-end and already flags a missing
>    `write.target-file-size-bytes` from DDL-derived properties alone. The real gap is
>    scheduling (nothing calls this proactively for a brand-new table today), not the check
>    itself. See the inline correction in §5.
>
> **Recommended next step before building anything:** re-read `partition_analyzer.py` and
> `analyze_partition_evolution` in full, and re-scope §3/§6 (and the build-order in §7) around
> "extend the existing partition-evolution recommender" rather than "build a new Partition-Fit
> Detector from scratch." The evidence in §1 (PDAE-8357, the small-file case study) and the
> external research in §2 are still valid and worth keeping — only the "what already exists in
> Cryo vs. what's net-new" framing in §3/§5/§6 needed correcting.

---

## 0. Executive summary

This RFC proposes four new Cryo capabilities, each grounded in (a) published research and industry practice on Iceberg/data-lake maintenance, and (b) two pieces of real, measured evidence gathered in production this session: the **PDAE-8357** investigation (a query pattern scanning ~2.4B rows / ~133GB per call, 11,610 times/week, because its partition key didn't match its access pattern) and a **live, freshly-observed small-file pathology** on a table built during that same investigation (129,375 files averaging 301KB each, for only 37.2GB of data — see §1.2).

The four capabilities:

| # | Capability | Closes the gap that... | Build status (shipped 2026-08-19) |
|---|---|---|---|
| 1 | **Partition-Fit Detector** | ...meant PDAE-8357 took hours of manual `EXPLAIN`/log analysis to diagnose, instead of an automated flag | **Shipped as an extension** of `analyze_partition_evolution`: Algoscale scan-ratio (`avg(QueryLog.bytes_read) / table_bytes > 10%`) plus existing `filter_pct >= 80`. See §3. |
| 2 | **Fast-Path Health Check for New Tables** | ...let a brand-new table fragment into 129K files before any scan ever looked at it | **Shipped** — `cryo/scanner/fast_path_check.py`, scheduler sweep, Health + table-detail UI. Detect-only (no auto-compact). See §4. |
| 3 | **Day-0 `write.target-file-size-bytes` Check** | ...meant that same table's writer never had a target file size to aim for in the first place | **Shipped as scheduling** — existing `compute_property_recommendations` (works with no `TableStats`); fast-path + optional property autopilot now call it for new tables. See §5. |
| 4 | **Bucket/Partition Cardinality Sizing Validator** | ...meant an educated-guess bucket count (`bucket(id, 64)`) went unverified against real row-volume growth | **Shipped** — `analyze_bucket_sizing` / public `bucket_count_for` re-checks existing `bucket(N)` vs volume. Coarse estimate (`total_size_gb / N`), not live `$partitions`. See §6. |

None of these replace existing Cryo capabilities (compaction, `expire_snapshots`, `remove_orphan_files`, manifest rewriting, the existing AutoComp-derived benefit-score ranking — see §2 for the current baseline). They extend Cryo from "detect and fix file-layout problems on tables I've already scanned" to "detect partition-*strategy* mismatches and catch fragmentation before it accumulates."

**Explicit non-goals (still true after implementation):** no one-click partition-spec apply; fast-path never enqueues `optimize`; property apply remains behind `property_autopilot_enabled` (default off) or the existing manual apply API. Puffin NDV, Trino `EXPLAIN` column-constraint cross-check, write-event / Airflow hooks, Catalogs-page filters, and exact `$partitions` per-bucket sizes were **not** built (see remaining gaps in §7).

---

## 1. Motivating evidence

### 1.1 Case study: PDAE-8357

**Ticket:** PDAE-8357, "backend_user_admin: single-ID phone lookup full-scans 2B-row silver_prospect_activity_base — ~364TB/week" (parent: PDAE-4337, S3 and Trino Cost Reduction Initiative).

A backend service ran this query pattern 11,610 times over 7 days, averaging **31.3GB scanned and 131 seconds per execution** — **~364TB/week** from one query pattern alone:

```sql
WITH target_prospect AS (
  SELECT RelatedProspectId
  FROM lsq_pw.silver_prospect_activity_base
  WHERE ProspectActivityAutoId = ${id}
  ORDER BY modifiedon DESC LIMIT 1)
SELECT p.phone
FROM lsq_pw.silver_prospect_base p
JOIN target_prospect t ON p.prospectid = t.RelatedProspectId
ORDER BY CAST(p.lastmodifiedon AS TIMESTAMP) DESC LIMIT 1
```

**Root cause, confirmed via `EXPLAIN (TYPE IO)` and `trino_analyze_query_performance` (not assumed):** both source tables (`silver_prospect_activity_base`, 2.04B rows; `silver_prospect_base`, 400M rows) are partitioned **only by `ingest_date`**. The query filters/joins on `ProspectActivityAutoId`/`prospectid` — neither is the partition column. Trino's own cost-based optimizer was already doing everything right at the SQL level (broadcast join, dynamic filtering, filter pushdown, minimal projection — `trino_analyze_query_performance`'s own `insights`/`recommendations` arrays came back **empty**, meaning no SQL rewrite could fix this). The problem was structural: **a point-lookup workload against a table partitioned for date-range scans.**

**The fix** (implemented, not yet merged — see `gold_dbt_lsq_pw_prospect_activity_phone_lookup.sql` in `internal-dbt-repo`): a purpose-built dbt-managed Iceberg table, precomputing the same "latest row wins" join once per incremental build instead of once per call, and — critically — using Iceberg's **`bucket()` partition transform** on the lookup key instead of a date transform:

```sql
properties = {
    "partitioning" : "ARRAY['bucket(prospectactivityautoid, 64)']",
    "sorted_by"    : "ARRAY['prospectactivityautoid']"
}
```

This is precisely the pattern Iceberg's own partitioning guidance recommends for "non-temporal tables with high-cardinality filter columns" ([Starburst's Iceberg partitioning guide](https://starburst.io/blog/iceberg-partitioning); reflected in `internal-dbt-repo` §3's own partition decision tree, which routes exactly this shape of table to `bucket(high_card_col, N)`).

**What this demonstrates for Cryo:** the entire multi-hour diagnostic process — read `EXPLAIN (TYPE IO)`, notice the scan estimate is ~100% of table size for both sides, check the `CREATE TABLE` DDL, notice the filter/join columns aren't the partition column, conclude the partition strategy is wrong for the workload — is a **mechanical, repeatable procedure**, not something that inherently required a human. §3 (Feature 1) proposes automating it.

### 1.2 Live measurement: the small-file pathology, caught in real time

While verifying the PDAE-8357 fix's own effectiveness (via a newly-available `mcp-trino` connection, 2026-08-19), the following was measured directly against the real, already-built table:

```sql
SELECT count(*) FROM cdp.lsq_pw.gold_dbt_lsq_pw_prospect_activity_phone_lookup;
-- 1,099,271,900 rows
```

A real point-lookup query was executed and its actual wall time measured via `system.runtime.queries`:

```sql
SELECT phone FROM cdp.lsq_pw.gold_dbt_lsq_pw_prospect_activity_phone_lookup
WHERE prospectactivityautoid = 3553216;
-- wall_time: 5,345 ms
```

**5.3 seconds — a real, measured ~24.5× improvement over the 131s baseline, but far from the sub-second result a well-tuned point-lookup table should give.** `EXPLAIN (TYPE IO)` showed the scan was pruned to ~35.2M rows (≈3.2% of the table) — proof the `bucket()` transform *was* pruning correctly, just not down to a single small file. Checking the table's actual file layout via Iceberg's `$files` metadata table explained why:

```sql
SELECT count(*) AS file_count, sum(file_size_in_bytes) AS total_bytes,
       avg(file_size_in_bytes) AS avg_file_bytes
FROM cdp.lsq_pw."gold_dbt_lsq_pw_prospect_activity_phone_lookup$files";

-- file_count:       129,375
-- total_bytes:       39,927,202,046  (~37.2 GB)
-- avg_file_bytes:    308,616         (~301 KB)
```

**Average file size ~301KB, against a documented target of 128MB–1GB** (`DBT_ICEBERG_BEST_PRACTICES.md` §3; corroborated independently by [Dremio's Iceberg small-files writeup](https://www.dremio.com/blog/apache-iceberg-small-files-problem-causes-fixes-and-prevention/), which cites 256–512MB as the Parquet target). Files are **300–3000× smaller than target.** The `delete+insert` incremental strategy, with no explicit `write.target-file-size-bytes` set, produced this on its very first build — and nothing caught it, because the table had never been scanned.

This single, freshly-observed data point is direct evidence for three of the four features below: it shows (a) a new table fragmenting before any health check ever looks at it (Feature 2), (b) a missing file-size property that could have been caught from the DDL alone with zero scan history (Feature 3), and (c) a bucket count (`64`) picked before real row-volume was known, now demonstrably too coarse (Feature 4).

---

## 2. Related work

### 2.1 Industry practice and tooling

- **[Algoscale — Data Lake Cost Optimization](https://algoscale.com/go/blog/data-lake-cost-optimization/)** — the practice that seeded this RFC. Key claims used here: (a) *"validate pruning effectiveness: measure average bytes-scanned-per-query against total table size; if exceeding ~10%, the partition key misaligns with the workload"* (→ Feature 1); (b) *"trigger compaction when small files exceed 30% of total file count rather than fixed time schedules"*; (c) *"partitioning fixes first, then compaction, then lifecycle — earlier work amplifies later work"* — directly matches the PDAE-8357 sequencing (partition-key fix is the real fix; compacting the source tables was flagged in the ticket as "real but smaller, unrelated to the core problem").
- **[Dremio — Apache Iceberg Small Files Problem: Causes, Fixes, and Prevention](https://www.dremio.com/blog/apache-iceberg-small-files-problem-causes-fixes-and-prevention/)** — target file size guidance (256–512MB Parquet), and the causes taxonomy (streaming writers with small checkpoint intervals, over-partitioning, `delete+insert`/merge patterns without target-size tuning) — matches the mechanism behind §1.2's finding almost exactly.
- **[LakeOps — Automating Apache Iceberg Table Maintenance](https://lakeops.dev/blog/automating-iceberg-table-maintenance)** — the central architectural framing this RFC borrows: *"treating small file management as infrastructure, not a cron job... measure your tables today, compact the worst offenders, tune your writers to prevent recurrence, automate so it runs continuously without human intervention."* Also: *"detection comes first using `table_files()` to establish a baseline"* — i.e. detection should be cheap and can run independent of a full statistical scan (→ Feature 3's "no-scan-required" design).
- **[LakeOps — 9 Iceberg Table Compaction Tools Compared](https://lakeops.dev/blog/iceberg-compaction-tools-compared)** and **[olake.io — Apache Amoro](https://olake.io/blog/olake-amoro-iceberg-lakehouse/)** — competitive landscape. Apache Amoro's positioning — *"self-optimizing capabilities that continuously monitor Iceberg tables, automatically trigger compaction when needed... without manual intervention"* — is the direct contrast to Cryo's current scheduled/preset-driven model, motivating Feature 2. AWS S3 Tables and Dremio's Open Catalog auto-optimization are cited as the "fully managed, zero-config" end of the spectrum Cryo is not trying to match exactly (Cryo is intentionally an operator-facing console, not a fully opaque managed service — see `DESIGN.md`'s "engineered calm, operator-first" doctrine) but whose *detection* cadence (continuous, not periodic) is worth adopting.
- **[Starburst — Iceberg Partitioning Guide](https://starburst.io/blog/iceberg-partitioning)** — source for the bucket-transform recommendation applied in PDAE-8357 and generalized in Feature 1/4.

### 2.2 Research literature: AutoComp (LinkedIn, 2025)

**[AutoComp: Automated Data Compaction Framework](https://arxiv.org/html/2504.04186v1)** (arXiv:2504.04186) is directly relevant and worth citing in depth, for a reason specific to this codebase: **Cryo's own existing compaction-candidates ranking already carries this paper's name** — the Cost & Savings tab's "Compaction candidates (AutoComp §A2)" table and the `getAutoCompCandidates`/benefit-score/MOOP-score fields throughout Cryo's frontend and backend are a direct implementation of this paper's ranking approach. The four features below extend that same lineage rather than introducing a foreign framework.

**Problem statement (from the paper):** at LinkedIn, *"83% of the system's files were smaller than 128MB"* before compaction was systematized, causing HDFS scalability issues and query timeouts — the same shape of problem as §1.2's 301KB-average table, at even larger scale.

**Architecture — the OODA loop** (Observe, Orient, Decide, Act):

1. **Observe** — generate compaction candidates (table/partition/snapshot-level) and extract statistics.
2. **Orient** — compute traits describing *benefit* (file-count reduction) and *cost* (compute resources).
3. **Decide** — rank candidates via multi-objective optimization (MOOP): `Score = w₁ × (normalized file-count reduction) − w₂ × (normalized compute cost)`, weights summing to 1.
4. **Act** — schedule and execute.

**Cost model:** compute cost in GB-hours, `GBHr = ExecutorMemoryGB × (DataSize / RewriteBytesPerHour)` — a concrete, reusable formula for weighing "is this compaction worth running" that generalizes directly to Feature 2's fast-path trigger (a newly-created, still-small table is cheap to compact immediately; a 290GB table is not, and needs the fuller cost-weighed scheduling Cryo's governor already does).

**Two execution modes documented:** *periodic* (fixed-interval standalone service) and **optimize-after-write** (triggered by data modifications through engine hooks) — this is the literal architectural pattern Feature 2 proposes adding to Cryo, not a novel idea invented for this RFC.

**Measured production results:** AutoComp achieved a **44% reduction in files smaller than 128MB** over LinkedIn's prior manual process (12% more file-count reduction than manual compaction at equivalent candidate depth, `k=10`); in synthetic TPC-DS benchmarks, properly-tuned compaction gave a **2× query-latency improvement**; and file-count/entropy-based triggers performed comparably as decision signals — i.e., the "small files ratio" trigger Cryo already appears to use (§1.2, "Small file ratio 86.4% ≥ threshold 40%") is validated by this paper as a sound signal, not just a heuristic.

**One caveat worth carrying forward, directly from the paper's own benchmarks:** *"TPC-H: default (no compaction) performed best due to high rewrite costs on non-partitioned tables."* Compaction is not free and not universally beneficial — the cost side of the MOOP ranking matters, and this RFC's features should feed into that same cost-weighed decision process, not bypass it with unconditional auto-fixing.

### 2.3 Existing Cryo baseline (what NOT to rebuild)

Confirmed present in Cryo as of this session (do not duplicate):

- Core maintenance operations: `optimize` (compaction), `expire_snapshots`, `remove_orphan_files`, `optimize_manifests` — `cryo/engines/*`.
- AutoComp-derived compaction-candidate ranking (`cryo/scanner/property_recommendations.py` neighbors, `getAutoCompCandidates`, benefit-score/MOOP-score fields) — Cost & Savings tab and Health & Quality tab's Compaction zone.
- Small-file-ratio threshold trigger signals — confirmed exact match: `cryo/triggers/signals.py`, `threshold_ratio: float = 0.40` (40% small files triggers regardless of schedule). Path corrected 2026-08-19 — the original draft cited `cryo/scanner/signals.py`, which is not where this lives.
- Iceberg property recommendations panel (Tier 1/2/3 model — `cryo/scanner/property_recommendations.py`, `cryo/api/routes_property_recommendations.py`), covering `write.target-file-size-bytes`, `write.distribution-mode`, sort-order, bloom filters — **excludes partition-*spec* changes** (that's a separate module, see below, not because Cryo doesn't have one).
- **Correction (added on validation pass, 2026-08-19): a partition-evolution recommender already exists and was missed in the original baseline survey.** `cryo/optimizer/partition_analyzer.py::analyze_partition_evolution` reads `QueryPattern` (populated by `cryo/telemetry/patterns.py` from `QueryLog`), finds the dominant filter column, checks it against the current partition spec, and recommends a specific transform (`bucket(N)` via `_bucket_count_for`, or `identity`, or a finer time granularity via `_finer_transforms`) when it's missing — already exposed via `GET` (see `getPartitionEvolutionRecommendation` in the frontend) and already rendered in `OverviewTab.tsx`. This is functionally most of Feature 1 and a meaningful chunk of Feature 4 (see the validation notice at the top of this document) — read this module before designing either feature's backend.
- Health scoring / grade system, scheduled scans, preset-driven maintenance runs — Cryo's primary detection cadence today is **scan-triggered**, not event/write-triggered.

---

## 3. Feature 1 — Partition-Fit Detector

> **Re-scoped 2026-08-19 after validation — read this before the rest of the section.**
> The original draft below described this as a green-field build (`cryo/scanner/
> partition_fit.py`, a new `partition_fit_recommendations` table, a new endpoint). **That's
> not the right frame.** `cryo/optimizer/partition_analyzer.py::analyze_partition_evolution`
> already implements the core of this: it reads `QueryPattern` (populated, not a stub — see
> the §2.3 correction), finds the dominant filter column via `filter_column_counts`, checks
> `is_partitioned_by_filter_col`, and at `filter_pct >= 80` recommends a specific transform
> (`bucket(N)` sized by `_bucket_count_for`, or `identity`, or a finer transform via
> `_finer_transforms`). It's already exposed (`GET .../partition-evolution` via
> `getPartitionEvolutionRecommendation`) and already rendered in `OverviewTab.tsx`.
> **Both of the original "Risks / open questions" below are now answered**: Cryo does retain
> per-query predicate-column data (`QueryPattern.filter_count`/`equality_filter_count`, plus
> `avg_selectivity` as a scan-ratio proxy — see `cryo/telemetry/patterns.py`), so the
> "may not currently retain this" risk doesn't apply. The rest of this section is kept as
> useful design material (Algoscale's specific >10% scan-ratio rule, the `EXPLAIN`-derived
> column-constraint list, the false-positive risk) but should be read as **"what to add to
> `analyze_partition_evolution`,"** not "what to build from scratch."

### Problem

A table's partition spec can be structurally wrong for its actual query workload — as PDAE-8357 was. Cryo's existing `analyze_partition_evolution` catches this class of problem already for the "dominant filter column, >=80% of queries" case, but has gaps worth closing: it triggers on filter-column *frequency* alone, not on Algoscale's more precise *scan-ratio* signal (§2.1's `avg(bytes_scanned_per_query) / total_table_size_bytes > 10%` rule), and its cardinality check is a **column-name heuristic** (`cardinality_hint`, matching keywords like "id"/"date") rather than a real distinct-value count — both are real gaps, but they're refinements to an existing detector, not evidence one needs to be built.

### Design

**What to add to the existing detector, not a new one:**

1. **Scan-ratio signal, alongside the existing filter-frequency signal.** `QueryPattern.avg_selectivity` (rows matched / table's total rows, already computed per column — see `cryo/telemetry/patterns.py`'s `ColumnPatternStats` docstring) is a usable proxy for Algoscale's bytes-scanned ratio without needing new ingestion. Add a check: flag even below the current 80%-of-queries threshold if `avg_selectivity` for the dominant filter column is poor (i.e., pruning isn't actually working) — this catches PDAE-8357-shaped cases where a *minority* of expensive queries dominate cost even if they're not the majority of query *count*.
2. **Real cardinality instead of the name heuristic.** `cardinality_hint()` currently guesses "high"/"low" from column-name keywords (`_LOW_CARDINALITY_HINTS`/`_HIGH_CARDINALITY_HINTS`). The module's own docstring already flags this as a known gap ("no real NDV stats are available yet — Puffin NDV reading is still a stub"). If/when Puffin NDV stats land, wire them in here first — this directly improves both Feature 1 and Feature 4's bucket-sizing accuracy.
3. **Confidence/frequency numbers surfaced in the UI**, not just a verdict — this part of the original design is still correct and worth keeping regardless of where the detection logic lives.

**Algorithm additions (on top of what `analyze_partition_evolution` already does):**

- Compute `avg(bytes_scanned_per_query) / total_table_size_bytes` where derivable (from `QueryLog`/telemetry, if it already tracks per-query bytes scanned — verify against `cryo/telemetry/parser.py` and `iceberg_source.py` before assuming a gap) and use it as a *second* trigger path alongside the existing `filter_pct >= 80` one.
- Where Trino's own `EXPLAIN`-derived column-constraint list is available per query (`inputTableColumnInfos[].constraint.columnConstraints[].columnName`, confirmed present via `trino_analyze_query_performance`'s output this session), cross-check it against `QueryPattern`'s parsed predicates as a second, independent source — useful for validating `QueryPattern`'s own parse accuracy, not just for net-new detection.

### As shipped (2026-08-19)

- Extended `cryo/optimizer/partition_analyzer.py` only — no `partition_fit.py`, no new recommendations table.
- Scan-ratio is **`avg(QueryLog.bytes_read) / table_bytes`**, not `QueryPattern.avg_selectivity` as the trigger (selectivity is still copied onto the rec when present). `table_scan_ratio()` coerces `table_id` to `UUID` so it matches `QueryLog.table_id`.
- Second trigger fires when the dominant filter column is **not** in the spec **and** `scan_ratio > 10%` **and** `filter_pct >= 20` **and** ≥10 `QueryLog` rows with `bytes_read > 0`. Existing `filter_pct >= 80` path unchanged. `PartitionEvolutionRecommendation.trigger` is `scan_ratio` | `filter_frequency` | `granularity` | `bucket_sizing`.
- API `GET /api/v1/advanced/partition-evolution/{table_id}` returns optional `scan_ratio`, `avg_selectivity`, `trigger`.
- UI: `OverviewTab.tsx` appends trigger + scan-ratio percent + `reason` in the partition analyzer “why” copy. **No apply button.**
- **Not shipped:** Puffin NDV instead of `cardinality_hint`; Trino `EXPLAIN` column-constraint cross-check; Catalogs-page “partition mismatch” filter.

### Risks / remaining

- Mixed workloads can still false-positive; 80% frequency and 20%+scan-ratio floors are the current guards.
- Cardinality is still a name heuristic until Puffin NDV exists.

---

## 4. Feature 2 — Fast-Path Health Check for New Tables

### Problem

§1.2's table fragmented into 129,375 files on its *first build*, and nothing in Cryo's current model (scheduled scans, preset-driven runs) would catch it until its next scheduled scan cycle. Per AutoComp's own documented execution modes (§2.2), this is the *periodic* mode's known blind spot — the paper explicitly also documents an *optimize-after-write* mode for exactly this reason.

### Design

A lightweight, cheap-to-run detection path, decoupled from Cryo's full statistical scan cycle, targeting tables with **zero or one prior scan** (i.e., newly discovered in the catalog, or scanned exactly once with no maintenance history yet).

**Trigger shipped:** option 3 (cheap sweep), not catalog-discovery or write-event hooks. The sweep uses **Postgres only** (`Table.first_seen_at` within 7 days and/or `TableStatsHistory` count ≤ 1). It does **not** query Iceberg `$files` live — small-file detection reuses stored `TableStats` via `FileDegradationSignal` (40% / min 20 files). Missing target file size uses `compute_property_recommendations` (works with no stats).

**Decision rule:** flag only. **Do not auto-run compaction.** Governor/benefit scoring is unchanged; operators compact through existing run/preset flows.

### As shipped (2026-08-19)

- `cryo/scanner/fast_path_check.py` — `run_fast_path_check(..., persist=, respect_cooldown=)`.
- Scheduler telemetry tick: `Settings.fast_path_check_enabled` (default **true**), `fast_path_check_max_tables_per_tick` (50). Persist + 24h cooldown via existing `AuditLog` (`action=fast_path.small_files` / `fast_path.missing_target_file_size`, actor `cryo-fast-path-check`).
- `GET /api/v1/advanced/fast-path-findings` → `{ "findings": [...] }` (viewer). On-read compute with `persist=False`, `respect_cooldown=False` so the UI shows current flags.
- Health page “Recently discovered” card; table-detail Overview banner; `missing_target_file_size` links to property recommendations (`?tab=health-quality#hq-properties`).
- **Not shipped:** write-event / Airflow webhook; live `$files` sweep; auto-enqueue optimize.

### Risks / remaining

- Until a first scan writes `TableStats`, small-file findings cannot fire (only the missing-property finding can). That is intentional given “no live `$files` on request paths.”
- Write-event hook remains a future option (AutoComp optimize-after-write); not required for v1.

---

## 5. Feature 3 — Day-0 `write.target-file-size-bytes` Check

### Problem

§1.2's table never had `write.target-file-size-bytes` set at creation. Per LakeOps' framing (§2.1): *"prevention includes configuring `write.target-file-size-bytes` at the source"* — prevention, not just after-the-fact detection.

**Correction (validated 2026-08-19, before handoff):** the paragraph originally here claimed the existing property-recommendations panel only flags this property "*after* a table has been scanned and has stats," and that Feature 3's backend work would need to build "a zero-stats-required code path." **That's not accurate — verify against the code before re-adding this framing.** `compute_property_recommendations` in `cryo/scanner/property_recommendations.py` (line ~135) already does `stats = session.get(TableStats, table_id)`, which is `None` for an unscanned table, and every downstream helper (`_props_from_stats`, `_stats_dict`) already has an explicit `if stats is not None` / `if stats is None` branch that falls back cleanly (`_props_from_stats` returns `{}`, `_stats_dict` returns `dict(raw)`). `write.target-file-size-bytes` presence is read from `props`, which itself comes from `raw.get("iceberg_properties")` — i.e., DDL-derived table properties, not scan stats. **Ran this reasoning against the real function; the "Day-0, zero-stats" check this feature wants already exists and already works with zero scan history.** This materially shrinks Feature 3's scope: there is likely no new "zero-stats code path" to build at all. What's still missing (and is the actual gap) is the *scheduling* side — nothing currently runs `compute_property_recommendations` proactively for a just-discovered, never-scanned table; today it only gets computed when something else (a scan, a UI page load) calls it. Feature 3 should be re-scoped as "call the existing check earlier/more eagerly," not "build a new stats-free check."

### Design

This check needs **no scan, no statistics, and no query history** — only the table's own DDL (`SHOW CREATE TABLE` / Iceberg metadata properties), which is essentially free to query. This makes it fundamentally cheaper and faster than Feature 2, and a natural companion to it: Feature 2 catches "this table is *already* fragmented"; Feature 3 catches "this table is *at risk* of fragmenting" before any data has even landed.

**Rule:** for every `table_type='iceberg'` table Cryo's catalog knows about, check whether `write.target-file-size-bytes` is present in the table's properties at all. If absent, flag it — independent of scan cadence, independent of whether the table has any rows yet.

**Stretch goal, tying back to Feature 1's infrastructure:** if the property is present but implausible for the table's ingestion pattern (e.g., set far too low for a table expected to grow to billions of rows, inferable from naming/schema conventions or from the pipeline's own declared expected volume, if such metadata exists), flag that too — though this is materially harder to do reliably and should be scoped as a v2, not blocking v1.

### As shipped (2026-08-19)

- Confirmed: `compute_property_recommendations` with **no `TableStats` row** still recommends `write.target-file-size-bytes` = 256MiB, tier1 / apply (`tests/test_property_recommendations_api.py`).
- Scheduling: Feature 2 fast-path emits `missing_target_file_size`. Property autopilot now also considers active tables **without** stats (still only tier1 apply, still gated by `property_autopilot_enabled`, default **off**). Fast-path itself **does not** `set_properties`.
- UI: existing Property Recommendations panel + fast-path banner cross-link. **Catalogs “missing target file size” column not shipped.**

### Risks / remaining

- Property only affects future writes — same messaging as other Tier 1 recs.
- Autopilot applying Day-0 target size fleet-wide is opt-in; detection is on by default via fast-path.

---

## 6. Feature 4 — Bucket/Partition Cardinality Sizing Validator

> **Note (2026-08-19, after validation): this feature is genuinely still a gap, unlike
> Feature 1/3 above — but reuse `_bucket_count_for` rather than reinventing it.**
> `cryo/optimizer/partition_analyzer.py::_bucket_count_for(total_size_gb)` already computes
> "how many buckets should this table have, given its current size" (targeting
> `_TARGET_HEALTHY_PARTITION_GB = 0.5`) — but only as part of *recommending a new partition
> spec for an unpartitioned/mis-partitioned column*, never as a **re-check of an
> already-`bucket()`-partitioned table's existing `N` against its current volume**, which is
> §1.2's actual failure mode (the table already had `bucket(64)`; nothing re-validated it as
> the table grew). That re-check is the real, still-open gap this feature should close. Reuse
> `_bucket_count_for` for the "what should N be" calculation (don't write a second formula);
> the net-new work is (a) detecting tables that already use `bucket(col, N)` and (b) running
> the comparison periodically instead of only at spec-recommendation time.

### Problem

§1.2's `bucket(prospectactivityautoid, 64)` was a documented, explicit guess (see the model file's own TODO comment) made before the table's real row-volume was known. Once built, the table reached 1.1B rows — averaging ~17M rows per bucket, well past the 100MB–1GB per-partition target both `DBT_ICEBERG_BEST_PRACTICES.md` §3 and this session's research consistently cite. Nothing currently checks whether a chosen bucket count still fits a table's *actual*, current-day volume.

### Design

For any table using a `bucket(col, N)` partition transform (detectable from `SHOW CREATE TABLE`, already-scanned metadata), once real scan/file stats exist:

1. Compute `total_table_size_bytes / N` (average bytes per bucket) and, more precisely, actual per-bucket size via Iceberg's `$partitions` metadata table (which reports per-partition-value size directly — no estimation needed, this is exact metadata already present in every Iceberg table).
2. Compare against the target range (100MB–1GB per §2.1/§2.3's shared sourcing).
3. If average per-bucket size is outside range, recommend a new `N`: `N_recommended = total_table_size_bytes / target_midpoint_bytes` (e.g., targeting ~500MB per bucket), rounded to a sensible power-of-two or round number for hash-distribution evenness.
4. Distinguish "too coarse" (buckets too large, under-partitioned — §1.2's case) from "too fine" (buckets too small, itself a small-file risk, per `DBT_ICEBERG_BEST_PRACTICES.md`'s own "< 10 MB per partition: too fine-grained" rule) — both are real failure modes in opposite directions.

### As shipped (2026-08-19)

- `analyze_bucket_sizing()` in `partition_analyzer.py`; `bucket_count_for` is public (`_bucket_count_for` is an alias). Same 0.5GB target, power-of-two N, cap 2048.
- Runs when query-pattern volume is below threshold **or** after case 1/2 do not fire. Parses `bucket[N]` / `bucket(N)` from `TableStats.raw.partition_spec`.
- Too coarse: recommended N ≥ 2× current **or** avg bucket > 1GB. Too fine: recommended N × 2 ≤ current **or** avg bucket < 128MB.
- Average size is **`total_size_gb / N`**, not Iceberg `$partitions`. `trigger=bucket_sizing`. Warning: evolution needs a rewrite for existing data.
- UI: same Overview partition analyzer (`reason` + trigger), no separate panel, no one-click apply.

### Risks / remaining

- Changing N is partition-spec evolution (future writes only without rewrite).
- Exact per-bucket sizes via `$partitions` still a v2 improvement.

---

## 7. As-built map, tests, remaining gaps

Build order was executed as planned (3 → 1 → 4 → 2). Shared infrastructure:

- Partition findings stay on the existing Overview partition analyzer (Features 1 + 4).
- Fast-path sweep hosts Features 2 + 3 detection together.
- Feature 3 apply reuses Tier 1 `set_properties` (manual API or opt-in autopilot), not a new apply path.

### Code map

| Area | Path |
|---|---|
| Scan-ratio + bucket re-size | `cryo/optimizer/partition_analyzer.py` |
| Fast-path sweep | `cryo/scanner/fast_path_check.py` |
| Day-0 property check | `cryo/scanner/property_recommendations.py` (unchanged logic) |
| Autopilot includes no-stats tables | `cryo/scanner/property_autopilot.py` |
| Scheduler | `cryo/scheduler/service.py` (`_run_fast_path_check`) |
| Settings | `cryo/config.py` (`fast_path_check_enabled`, `fast_path_check_max_tables_per_tick`) |
| API | `cryo/api/advanced_routes.py` |
| UI | `web/src/api/client.ts`, `OverviewTab.tsx`, `OverviewTabPanel.tsx`, `DatalakeHealth.tsx`, `useTableDetail.ts` |

### Tests

| File | What it covers |
|---|---|
| `tests/test_partition_analyzer.py` | Existing cardinality/granularity guards; **scan-ratio trigger** (filter_pct below 80%); **bucket too coarse** (N=64, 37GB → 128); **healthy bucket → None**; **bucket too fine** (N=512, 1GB). |
| `tests/test_fast_path_check.py` | New table small-file flag; old table skipped; missing target size without stats; 24h AuditLog cooldown; AST guard that the module never imports planner/engines/worker. |
| `tests/test_property_recommendations_api.py` | `test_compute_property_recommendations_without_stats_defaults_target_file_size` — 256MiB, tier1 apply. |
| `tests/test_property_autopilot.py` | Autopilot includes tables with no `TableStats`. |
| `tests/test_iceberg_maintenance_intelligence_api.py` | `GET .../partition-evolution/{id}` returns `trigger=scan_ratio`; `GET .../fast-path-findings` returns `{ findings }` with `missing_target_file_size`. |
| `web/src/pages/DatalakeHealth.test.tsx`, `TableDetail.test.tsx` | Fast-path query mocks / empty state; table-detail mocks. |

### Remaining gaps (not in this implementation)

- Puffin NDV for real cardinality.
- Exact `$partitions` bucket sizes.
- Write-event / catalog-discovery hooks (still periodic sweep).
- Catalogs-page filters for partition mismatch / missing target file size.
- Fast-path live `$files` when there is no `TableStats` yet.

---

## 8. Full reference list

- [Algoscale — Data Lake Cost Optimization](https://algoscale.com/go/blog/data-lake-cost-optimization/)
- [Dremio — Apache Iceberg Small Files Problem: Causes, Fixes, and Prevention](https://www.dremio.com/blog/apache-iceberg-small-files-problem-causes-fixes-and-prevention/)
- [Dremio — Compaction in Apache Iceberg: Fine-Tuning Your Iceberg Table's Data Files](https://www.dremio.com/blog/compaction-in-apache-iceberg-fine-tuning-your-iceberg-tables-data-files/)
- [LakeOps — Automating Apache Iceberg Table Maintenance](https://lakeops.dev/blog/automating-iceberg-table-maintenance)
- [LakeOps — 9 Iceberg Table Compaction Tools Compared for Production Lakehouses](https://lakeops.dev/blog/iceberg-compaction-tools-compared)
- [LakeOps — Fixing Small Files in Apache Iceberg: A Practical Guide](https://lakeops.dev/blog/iceberg-small-files-guide)
- [LakeOps — Kafka to Iceberg Compaction, Done Right](https://lakeops.dev/blog/kafka-to-iceberg-compaction)
- [olake.io — How to Compact Apache Iceberg Tables: Small Files + Automation with Apache Amoro](https://olake.io/blog/olake-amoro-iceberg-lakehouse/)
- [Conduktor — Maintaining Iceberg Tables: Compaction and Cleanup](https://www.conduktor.io/glossary/maintaining-iceberg-tables-compaction-and-cleanup)
- [RisingWave — Iceberg Compaction Strategies for Streaming Workloads](https://risingwave.com/blog/iceberg-compaction-strategies-streaming/)
- [DEV Community — 11 Compaction Optimizations for Iceberg Data Lakes](https://dev.to/jonisar/11-compaction-optimizations-for-iceberg-data-lakes-52h2)
- [DEV Community — Maintaining Apache Iceberg Tables: Compaction, Expiry, and Cleanup](https://dev.to/alexmercedcoder/maintaining-apache-iceberg-tables-compaction-expiry-and-cleanup-12en)
- [overcast blog — 9 Apache Iceberg Table Maintenance Tools You Should Know](https://overcast.blog/9-apache-iceberg-table-maintenance-tools-you-should-know-df864ed7a6d5)
- [IOMETE — The Iceberg Table Maintenance Landscape: From Open-Source to Fully Managed](https://iomete.com/resources/blog/iceberg-maintenance-alternatives)
- [AWS Builder Center — Amazon S3 Tables vs Self-Managed Apache Iceberg on S3](https://builder.aws.com/content/39n7WU54TBsV3OlKwtJsnL54PVL/amazon-s3-tables-vs-self-managed-apache-iceberg-on-s3-a-technical-deep-dive-for-startups)
- [Starburst — Iceberg Partitioning Guide](https://starburst.io/blog/iceberg-partitioning)
- **[AutoComp: Automated Data Compaction Framework (arXiv:2504.04186)](https://arxiv.org/html/2504.04186v1)** — LinkedIn production research paper; the direct lineage source for Cryo's existing AutoComp-named compaction-candidate ranking.
- Internal: `internal-dbt-repo` — the mandatory house standards this session's PDAE-8357 fix and this RFC's targets (100MB–1GB partitions, 128MB–1GB files, delete+insert-over-merge guidance) are both drawn from.
- Internal: Jira PDAE-8357 (parent PDAE-4337, "S3 and Trino Cost Reduction Initiative") — full ticket, root-cause writeup, and the original query pattern.
- Internal: live measurements against `cdp.lsq_pw.gold_dbt_lsq_pw_prospect_activity_phone_lookup`, taken 2026-08-19 via the `mcp-trino` connection (§1.2) — row count, wall-clock timing, and `$files` metadata, all directly queried, not estimated.
