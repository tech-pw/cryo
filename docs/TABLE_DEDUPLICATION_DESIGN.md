# Table-Level Deduplication — Research & Design

**Status:** research complete, real fleet numbers gathered, no code shipped yet.
**Author:** investigated 2026-08-13 at the operator's request ("find duplicate/backup/temp
tables across the lakehouse, cluster by similarity, use that + usage data to recommend
safe drops").
**Scope:** table-level dedup only — is `store.orders_backup` a redundant copy of
`store.orders`? — not row-level dedup within a single table.

---

## 1. The problem, stated precisely

Across ~13,225 tables and ~450TB, some unknown fraction are not distinct datasets but
**backups, temp copies, or abandoned forks of a table that still exists**. Someone runs
`CREATE TABLE orders_backup AS SELECT * FROM orders` before a risky migration and never
comes back to drop it. A `_v2` table gets created, the pipeline gets pointed at it, and
the `_v1` original just sits there. These are pure waste — the same data, paid for twice
(or five times), sitting in Standard storage with zero reads.

The goal: **detect these pairs/clusters automatically, score confidence, and turn that
plus existing usage signals (query-age, write-age — the same signals already used for the
[S3 tiering ROI work](../docs/) earlier this session) into a safe drop recommendation.**

This is genuinely harder than it sounds. Two tables with identical schema and similar
size are not necessarily duplicates — they might be two unrelated fact tables that happen
to share a grain. The research below exists to get this right, not fast.

---

## 2. Real numbers from this fleet (already gathered, 2026-08-13)

Before any content-similarity work, a pure **naming-convention scan** (`_backup`, `_bak`,
`_temp`, `_tmp`, `_old`, `_copy`, `_v[0-9]+`, `_test`, date suffixes, `_dedup`, `_dtmp`)
against `cdp.admin.iceberg_compaction_scan` (deduplicated to the latest scan per table —
see the S3-tiering work earlier this session for why that dedup matters) found:

| Pattern | Tables | Size |
|---|---:|---:|
| `_v[0-9]+` (versioned) | 261 | 6,841.3 GB |
| `_temp`/`_tmp` | 99 | 6,604.5 GB |
| `_backup`/`_bak` | 123 | 3,709.3 GB |
| date-suffixed | 3 | 739.9 GB |
| `_test` | 56 | 363.0 GB |
| `_dedup` variant | 3 | 146.3 GB |
| `_old` | 6 | 0.1 GB |
| `_copy` | 6 | 0.0 GB |
| **Total** | **557** | **18,404.4 GB (17.97 TB)** |

Filtering further to pairs where the suspected-copy's name, with the suffix stripped,
matches a table that **still actually exists in the same schema** (i.e. a real parent +
suspected-copy pair, not a lone table with a suspicious name and no original) surfaces
concrete, actionable candidates:

| Suspected copy | Size (GB) | Parent table | Parent size (GB) |
|---|---:|---|---:|
| `cdp_revenue.silver_infinite_practice_result_questions_temp` | 4,969.4 | `cdp_revenue.silver_infinite_practice_result_questions` | 5,187.9 |
| `user_experience.gold_results_backup` | 1,098.6 | `user_experience.gold_results` | 747.9 |
| `mview.gold_dbt_test_results_backup` | 749.4 | `mview.gold_dbt_test_results` | 79.7 |
| `mview.gold_dbt_video_stats_v3` | 406.3 | `mview.gold_dbt_video_stats` | 187.0 |
| `cdp_revenue.silver_infinite_practice_result_temp` | 244.3 | `cdp_revenue.silver_infinite_practice_result` | 1,188.7 |
| `cdp_revenue.gold_infinite_practice_result_backup` | 149.9 | `cdp_revenue.gold_infinite_practice_result` | 186.7 |
| `pw_experimentation.silver_unleash_experiments_v1_backup` | 102.4 | `pw_experimentation.silver_unleash_experiments_v1` | 2,686.6 |
| `user360_test.silver_user360_session_v1` | 101.6 | `user360_test.silver_user360_session` | 22.7 |
| `lms.gold_uservideosessions_dummy_v2`/`_v3` | 94.5 each | `lms.gold_uservideosessions_dummy` | 0.0 |
| `reports.gold_dbt_video_stats_reports_unified_test` | 51.9 | `reports.gold_dbt_video_stats_reports_unified` | 1,526.0 |

