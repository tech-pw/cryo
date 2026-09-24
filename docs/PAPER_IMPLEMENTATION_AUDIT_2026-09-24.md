# Research paper implementation audit — 2026-09-24

Every paper Cryo cites, read against the paper text and against the code that actually runs in
production after the batch-pipeline cutover (`cryo/maintenance/`). Written ahead of open-sourcing:
a claim like "Cryo implements AutoComp" has to survive someone reading the paper next to the code.

Paper texts were read from arXiv HTML (AutoComp, WAIR, Zero-Scan DQ), the VLDB PDF (Okolnychyi)
and the abstract only for PTO (full text is behind ACM DL).

The audit found gaps in every paper, and the same day's work closed the ones that could be
closed faithfully. Each section below says what the paper specifies, what Cryo does now, and
what is still Cryo's own choice or missing. Thresholds the papers don't give are called out as
Cryo's.

## Summary

| Paper | Venue (verified) | Status after 2026-09-24 |
|---|---|---|
| AutoComp — Gruenheid, Camacho-Rodríguez et al. (LinkedIn) | SIGMOD 2025 Companion, DOI 10.1145/3722212.3724430, arXiv:2504.04186 | **Implemented** on the batch path: paper traits, pool normalization, 0.7/0.3, budgeted greedy top-k. Query heat is an opt-out extension. |
| Okolnychyi et al. (Apple) | PVLDB 17, VLDB 2024 | **Faithful** minor/major delete policy; thresholds are Cryo's |
| Zero-Scan DQ — Verma et al. (LinkedIn) | FORMATS '26, DOI 10.1145/3802514.3812601, arXiv:2605.30308 | **Implemented**: manifest stats, writer identity, arrival/freshness, row-count anomalies, MOR-corrected live rows, §5.4 declarative constraints (via Deequ below) |
| Deequ — Schelter et al. (Amazon) | PVLDB 11(12), VLDB 2018 | **Implemented, metadata subset**: declarative constraints, metrics repository, rate-of-change check; three-valued verdicts |
| Ernest — Venkataraman et al. | NSDI 2016 | **Implemented, opt-in**: the paper's four-term model fit by NNLS on measured rewrites; used for AutoComp's cost only when it beats the rate model on held-out runs |
| Pivoted Query Synthesis — Rigger & Su | OSDI 2020 | **Implemented as a test oracle** for maintenance rewrites, not for engines |
| LST-Bench, CherryPick, Bao | SIGMOD 2024, NSDI 2017, SIGMOD 2021 | **Deferred**, reasons below |
| WAIR — Liu, Zhou, Yan, Zhang (Tsinghua/CMU/Snowflake) | SIGMOD 2026 (PACMMOD 43), DOI 10.1145/3802127, arXiv:2602.23289 | **Implemented, recommend-only**: boundary files, Eq. 3 savings, Algorithm 2 cut, Eq. 4 α, scoped Spark sort rewrite |
| PTO — Meduri, Kreismann, Barber, Reinwald (IBM) | SIGMOD 2026 (PACMMOD 4(1)), DOI 10.1145/3786681 | **Partial**: PTO's parameter space and workload pruning; heuristic scorer instead of sampling + gradient-boosted trees; shadow-only |

## AutoComp

The paper (§4.2–4.3, §5, §6):

- T1, file-count reduction: ΔF_c = count of files smaller than the target file size.
- T2, compute cost: GBHr_c = ExecutorMemoryGB × DataSize_c / RewriteBytesPerHour.
- Min-max normalize each trait across the candidate pool; S_c = w1·T1′ − w2·T2′, Σw = 1; rank by
  S_c; greedily take the top-k that fit the compute budget. Evaluation weights 0.7 / 0.3.
- Workload awareness is future work (§7), not part of the method.

What Cryo runs (`cryo/maintenance/selection.py`, called by the batch coordinator every night):

- ΔF counts every data file below the table's `write.target-file-size-bytes` (256 MB when unset, where the paper used 512 MB), plus
  delete files, which the rewrite removes. Both scanners emit it (`files_below_target`).
- GBHr uses the lane's executor memory, the bytes of below-target files, and RewriteBytesPerHour
  measured as the median of recent runs' observed throughput (`calibrated_rewrite_gb_per_hour`),
  falling back to 25 GB/h before any run has measured it.
