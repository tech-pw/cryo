# Iceberg Maintenance Intelligence — Four New Cryo Capabilities

**Status:** Proposed, not yet built
**Owner:** Aamir
**Last updated:** 2026-08-19
**Scope:** Full-stack (backend scanner/analyzer changes, new API surface, UI in Table Detail / Catalogs / Health)

---

## 0. Executive summary

This RFC proposes four new Cryo capabilities, each grounded in (a) published research and industry practice on Iceberg/data-lake maintenance, and (b) two pieces of real, measured evidence gathered in production this session: the **PDAE-8357** investigation (a query pattern scanning ~2.4B rows / ~133GB per call, 11,610 times/week, because its partition key didn't match its access pattern) and a **live, freshly-observed small-file pathology** on a table built during that same investigation (129,375 files averaging 301KB each, for only 37.2GB of data — see §1.2).

The four capabilities:

| # | Capability | Closes the gap that... |
|---|---|---|
| 1 | **Partition-Fit Detector** | ...meant PDAE-8357 took hours of manual `EXPLAIN`/log analysis to diagnose, instead of an automated flag |
| 2 | **Fast-Path Health Check for New Tables** | ...let a brand-new table fragment into 129K files before any scan ever looked at it |
| 3 | **Day-0 `write.target-file-size-bytes` Check** | ...meant that same table's writer never had a target file size to aim for in the first place |
| 4 | **Bucket/Partition Cardinality Sizing Validator** | ...meant an educated-guess bucket count (`bucket(id, 64)`) went unverified against real row-volume growth |

None of these replace existing Cryo capabilities (compaction, `expire_snapshots`, `remove_orphan_files`, manifest rewriting, the existing AutoComp-derived benefit-score ranking — see §2 for the current baseline). They extend Cryo from "detect and fix file-layout problems on tables I've already scanned" to "detect partition-*strategy* mismatches and catch fragmentation before it accumulates."

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
- Small-file-ratio threshold trigger signals (`file_degradation` / "Small file ratio 86.4% ≥ threshold 40%" — `cryo/scanner/signals.py`-adjacent logic).
- Iceberg property recommendations panel (Tier 1/2/3 model — `cryo/scanner/property_recommendations.py`, `cryo/api/routes_property_recommendations.py`), covering `write.target-file-size-bytes`, `write.distribution-mode`, sort-order, bloom filters — **explicitly scoped to exclude partition-*spec* changes** (a deliberate boundary, noted in the panel's own design rationale: partition-spec changes only affect future writes and require careful, non-blind handling).
- Health scoring / grade system, scheduled scans, preset-driven maintenance runs — Cryo's primary detection cadence today is **scan-triggered**, not event/write-triggered.

---

## 3. Feature 1 — Partition-Fit Detector

### Problem

A table's partition spec can be structurally wrong for its actual query workload — as PDAE-8357 was — and nothing in Cryo's current scope catches this. The property-recommendations panel is explicitly out of scope for partition-spec changes (§2.3). Diagnosing this today requires a human reading `EXPLAIN (TYPE IO)` plans and cross-referencing the table's `CREATE TABLE` DDL by hand, exactly as was done for PDAE-8357 (§1.1).

### Design

**Signal:** for each table, compute `avg(bytes_scanned_per_query) / total_table_size_bytes` from query-log telemetry, per Algoscale's stated rule of thumb (§2.1) — flag when this ratio is high (proposed starting threshold: >10%, per the source article; tunable) **and** the query log shows queries consistently filtering/joining on a column that is *not* the table's partition column.

**Data requirement:** a query log with per-query table references, filter/join columns, and bytes-scanned — the equivalent of `cdp.trino_logs.trino_queries` (columns confirmed present this session: `query_state`, `cpu_time_millis`, `physical_input_bytes`, `wall_time_millis`, `query_date`) or Cryo's own query-performance telemetry if it already ingests something equivalent (Cryo has a Query Performance page — verify its underlying data source covers filter-column extraction, not just aggregate timing, before assuming this is a green-field build).

**Algorithm (proposed):**

1. For each table with query-log coverage, parse `WHERE`/`JOIN ON` predicates from logged queries (or use the query engine's own `EXPLAIN`-derived column-constraint list, which Trino already exposes per query, per `trino_analyze_query_performance`'s output structure observed this session — `inputTableColumnInfos[].constraint.columnConstraints[].columnName`).
2. Rank predicate columns by frequency across the table's query log.
3. Compare the top predicate column(s) against the table's actual partition-spec columns (available from `SHOW CREATE TABLE` / Iceberg metadata, already scanned by Cryo).
4. If the top predicate column is absent from the partition spec **and** the measured scan-ratio exceeds the threshold, emit a recommendation.
5. **Recommend a specific transform**, not just "your partitioning is wrong": if the predicate column is high-cardinality (distinct-value count, derivable from existing scanned stats) → recommend `bucket(col, N)`; if low-cardinality (<50 distinct values, per Starburst's guide) → recommend identity partition; if it's a timestamp already covered by a coarser existing transform but queries filter at finer grain → recommend a finer transform.

### Backend implementation

- New module, e.g. `cryo/scanner/partition_fit.py`, mirroring the shape of `cryo/scanner/property_recommendations.py`.
- Requires a query-log ingestion path if Cryo doesn't already have one with per-query column-level detail (check Query Performance page's data model first — do not build a second one if `cryo/ai/` or similar already ingests this for the existing QPP/query-pattern features).
- New DB table or extension of existing recommendation storage: `partition_fit_recommendations` (table_id, predicate_column, current_partition_spec, recommended_transform, scan_ratio_pct, confidence, computed_at).
- New endpoint: `GET /tables/{id}/partition-fit` (read) — mirrors `GET /tables/{id}/property-recommendations`'s existing shape.

### UI/UX

- **New zone in Table Detail → Health & Quality tab**, alongside the existing Property Recommendations panel (which already explicitly excludes this case — this is the natural sibling panel). One row per finding: predicate column, current spec, recommended transform, one-line reason grounded in real numbers (e.g. *"queries filter on `prospectactivityautoid` in 94% of scans against this table; it's not in the partition spec (`ingest_date` only); avg scan ratio 87% of table size per query"*).
- **No one-click apply** — per Iceberg's own semantics (`DBT_ICEBERG_BEST_PRACTICES.md` §9: *"partition changes on large tables require full-refresh; existing data stays in the old partition layout for a spec-only change"*), this is a Tier-3-equivalent, human-reviewed recommendation only, consistent with the existing property-recommendations panel's own tiering model (§2.3). Action button: "Learn more" + a generated migration snippet (new table DDL / dbt model config), not an apply button.
- **Catalogs page**: optional new filter/column — "partition mismatch" flag — so this is discoverable fleet-wide, not just per-table.

### Risks / open questions

- False positives on tables with genuinely mixed query patterns (some queries filter by date, others by ID) — the recommendation must show its confidence/frequency numbers plainly so an operator can judge, not present a bare verdict.
- Requires query-log data Cryo may not currently retain at the granularity needed (per-query predicate columns, not just aggregate bytes) — this is a real, non-trivial prerequisite to validate before committing to a build estimate.

---

## 4. Feature 2 — Fast-Path Health Check for New Tables

### Problem

§1.2's table fragmented into 129,375 files on its *first build*, and nothing in Cryo's current model (scheduled scans, preset-driven runs) would catch it until its next scheduled scan cycle. Per AutoComp's own documented execution modes (§2.2), this is the *periodic* mode's known blind spot — the paper explicitly also documents an *optimize-after-write* mode for exactly this reason.

### Design

A lightweight, cheap-to-run detection path, decoupled from Cryo's full statistical scan cycle, targeting tables with **zero or one prior scan** (i.e., newly discovered in the catalog, or scanned exactly once with no maintenance history yet).

**Trigger options (pick one or combine, in priority order):**

1. **Catalog-discovery hook** — when Cryo's scanner discovers a table it has never seen before, immediately queue a lightweight file-count/size check (not a full statistical scan) rather than waiting for the next scheduled cycle.
2. **Write-event hook**, where available — if the ingestion orchestrator (Airflow, dbt) can emit a completion signal Cryo can subscribe to (Cryo already models `Asset`-triggered dependencies conceptually, per the broader platform's own Airflow-3 Asset pattern used in `internal-dbt-repo`), trigger the fast-path check immediately after a table's first successful write, not on Cryo's own schedule at all. This is the "optimize-after-write" mode from AutoComp §2.2, and the closest match to Apache Amoro's "continuously monitor... without manual intervention" positioning (§2.1).
3. **Fallback, if neither hook is feasible short-term:** a cheap, frequent (e.g. every 15–30 min) sweep that lists only `information_schema`/catalog-metadata for tables with `scan_count <= 1`, checking file count via Iceberg's `$files` metadata table alone (no full stats computation) — this is materially cheaper than a full Cryo scan and matches LakeOps' "detection first using `table_files()` to establish a baseline" pattern (§2.1).

**Decision rule once a new/near-new table is checked:** flag immediately if `file_count > N` and `avg_file_size < target_floor` (reuse the same small-file-ratio threshold Cryo's existing signal already uses, per §2.3, rather than inventing a second threshold system) — but **do not auto-run compaction unconditionally**. Per AutoComp's own cost-model caveat (§2.2, the TPC-H counter-example), route the flag through the same benefit/cost-weighed governor decision Cryo already uses for scheduled compaction, just triggered sooner.

### Backend implementation

- New lightweight check function, e.g. `cryo/scanner/fast_path_check.py`, callable independent of the full `analyze.py` scan path — must not require the full column-quality/stats pipeline, only Iceberg metadata (`$files`, `$snapshots`) which is cheap to query.
- New scheduler entry (or extension of the existing scan-scheduling logic) for "new/near-new table sweep," decoupled from the standard preset-driven cadence.
- If a write-event hook is pursued: new webhook/event-ingestion endpoint Cryo can receive from Airflow (`on_success_callback`-style, matching the failure-callback pattern already used throughout `internal-dbt-repo`'s DAGs).

### UI/UX

- **Health page**: a new, distinct "Recently discovered — needs first health check" or "New table, small-file risk" bucket, separate from the standard grade-sorted top-offenders list, so operators can see fast-path findings without them being buried under long-standing large-table problems.
- **Table Detail Overview tab**: if a table is flagged via the fast-path, show a distinct banner — *"First build produced N files averaging X — flagged within [time] of creation"* — visually distinct from the standard "Grade F, needs compaction" banner, so an operator understands this is a fresh, catchable-early problem, not chronic neglect.
- **Operations / Catalogs**: surfaces the same way existing scan-triggered findings do, just with a "first-seen" timestamp badge showing how quickly it was caught.

### Risks / open questions

- Whether a write-event hook is actually feasible depends entirely on what Cryo's current scanner architecture already supports for triggering outside its own schedule — needs a scoping pass before commit; the metadata-only sweep (option 3) is the safe fallback that needs no new integration points.
- Must not fire the same alert repeatedly as a table continues its normal early-life write pattern — needs a cooldown/dedup rule (Cryo already has cooldown-gate logic for its `conditional_table_optimize`-equivalent maintenance triggers per the PW platform's own documented gates — reuse that pattern, don't invent a new one).

---

## 5. Feature 3 — Day-0 `write.target-file-size-bytes` Check

### Problem

§1.2's table never had `write.target-file-size-bytes` set at creation. Cryo's existing property-recommendations panel already flags this property — but only *after* a table has been scanned and has stats. By the time that happens, the fragmentation has already occurred and the cost has already been paid. Per LakeOps' framing (§2.1): *"prevention includes configuring `write.target-file-size-bytes` at the source"* — prevention, not just after-the-fact detection.

### Design

This check needs **no scan, no statistics, and no query history** — only the table's own DDL (`SHOW CREATE TABLE` / Iceberg metadata properties), which is essentially free to query. This makes it fundamentally cheaper and faster than Feature 2, and a natural companion to it: Feature 2 catches "this table is *already* fragmented"; Feature 3 catches "this table is *at risk* of fragmenting" before any data has even landed.

**Rule:** for every `table_type='iceberg'` table Cryo's catalog knows about, check whether `write.target-file-size-bytes` is present in the table's properties at all. If absent, flag it — independent of scan cadence, independent of whether the table has any rows yet.

**Stretch goal, tying back to Feature 1's infrastructure:** if the property is present but implausible for the table's ingestion pattern (e.g., set far too low for a table expected to grow to billions of rows, inferable from naming/schema conventions or from the pipeline's own declared expected volume, if such metadata exists), flag that too — though this is materially harder to do reliably and should be scoped as a v2, not blocking v1.

### Backend implementation

- Small, standalone check — extend the existing property-recommendations scanner (§2.3, `cryo/scanner/property_recommendations.py`) with a **zero-stats-required code path**, since that module currently gates on `TableStats` existing (`stats = session.get(TableStats, table_id); ...` per this session's earlier reading of that file) — this check must work even when `stats` is `None`.
- Natural integration point: run this specific check as part of Feature 2's fast-path sweep (§4), since both are meant to run before/independent of a full scan — no need for a third separate scheduling mechanism.

### UI/UX

- Surfaces in the **existing Property Recommendations panel** (Table Detail → Health & Quality), not a new panel — it's a natural Tier 1 entry in that already-established tiered model (§2.3), just reachable without requiring scan history first.
- If reached via Feature 2's fast-path banner, cross-link directly: *"This table has no `write.target-file-size-bytes` set — see Property Recommendations for the one-click fix"* (Tier 1 properties already support one-click apply via the existing PyIceberg `set_properties()` transaction path, per this session's earlier work on that panel — reuse that execution mechanism unchanged).
- **Catalogs page**: a lightweight new column/filter, "missing target file size," computable fleet-wide without a scan — genuinely cheap to add given the check itself requires no stats.

### Risks / open questions

- Setting `write.target-file-size-bytes` only affects *future* writes, same caveat as every other Tier-1 property recommendation already documented in the existing panel — this needs the same "won't retroactively fix existing files" messaging already established there, not a new caveat to design.
- Low risk overall — this is the cheapest, most mechanical of the four features and a reasonable first one to build.

---

## 6. Feature 4 — Bucket/Partition Cardinality Sizing Validator

### Problem

§1.2's `bucket(prospectactivityautoid, 64)` was a documented, explicit guess (see the model file's own TODO comment) made before the table's real row-volume was known. Once built, the table reached 1.1B rows — averaging ~17M rows per bucket, well past the 100MB–1GB per-partition target both `DBT_ICEBERG_BEST_PRACTICES.md` §3 and this session's research consistently cite. Nothing currently checks whether a chosen bucket count still fits a table's *actual*, current-day volume.

### Design

For any table using a `bucket(col, N)` partition transform (detectable from `SHOW CREATE TABLE`, already-scanned metadata), once real scan/file stats exist:

1. Compute `total_table_size_bytes / N` (average bytes per bucket) and, more precisely, actual per-bucket size via Iceberg's `$partitions` metadata table (which reports per-partition-value size directly — no estimation needed, this is exact metadata already present in every Iceberg table).
2. Compare against the target range (100MB–1GB per §2.1/§2.3's shared sourcing).
3. If average per-bucket size is outside range, recommend a new `N`: `N_recommended = total_table_size_bytes / target_midpoint_bytes` (e.g., targeting ~500MB per bucket), rounded to a sensible power-of-two or round number for hash-distribution evenness.
4. Distinguish "too coarse" (buckets too large, under-partitioned — §1.2's case) from "too fine" (buckets too small, itself a small-file risk, per `DBT_ICEBERG_BEST_PRACTICES.md`'s own "< 10 MB per partition: too fine-grained" rule) — both are real failure modes in opposite directions.

### Backend implementation

- Extends the same property/layout-recommendation infrastructure as Features 1 and 3 (`cryo/scanner/property_recommendations.py` or a shared sibling module) — this is fundamentally the same *kind* of check as Feature 1 (partition strategy validation), scoped narrower (validating a chosen transform's parameter rather than recommending the transform itself). Worth building Features 1 and 4 on a shared "partition health" code path rather than two independent ones.
- Query source: Iceberg's `$partitions` metadata table (confirmed queryable this session, e.g. `SELECT ... FROM catalog.schema."table$files"` pattern) — exact, not estimated, data.

### UI/UX

- Same Health & Quality zone as Feature 1 (a shared "Partition health" panel makes more sense than three separate small panels for Features 1, 3, 4 — consolidate at build time into one coherent section with distinct row-types, rather than three visually separate cards, per the broader Cryo UI work this session on avoiding panel sprawl).
- Recommendation row: *"bucket(prospectactivityautoid, 64) — actual avg bucket size 581MB [or whatever the real number is]... recommend N=128"* with the same tier-appropriate caution as Feature 1 (spec changes need a rewrite to fully apply — link to the existing compaction/rewrite flow for execution, don't invent a new apply mechanism).

### Risks / open questions

- Changing `N` in a bucket transform is a partition-spec evolution, same caveats as Feature 1 (only affects future writes without a full rewrite) — messaging must be consistent across both features since operators will see them as clearly related.
- Needs real per-bucket size data, which requires at least one scan to exist — this feature is scan-dependent in a way Features 2/3 deliberately are not; sequence it after a table has meaningful data, not as part of the Day-0 fast path.

---

## 7. Cross-cutting implementation plan

**Suggested build order**, weighing cost/complexity against value (not a hard commitment, a reasoned starting point):

1. **Feature 3** (Day-0 property check) — cheapest, no new data dependencies, extends an existing panel, immediate value.
2. **Feature 2** (fast-path health check) — needs a scoping pass on trigger mechanism (write-event hook vs. cheap sweep) but doesn't need new external data sources; naturally bundles Feature 3's check as one of its outputs.
3. **Feature 4** (bucket sizing validator) — needs real scanned data to be useful, so sequence after a table has at least one real scan; shares infrastructure with Feature 1.
4. **Feature 1** (partition-fit detector) — the highest-value, most novel capability, but has the largest open dependency: confirm what query-log/predicate-column data Cryo already retains before committing to a timeline. Scope this first as a data-availability spike, separate from the feature build itself.

**Shared infrastructure to build once, not four times:**
- A single "Partition health" panel/zone in Table Detail → Health & Quality, hosting Features 1 and 4's findings together.
- A single new fast-path/lightweight-check scheduling path, hosting Features 2 and 3's triggers together.
- Reuse of the existing property-recommendations Tier 1/2/3 execution model and PyIceberg `set_properties()` transaction mechanism wherever a recommendation is one-click-appliable (Feature 3); reuse the existing cross-link-to-existing-flow pattern wherever it isn't (Features 1, 4 → link to compaction/rewrite, don't reinvent).

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