The single largest pair (`silver_infinite_practice_result_questions_temp` ↔ its parent)
is **~10TB combined** on its own.

**Important caveat, stated up front:** name matching is necessary-but-not-sufficient. It
tells you *where to look*, not whether two tables are *actually* duplicates. A `_backup`
table could be a stale, safe-to-drop leftover — or it could be a deliberate point-in-time
retention copy someone needs for audit/rollback. This is exactly why the rest of this
document exists: a real confidence pipeline, not a name-matching script that deletes
things.

---

## 3. What Iceberg gives you for free (Stage 1 signals — metadata only, no data read)

Iceberg's metadata layering (catalog pointer → `metadata.json` → manifest list → manifest
files → data files) exposes a surprising amount of structured, queryable information
without reading a single data file.

### 3.1 Schema fingerprinting

`metadata.json`'s current schema is a list of `{id, name, type, required}` fields. Build
two fingerprints:
- **Order-insensitive**: Jaccard similarity over the set of `(name, type)` pairs — catches
  column reordering.
- **Order-sensitive**: normalized edit distance over the ordered tuple sequence — a CTAS-
  produced backup almost always preserves exact column order; a hand-rebuilt "similar"
  table often doesn't, which is itself a useful discriminating signal.

Also fingerprint the **partition spec** (source column IDs + transform, e.g.
`days(event_ts)`, `bucket(16, user_id)`). Identical partitioning is a strong prior for
"same pipeline logic produced both" — but don't require an exact match: temp copies are
very often written unpartitioned even when the source is partitioned. Record it as a
feature, not a gate.

### 3.2 File-level overlap — the strongest signal when it fires

Iceberg data files are immutable, addressed only by S3 path. Two *different* tables can
reference the **literal same files**:
- **Shallow/zero-copy clone**: some engines create a new table whose manifests point at
  the same underlying files as the source, with zero data rewrite (behavior varies by
  engine/version — worth verifying for whatever write paths this fleet's tooling actually
  uses).
- **CTAS**: `CREATE TABLE B AS SELECT * FROM A` in Trino/Spark always *rewrites* files —
  no free win here — but produces files whose *content* is byte-identical, just at new
  paths.
- **Manual S3 `cp`/`sync` "backup"**: a filesystem-level snapshot copies Parquet files
  byte-identical to originals.

Practically: pull `file_path`, `file_size_in_bytes`, `record_count` from each table's
`$files` metadata table (`SELECT ... FROM catalog.schema."table$files"` — already used
this session for the bronze-bucket investigation). Build a `(file_size_in_bytes,
record_count)` multiset per table; multiset intersection between two tables is a free,
deterministic signal. If file content hashes (S3 ETag, or a computed checksum) are
available, exact file-hash-set intersection is **zero-ambiguity proof of physical
identity** — this is a signal no statistical method can give you, and it should bypass all
of Stage 2/3 when it fires.

### 3.3 Row count / byte size / column bounds as a free fingerprint vector

The `$files` table (and the underlying manifest's `lower_bounds`/`upper_bounds`/
`value_counts`/`null_value_counts` maps) gives per-file, per-column min/max/null-count —
populated at write time, readable without a scan. Aggregate to table level: total row
count, total byte size, and per-column `(min, max, null%)`. This full vector is cheap
enough to compute for every table in the fleet, not just candidate pairs.

### 3.4 Snapshot summary / lineage hints