- Normalization is across the eligible pool, weights default to 0.7/0.3, and the greedy cut is
  against a GBHr budget derived from lanes × lane memory × usable hours.
- **Extension, labelled as such:** `CRYO_MAINTENANCE_AUTOCOMP_HEAT_WEIGHT` (default 0.3) blends in
  min-max normalized log(1 + 30-day Trino queries): S = (1 − h)·S_c + h·H′. Setting it to 0 gives
  the paper's ranking exactly. Every run records both orderings in `summary.autocomp`, so the
  extension's effect can be measured rather than assumed.

Deviation that remains: candidates are whole tables (the paper's production deployment), and
partitions inside a table are ordered by small-file count, which is Cryo's parity rule.

The older governor-path scorer (`compute_moop_scores`) is still Cryo's own four-trait variant;
its docstring now says so, and that scores from per-table batches aren't comparable across tables.

## Okolnychyi et al.

The paper (§3.2.9, §5.2) establishes: position-delete files can be compacted together without
reading data (minor), which took 23% of one iteration's time and cut read time 45%; minor
compaction cannot fully restore performance, so deletes must eventually be merged into data files
(major); equality deletes can only be converted to position deletes, which reads data.

Cryo: minor = Spark `rewrite_position_delete_files`, major = `rewrite_data_files` with delete
thresholds, escalation after N successful minor runs, equality deletes go straight to major, V3
tables excluded. That matches the paper. The paper gives no thresholds; 5 runs / 10% / 50 files
are Cryo's and are not yet validated against read-cost measurements.

## Zero-Scan DQ

The paper: per-file manifest stats (record counts, null counts, bounds) and snapshot summaries
turned into continuous signals — row-count anomalies, null-rate drift, range violations,
arrival/freshness — with writer identity so maintenance commits don't read as data changes, and
MOR-aware row counts. It gives no detection thresholds.

What Cryo does now:

- **Writer identity** from the Iceberg spec itself: a snapshot whose `operation` is `replace` changes
  no data (compaction, sort, manifest or delete rewrites), so it is excluded from every signal.
  This covers Cryo's own maintenance commits and anyone else's.
- **Commit log**: both scanners emit the newest 200 snapshots' time, operation and summary counters
  (`cryo/scanner/commit_signals.py`, `engine-go/internal/scanstats/scan.go`).
- **Freshness** uses the last data commit. Before this, a nightly compaction reset "last updated",
  hiding upstream pipelines that had stopped writing.
- **Arrival overdue**: time since the last data commit exceeds 2× the p90 of historical gaps
  (min 5 gaps, min 1 h overdue). **Row-count anomaly**: the latest commit's net rows is more than
  5 robust z-scores (median/MAD) from the baseline of at least 8 commits. Both thresholds are Cryo's.
- **Live rows** = total-records − total-position-deletes, as the paper's MOR correction.
- **Null drift and range checks** from manifest stats, unchanged. Bounds are now kept only for
  numeric and temporal types. Writers store string min/max verbatim, and prod had employee emails
  in 229 columns, which the paper doesn't address. Existing rows need
  `tmp/purge_string_bounds.py` run once after deploy.
- Signals feed the alert dispatcher and the Quality tab ("Data arrival", "Row count").

§5.4, declarative constraints, is covered by the Deequ implementation below. Rules are evaluated
after every scan of the table rather than on every commit. Cryo sees a table when it scans it.

## Deequ

The paper: users declare constraints (size, completeness, containment, uniqueness, patterns and
more). Deequ computes the metrics they need in batched scans, stores them in a metrics repository,
and uses anomaly checks such as relative rate of change over that history.

Cryo (`cryo/quality/constraints.py`, `constraint_store.py`, the Quality tab and
`/api/v1/tables/{id}/quality-constraints`) keeps the declarative model and replaces the scan with
Iceberg metadata. That limits the rule set to what metadata can decide:

| Kind | Deequ counterpart | Decided from |
|---|---|---|
| `size` | hasSize | live rows (MOR-corrected) |
| `completeness` | hasCompleteness / isComplete | manifest null and value counts |
| `range` | isContainedIn / isNonNegative | table-level min/max aggregated across files |
| `size_change` | RelativeRateOfChangeStrategy | the previous result for an earlier snapshot |
| `freshness` | none (Zero-Scan DQ) | last data commit, excluding `replace` snapshots |

Uniqueness, distinct counts, patterns and custom predicates need the data and are not offered.

The difference from Deequ that matters is that metadata can be incomplete. Every verdict is
therefore `passed`, `failed` or `unknown`, and missing evidence never counts as a pass:

- Column rules require every data file to carry metrics for the column (value counts summing to
  the table's record count). Otherwise the result is unknown. Columns are identified by Iceberg field
  id, so a rename doesn't detach a rule.
- Range rules require complete bounds. Both scanners now record `bounds_complete`, which is false
  when any file holding values for the column lacks a bound. The Go scanner, the one prod runs,
  did not emit table-level bounds before this change. Bounds are aggregated with typed comparisons
  for numeric, date and timestamp types only.
- Delete files: removing rows can only take nulls and values away. So a pass computed over all
  rows still holds, while a failure is only asserted when the snapshot has no delete files.
  Equality deletes make the live row count unknown.
- Results are as of the scanned snapshot and record its id.

The results table is the metrics repository: one row per rule per scanned snapshot (re-checking
the same snapshot with the same outcome only refreshes its timestamp), newest 100 kept per rule.
`size_change` compares against the newest result from a different snapshot. Failed rules with severity `error` feed the existing alert
dispatcher. Evaluation runs inside the scan's persistence transaction, in a savepoint, so a
broken rule cannot fail a scan.

## Ernest

The paper models a job's time on m machines at data scale s as
t = θ0 + θ1·s/m + θ2·log m + θ3·m, with θ ≥ 0 fit by non-negative least squares. It picks
training runs by optimal experiment design and predicts larger configurations from small ones.

Cryo (`cryo/maintenance/ernest.py`):

- Samples are real rewrite statements from past batch runs, recorded per statement in
  `summary.duration_samples`: rewritten GiB, the lane's cores and seconds. They are collected
  passively. No runs are spent on experiment design, which is the paper's other half.
- Fit: exact NNLS by enumerating active sets (4 terms, 15 supports), in pure Python.
- Validation: the newest 25% of samples (at least 5) are held out in time order. The model is
  *preferred* only when its MAPE beats the incumbent single-rate model's by 5%, with at least 20
  training samples.
- Use: `CRYO_MAINTENANCE_DURATION_MODEL=ernest` (default `rate`) makes AutoComp's GBHr cost use
  Ernest's prediction, but only while it is preferred. Every run's `summary.autocomp.duration_model`
  records the calibration either way, and `GET /api/v1/maintenance/duration-model` shows it.

Caveat: lanes run Spark local mode with 1, 2 or 4 cores, so "machines" means cores on one pod,
and the paper's extrapolation to larger clusters isn't exercised. Leave it `rate` in prod until
the endpoint reports `preferred` on real history.

## Pivoted Query Synthesis

The paper tests database engines. It picks a pivot row, builds a random predicate, evaluates it
under three-valued logic and rectifies it (keep, NOT, or IS NULL) so it is TRUE for the pivot.
Any engine that then omits the pivot has a bug.

Cryo uses it to test maintenance, not engines (`cryo/testing/pqs.py`, `tests/test_pqs.py`). Probes
run through PyIceberg's scan planning, so a rewrite that writes wrong bounds or loses rows makes
pruning skip the pivot. Generated values are edge cases: extreme longs, 40-character strings past
the 16-character bound truncation, 1970 and 9999 dates, NULLs, and a column added mid-history.
Small fixtures also compare full results against a reference evaluation, catching extra rows,
and a greedy shrinker reduces failing predicates.

**Finding.** PyIceberg 0.11.1 does not treat NULL consistently under negation. `v <> 5` drops an
explicit NULL but keeps a row from a file written before `v` existed. `NOT v IN (...)` keeps both.
Compaction rewrites old files into new ones with explicit NULLs, so those filters return different
rows before and after a rewrite of identical data. Trino and Spark apply SQL semantics and aren't
affected. The harness renders negation onto the atoms PyIceberg evaluates consistently, and
`test_probes_agree_after_a_rewrite_materializes_an_evolved_column` pins the rewrite case. Anything
in Cryo that filters with PyIceberg expressions should avoid `NotEqualTo`, `NotIn` and `Not` over
nullable, schema-evolved columns.

Not yet done: the same probes against a native Go rewrite end to end
(`tests/integration/native/`, needs `make up-datalake-native`).

## Deferred papers

- **LST-Bench** (SIGMOD 2024) benchmarks table formats over long-running workloads with
  maintenance phases. Using it properly means TPC-DS-style data, engine replay and repeated
  multi-hour runs on a real cluster, so it's a benchmark project, not a code change. PQS covers
  correctness now. LST-Bench is the right tool for publishing performance-over-time numbers.
- **CherryPick** (NSDI 2017) finds a cloud configuration by Bayesian optimization over real
  trial runs. It needs a replayable workload to spend trials on, plus a duration model that has
  proven itself. Ernest's calibration is the prerequisite; revisit when it reports `preferred`.
- **Bao** (SIGMOD 2021) steers a query optimizer with hint sets learned from executed queries.
  That changes Trino query planning, which is outside Cryo's maintenance scope. The adoption
  doc marks it deferred.

## WAIR

The paper: files ("micro-partitions") whose clustering-key range partially overlaps a queried
range's endpoint are boundary files. Eq. 3 estimates the time a query would save if a file were
reclustered, from its utilization and the query's measured read time per byte. Algorithm 2 sorts
files by savings over a window and cuts at the prefix minimizing α·cost − savings, reclustering
only if that is negative and within a cost limit.

What Cryo does (`cryo/optimizer/wair/`, recommend-only; `POST /tables/{id}/wair/analyze` and the
Health & Quality tab):

- Query ranges per table from the Trino query log, with each query's CPU time and bytes read, over
  a 7-day window. Only numeric/temporal literals are stored.
- Per-file bounds for at most 3 range-queried numeric/temporal columns per table, from manifests.
- Eq. 3 savings in CPU-seconds, Algorithm 2's cut, α from Eq. 4, recluster cost at the measured
  Spark rewrite rate × lane vCPUs. Abstains without measured query cost or a measured rewrite rate.
- The chosen key is the column with the highest total savings (the paper's single-key baseline).
  The action is a Spark `rewrite_data_files` sort rewrite whose `where` selects exactly the files
  straddling the chosen endpoints.

Assumptions, surfaced in every report's `model_notes`: utilization assumes values are uniform
within a file's min/max (the paper measures it); the hybrid DBSCAN/Hilbert/Qd-tree layout, the
adaptive window and debt accounting against realized savings are not implemented.

## PTO

The paper (from its abstract): prunes partition and multi-column sort candidates using scan
selectivity, query frequency and sort cluster sizes, then samples the table and uses
gradient-boosted trees to choose partition columns, target file size, row-group size and
binpack/sort strategy jointly; Presto + Iceberg, TPC-H −11% and TPC-DS −36% average latency at
SF 10K.

Cryo (`cryo/optimizer/pto/`, shadow-only) searches the same four-parameter space with workload-
driven pruning, but scores candidates with a fixed weighted heuristic (`heuristic_v1`), not
sampled rewrites and a learned model. A faithful scorer needs a scratch namespace, sampled
rewrites per candidate and replayed queries, which is a research project with real cluster cost.

## What is safe to claim

- "Batch compaction selection implements AutoComp's MOOP ranking and budgeted top-k (Gruenheid et
  al., SIGMOD '25), with an optional, measured query-heat extension."
- "Delete-file compaction follows the minor/major strategy from Okolnychyi et al. (VLDB '24)."
- "Data-quality signals — freshness, arrival, row-count anomalies, null drift — come from Iceberg
  metadata without scanning data, filtering out maintenance commits, as in Verma et al. (FORMATS '26)."
- "Cryo recommends WAIR-style boundary reclustering (Liu et al., SIGMOD '26) with the paper's cost
  model, scoped to the files that straddle queried ranges."
- "Declarative data-quality constraints in the style of Deequ (Schelter et al., VLDB '18),
  decided from Iceberg metadata alone, with an explicit unknown when metadata can't decide."
- "Maintenance rewrites are tested with Pivoted Query Synthesis (Rigger & Su, OSDI '20) probes."
- "An Ernest-style (Venkataraman et al., NSDI '16) duration model, fit on Cryo's own rewrite
  history and used only when it beats the baseline on held-out runs." Don't claim Ernest's
  experiment design or cluster-size extrapolation.
- Not yet: "implements PTO". Say "searches PTO's layout space with a heuristic scorer".