Each snapshot's `summary` map records `operation`, `added-data-files`, `added-records`,
etc., and sometimes engine-specific tags (Spark's `WAP.id`, `spark.app.id`) or orchestrator
tags (Airflow/dbt job names) if the write path sets them. If any tool in this stack tags
snapshots with a source-table hint, that's directly queryable lineage — a table whose only
snapshot immediately follows a read of `source_table` in the same job is *definitionally*
derived from it, and this should short-circuit all statistical work below. Absent explicit
lineage, snapshot timestamp clustering (table B created seconds/minutes after table A's
write) is a cheap, noisy, worth-having signal in the composite score.

---

## 4. Statistical methods for confirming similarity (Stage 2 — candidate pairs only)

Once Stage 1 has cut ~13,225 tables down to a tractable candidate set (expect 2–4 orders
of magnitude reduction from schema-bucket blocking alone), apply:

### 4.1 MinHash + LSH for row-set similarity

MinHash estimates Jaccard similarity J(A,B) = |A∩B|/|A∪B| between two sets without
materializing them, by hashing each element with k independent hash functions and keeping
the per-function minimum; the fraction of matching signature positions estimates J(A,B).
LSH bands the signature into b bands of r rows so only pairs colliding in *some* band
become candidates — turning O(n²) comparison into near-linear bucketed lookup.

For table dedup: treat each **row** as a shingle (hash of concatenated column values, or
the natural/primary key if known), each table as a *set of row-hashes*. The resulting
Jaccard similarity is a direct, interpretable "row overlap %" — this is what you actually
want, as distinct from column-value MinHashing (which is what join-discovery systems like
JOSIE use, solving a different problem).

Implementation options:
- **`datasketch`** (Python) — `MinHash`/`MinHashLSH` classes, lightweight, fits directly
  into the existing FastAPI backend, computed over a bounded sample pulled via Trino.
- **Spark `MinHashLSH`** — for full-row-set signatures at higher cost/confidence, with
  `approxSimilarityJoin` avoiding O(n²) blowup.
- A native Go implementation (given Cryo already has a Go native engine) is worth
  considering for the sampled-row case — the algorithm itself (one hash family + rolling
  min) is simple enough to port without a JVM dependency.

### 4.2 Bloom filters as a cheaper pre-filter

Build a Bloom filter per table over row hashes (or PK values). Filter intersection
estimates overlap far more cheaply than full MinHash, at the cost of being a pure
existence test, not a similarity estimate. Use as a "is this worth computing MinHash for
at all" gate on the largest tables. Note: Iceberg/Parquet writers can already emit
**row-group-level Bloom filters** (`write.parquet.bloom-filter-enabled.column.<col>=true`)
for predicate pushdown — if key columns already have these enabled, they're potentially
reusable rather than building new filters, though they're scoped per-row-group/file, so
they'd need ORing together for a table-wide filter.

### 4.3 Column-level profiling comparison

Per table, per column: `{distinct_count, null_pct, min, max, mean/stddev, top-k values}`.
Compare via cosine similarity (numeric stats) and Jaccard similarity (top-k value sets per
matched column). Align columns by name first, fall back to type+position for renamed
columns. This is a lightweight table-wide version of the multi-signal fusion D3L uses for
column-pair matching (name similarity + value overlap + embedding similarity + format-regex
+ numeric-distribution similarity) — same idea, coarser granularity.

### 4.4 Sampling vs. full comparison

At 450TB, full-table hashing for every candidate pair is not affordable — reserve it for
the small set surviving Stage 2. Sample via **whole-file or whole-row-group selection**
(Parquet makes within-file random-row sampling expensive; Iceberg already tracks per-file
stats, so file-level sampling weighted by size is cheap and statistically representative).
Jaccard estimation error scales O(1/√n) in sample size — a few hundred thousand sampled
rows per side is generally enough to separate true duplicates (which sample-match near
95–100%) from coincidentally-similar tables (which sample-match near 0%, since unrelated
fact tables essentially never produce identical row hashes by chance).

---

## 5. Published research this design is grounded in

- **R2D2: Reducing Redundancy and Duplication in Data Lakes**
  (arXiv:2312.13427, SIGMOD/PACMMOD 2024) — **the single most directly applicable paper.**
  Targets exactly this problem: dataset containment and duplication detection across large
  enterprise table collections. Proposes a three-stage pipeline that maps almost 1:1 onto
  what's described here: (1) schema containment graphs pruning the n² pair space via cheap
  schema comparison, (2) statistical min-max pruning (row-count and value-range invariants
  — if A contains B, `row_count(A) ≥ row_count(B)` and B's value ranges fall within A's),
  (3) content-level pruning via sampling/hashing only on survivors. Explicitly
  distinguishes **exact duplicates**, **near-duplicates**, and **containment** (asymmetric
  subset) as different actionable outcomes — directly informs §7 below.

- **Goods: Organizing Google's Datasets** (Halevy, Korn, Noy et al., SIGMOD 2016) —
  Google's system for cataloging datasets at hyperscale with no centralized creation path.
  Explicitly clusters similar datasets and versions together, and mines lineage from
  production job logs (which jobs read/wrote which dataset) as a free signal — the same
  "lineage first, statistics second" ordering used in §3.4. Also propagates metadata within
  a cluster once one member is annotated — a pattern worth reusing for canonical-table
  selection once a Cryo cluster is identified.

- **JOSIE: Overlap Set Similarity Search for Finding Joinable Tables in Data Lakes**
  (Zhu, Deng, Miller et al., SIGMOD 2019) — formulates table relatedness as top-k overlap
  set similarity search via an inverted index over column values, minimizing index probes
  and full-set reads via a cost model. Built for join discovery, not dedup, but its
  inverted-index technique is directly reusable for Stage 2/3: an inverted index mapping
  sampled row-hash → table IDs turns "which tables share this row" into a lookup, which is
  effectively LSH implemented via inverted index rather than banding.

- **Table Union Search on Open Data** (Nargesian, Zhu, Pu, Miller, PVLDB 2018) —
  statistical column-distribution comparison for finding unionable tables (same logical
  schema/domain, e.g. two "employee" tables from different departments). Useful for the
  Stage 1/2 column-alignment step; targets a different problem (unionability across
  distinct logical tables) than identity-level dedup.

- **D3L** (Bogatu, Fernandes, Paton, Konstantinou, ICDE 2020) — five-signal weighted column
  matching (name similarity, value overlap, embedding similarity, format-regex, numeric
  distribution). Template for §4.3's multi-signal fusion, at column instead of table
  granularity.

- **Aurum** (Fernandez, Abedjan, Koko, Yuan, Madden, Stonebraker, ICDE 2018) — hypergraph
  over datasets connecting columns via similarity/PK-FK relationships, for data discovery.
  Its hypergraph-of-relationships representation is a reasonable model for the similarity
  graph → clusters step in §6.

- Apache Iceberg Table Spec (`iceberg.apache.org/spec`) and metadata-tables documentation
  (`$files`, `$snapshots`, `$manifests` — as queried live against `cdp.pw_bq` this session).

- `datasketch` (MinHash/MinHashLSH) and Spark MLlib `MinHashLSH` library documentation.

---

## 6. Clustering pairwise scores into groups

Build an undirected weighted graph: nodes = tables, edges = pairs scoring above threshold
(Stage 2 or confirmed by Stage 3), edge weight = similarity score. Use **connected
components** (union-find / disjoint-set, weighted-union-by-rank with path compression,
effectively O(n α(n))) to group transitively-linked tables — the standard approach from
the entity-resolution/record-linkage literature.

**Known failure mode, stated directly:** naive connected-components clustering increases
recall but is noise-sensitive — one spurious high-scoring edge can transitively merge two
otherwise-unrelated clusters ("cluster chaining"). Mitigate by either (a) requiring a
minimum edge weight well above the pairwise decision threshold before allowing transitive
merges, or (b) a post-connected-components refinement step that only keeps a cluster if it
forms a near-clique (most pairs within it are also high-scoring, not chained through a
single bridge edge).

---

## 7. From cluster to drop recommendation

Within each cluster, compute a composite "keep score" per table, recommend dropping all
but the top-scored member. Signals, in rough priority order:

1. **Not the canonical/oldest table** — prefer keeping the table with the earliest
   creation snapshot, or the one lineage/dbt-model metadata names as the actual
   materialization target (not a byproduct).
2. **Not recently read** — join against Cryo's existing `TableQueryActivity`/read-age
   tracking (the same signal validated during the S3-tiering ROI work). A table with zero
   reads in 90+ days is safe to flag regardless of similarity score; one actively read by
   dashboards needs a human decision, never an auto-drop.
3. **Cluster role from Stage 3** — if containment was established with a direction
   (A ⊇ B), prefer dropping the subset *unless the subset is the one being actively read*,
   in which case flag for investigation rather than auto-recommending drop of the superset.
4. **Naming heuristics** (§8) — tie-breaker/confidence booster only, never primary.
5. **Storage bytes reclaimed** — not a safety signal, but determines triage/review order
   by $ impact, same prioritization logic already used for the S3-tiering candidate list.

**Output a ranked, human-reviewable recommendation list, not an auto-delete action** —
at least initially. Given the asymmetric cost of a false positive (real data loss — see
the `gold_gst_slabs` incident earlier this session, a direct, recent, and costly example of
exactly this asymmetry) versus a false negative (a duplicate sits around a bit longer),
this must be a recommend-and-confirm workflow. Auto-drop, if ever enabled, should be
reserved for the highest-confidence tier only (exact file-overlap or exact Stage-3 row
match, zero reads in N days, non-canonical by lineage) after a burn-in period of manual
confirmations validates the pipeline's real-world precision.

---

## 8. Naming-convention heuristics (cheap, real, but a tie-breaker only)

Confirmed useful patterns from §2's actual scan: `_backup`/`_bak`, `_old`/`_orig`,
`_temp`/`_tmp`/`_scratch`, `_copy`/`_dup`, `_test`/`_staging`/`_wip`, `_deprecated`/
`_archive`/`_delete_me`/`_do_not_use`, versioning (`_v1`/`_v2`/`_final`/`_final_final` —
classic anti-pattern, high signal when paired with a schema-identical sibling), date
suffixes (`_20250315`, `_2025_03`, `_snapshot_<date>` — common before risky migrations),
and prefix variants of the same tokens.

Implementation: strip the matched token, check whether the stripped name exactly or
fuzzily (Levenshtein ≤ 2, or same schema fingerprint) matches another existing table —
feed as one more weighted signal into the Stage 2 composite score, and surface it in the
recommendation's explanation text (a human reviewer trusts "94% row overlap AND `_backup`
suffix AND zero reads in 60d" far more than a bare numeric score).

**Explicit failure mode:** `_v2` is just as often the *live* table waiting for `_v1` to be
dropped as the reverse. The naming signal should inform *which side of a confirmed pair to
recommend dropping*, combined with recency-of-write/read — it must never determine
directionality alone.

---

## 9. Why this is genuinely hard — false-positive risks

Stated explicitly because every one of these is a real way to destroy real data if the
pipeline is trusted past its actual confidence level:

- **Coincidental schema/size similarity, unrelated data.** Two fact tables with the same
  grain (`event_id, user_id, ts, value`) can have identical schemas and comparable sizes
  while holding completely disjoint data (`page_views` vs `click_events`). Schema+size is
  a necessary-but-not-sufficient filter — never a drop trigger alone. Iceberg's per-column
  min/max bounds help for free here: if key-column value ranges don't overlap at all, the
  pair can be pruned before Stage 2 even starts.

- **Containment ≠ duplication.** A table that's a filtered subset of another
  (`orders_us` ⊆ `orders_global`) may exist deliberately — query performance, access
  control, contractual reasons. R2D2's three-way distinction (exact / near-duplicate /
  containment) must be preserved end-to-end: containment pairs get a different
  recommendation ("possible redundant subset — review access patterns and downstream
  dependents before dropping"), not lumped into "duplicate, safe to drop."

- **Slowly-changing dimensions look identical at any single snapshot but diverge over
  time.** A `dim_customer` table and a month-old "backup" will show ~99% row overlap today
  if churn is low — statistically near-indistinguishable from a true duplicate — but the
  backup may be a deliberate SCD Type-2/audit/rollback retention copy that must not be
  dropped. Mitigate by treating "older, mostly-superseded copy of an actively-evolving
  table" as its own lower-confidence category, requiring the naming/purpose heuristic or an
  explicit retention-policy check before recommending removal.

- **Incremental vs. full-snapshot tables of the same logical entity.** `orders_incremental`
  (rolling N-day window) and `orders_full_history` (complete accumulating table) will have
  very different row counts/sizes despite representing the same entity. Correctly *not*
  paired by Stage 1 blocking (good) — but the inverse failure also happens: a genuinely
  stale subset-duplicate can get wrongly excluded if the row-count tolerance band is too
  tight. Argues for a wide tolerance band using containment-style min-max logic (per R2D2),
  not a narrow near-equal-size requirement.

- **Snapshot/time-travel confusion.** A table queried via time-travel against an old
  snapshot can look, in a naive scan, like it duplicates another table's current state.
  Metadata crawls must always read the *current* snapshot's manifest list unless explicitly
  auditing history, or the pipeline will manufacture false candidate pairs between a table
  and its own past self.

**The general lesson, common to every system reviewed** (Goods, R2D2, JOSIE, D3L): cheap
syntactic/statistical filtering prunes the search space; expensive content-level
confirmation runs only on survivors; human- or provenance-informed judgment makes the final
containment/duplicate/derivative call — because no single cheap signal is trustworthy
alone, and full pairwise content comparison at lakehouse scale is the one thing that cannot
be afforded. This design should be framed around **confidence tiers with matched action
tiers** (auto-flag-only → recommend-with-evidence → auto-drop-eligible), never a single
binary duplicate/not-duplicate classifier.

---

## 10. Proposed staged pipeline for Cryo (concrete)

**Stage 1 — Candidate generation** (metadata-only, all ~13,225 tables, cheap enough to run
routinely). Pull schema fingerprint (ordered + unordered), partition spec, row count, byte
size, per-column min/max/null% from `metadata.json`/`$files` (already crawlable by Cryo's
existing Go engine). Block via standard record-linkage blocking: bucket by unordered-schema
hash first (exact schema match is the strongest free filter), then within a bucket only
pair tables whose row counts/byte sizes fall in a *wide* tolerance band (R2D2-style,
allowing asymmetric ratios to catch containment, not just near-equal duplicates). Run the
file-path/file-size-multiset overlap check here too — any nonzero overlap is near-100%-
confidence and can skip straight to a recommendation. Expect 2–4 orders of magnitude
reduction in pair count from schema-bucket blocking alone.

**Stage 2 — Statistical fingerprint scoring** (candidate pairs only). Composite score in
[0,1] from schema similarity + column-stat cosine similarity + MinHash-estimated row-set
Jaccard on a bounded sample. Weighted average, explainable and auditable (a simple, stated
weighting beats an opaque model for a tool that recommends deleting data) — persist the
score *and* its component breakdown for every candidate pair.

**Stage 3 — Exact verification** (only pairs scoring above a high threshold, e.g. ≥0.85).
Real Trino query: row-hash `EXCEPT`/`INTERSECT`, or `FULL OUTER JOIN` on a natural key if
one exists, to get exact matched/unmatched/changed-row counts and establish true
containment direction. Gate hard on Stage 2 score and absolute table size — for very large
tables, accept a sampled `EXCEPT` or fall back to a lower-confidence Stage-2-only label
rather than a full scan.

**Output**: ranked, human-reviewable recommendations with full evidence trail (which
signals fired, what score, what read/write-age, what containment direction if known) — not
auto-delete. This is deliberately the same operating posture Cryo already uses for cost
ceiling enforcement and fleet admission caps: guardrails and recommendations first, blast-
radius-limited automation only after the mechanism has proven itself live.

---

## 11. Open questions before implementation starts

- Does any existing ingestion/orchestration tool in this stack (Airflow, dbt, Hevo) already
  tag Iceberg snapshots or table properties with source-table lineage? If so, §3.4's
  lineage shortcut could cover a meaningful fraction of clusters for near-zero cost before
  any statistical work runs.
- Are S3 ETags or per-file checksums retrievable cheaply enough (via existing S3 API access
  or Cryo's own crawl) to make §3.2's file-hash-intersection check a real, exact signal
  rather than a size/count proxy?
- What confidence threshold and read-age window should gate the first real drop
  recommendations shown to a human? (Mirrors the same question already answered for the
  S3-tiering work — 90-day zero-reads via `trino_logs.trino_queries`, not the 12-13-day
  `TableQueryActivity` history.)
- Where should Stage 1/2 scores live — a new table alongside `TableStats`/
  `iceberg_compaction_scan`, or a dedicated `table_similarity_pairs` table? Needs a
  concrete schema before Stage 1 can be implemented.
