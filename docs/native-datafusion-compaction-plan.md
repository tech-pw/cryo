# Native DataFusion Compaction on Upstream Apache Iceberg-Rust — End-to-End Plan

> **Audience:** a mid-level engineer who will implement this without prior context on the
> reference systems. Read this top to bottom once, then use it as the working checklist.
>
> **Status:** design + implementation plan. Supersedes **Phase 3** of
> [`docs/native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md)
> (which correctly identified that `rewrite_data_files` execute was blocked on an upstream commit
> primitive). This document decides *how* we unblock it **using only upstream Apache
> iceberg-rust** — no third-party fork.
>
> **Hard constraint from the product owner (do not violate):**
> - Use **only upstream `apache/iceberg-rust`** + **DataFusion**. **Do NOT adopt the RisingWave
>   fork** that nimtable depends on.
> - The result must be **cost-aware** (must not increase S3 request/storage cost) and
>   **performant** (must not regress scan/plan performance). See [§5](#5-cost-aware--performance-best-practices).

---

## 0. TL;DR — the decision, in one screen

**Goal:** make Cryo's native Rust engine (`cryo-engine/`) perform real Iceberg **compaction**
(small-file bin-pack + optional sort) and **manifest rewrite** using **DataFusion** for the
read/rewrite and **upstream Apache iceberg-rust** for an **atomic ACID commit**, for both
**Copy-on-Write (COW)** and **Merge-on-Read (MOR)** tables — with **zero data loss or table
corruption** across single commits *and* concurrent writers.

**The crux:** compaction = "replace a set of old files with a set of new files **in one atomic
snapshot**". Upstream iceberg-rust `Transaction` does **not yet expose** an
overwrite/rewrite action on `main`. It *does* expose all the lower-level pieces
(`ManifestWriter::add_file`/`add_delete_file`/`add_existing_file`, `ManifestListWriter`,
`Catalog::update_table` with `TableRequirement::AssertRefSnapshotId`), and the community is
landing the exact action we need (`OverwriteAction`, PR
[#2185](https://github.com/apache/iceberg-rust/pull/2185)).

**Decision (see [§3.2](#32-the-commit-primitive-decision-upstream-only)):**

1. **COW compaction** (append-only + copy-on-write tables — the majority, and where the S3 cost
   wins are) ships **first**, committed via the Apache community `OverwriteAction`
   (`Operation::Overwrite`: add new data files, mark old data files deleted).
2. **MOR compaction** (tables with position/equality delete files) ships **second**, gated on the
   upstream **delete-file processing in `SnapshotProducer`** (#2367 / #2242) landing. Until then,
   MOR tables **route to Trino/Spark** (existing parity path) — never a half-correct native MOR
   commit.
3. We take a **hard dependency policy**: pin one exact upstream commit; obtain `OverwriteAction`
   the "most-upstream way available at build time" ([§3.2](#32-the-commit-primitive-decision-upstream-only)); **never** vendor the RisingWave fork.

**Capability matrix (target end state):**

| Table type | Read (apply deletes) | Rewrite (DataFusion) | Atomic commit primitive | Ships in |
|---|---|---|---|---|
| Unpartitioned / partitioned, **append-only** | trivial (no deletes) | bin-pack (+ optional sort) | `OverwriteAction` (replace data files) | **Phase C** |
| **COW** DELETE/UPDATE (no delete files) | trivial | bin-pack | `OverwriteAction` | **Phase C** |
| **MOR** with position deletes | DataFusion anti-join on `(file_path, pos)` | bin-pack, deletes applied | `OverwriteAction` + drop consumed position deletes | **Phase E** (upstream-gated) |
| **MOR** with equality deletes | DataFusion anti-join on eq-cols + `seq_num` | bin-pack, deletes applied | `OverwriteAction` + drop consumed equality deletes | **Phase E** (upstream-gated) |
| **v3** deletion vectors (Puffin) | reconcile DVs | bin-pack | as above | **Phase E+** (upstream-gated) |
| `optimize_manifests` | n/a | rewrite manifests | manifest-list rewrite | **Phase F** (upstream-gated) |

**Definition of Done (the whole project):** every scenario in the
[Testing Methodology (§7)](#7-testing-methodology--local-minio-iceberg-tables) passes green in CI,
including the concurrency and fault-injection cases, with the correctness **invariants (§4.4)**
never violated; and a native COW OPTIMIZE run on a fragmented local table measurably reduces file
count and per-scan S3 request count **without** increasing total stored bytes after
expiry+orphan. Only then do we flip native OPTIMIZE on by default.

---

## 1. Where Cryo is today (baseline)

`cryo-engine/` is a Rust subprocess the worker invokes with a JSON request over stdin. It pins
`apache/iceberg-rust` at git `6144e10` (crate version 0.9.0), Arrow 58, `object_store` 0.11, and
**does not use DataFusion** (that dependency lives only in the unrelated `cryo-native/` PyO3 crate).

Current operation status (from
[`docs/native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md)):

| Op | Analyze | Execute | Commits atomically? |
|---|---|---|---|
| `expire_snapshots` | ✅ preview | ✅ `Transaction::expire_snapshots` | ✅ metadata-only |
| `remove_orphan_files` | ✅ | ✅ `FileIO.delete` per file | n/a (object delete) |
| `rewrite_data_files` (OPTIMIZE) | ✅ candidate scan | ⚠️ **staged Parquet only, always `success:false`** | ❌ **blocked** |
| `rewrite_manifests` | ✅ count | ❌ blocked | ❌ |

The staged path (`cryo-engine/src/compaction_write.rs`) already reads candidate data files via
`FileIO` and writes bin-packed Parquet to `data/.cryo-compaction-staging/`, but **cannot commit**
and **does not apply deletes** (COW-only, no MOR). Worker routing currently **disconnects**
`OPTIMIZE`/`OPTIMIZE_MANIFESTS` (`NATIVE_SUPPORTED_OPERATIONS` in `cryo/engines/native/engine.py`),
auditing them `SKIPPED`.

Two known footguns we must fix while here:
- `compaction_write.rs` does `concat_batches(all_input)` in memory → **OOM / poor performance** on
  large partitions. The DataFusion pipeline must **stream** (bounded memory).
- Predicates already flow Python→Rust (`predicates.rs`); the commit path must **honor them exactly**
  and never widen scope (this is the whole reason the Cryo planner exists).

Cryo's **planner is the source of truth** (`cryo/planner/`): it decides *which* partitions/chunks
to compact and emits `Operation(OPTIMIZE, params, predicates)` with GB-budget + max-partitions caps.
Native must **consume** that plan, not re-plan.

---

## 2. How the reference systems compact (parity study)

### 2.1 Trino — `ALTER TABLE t EXECUTE optimize(...)`

- Syntax: `ALTER TABLE t EXECUTE optimize(file_size_threshold => '256MB') WHERE <partition predicate>`.
  Rewrites files **smaller than** `file_size_threshold` into ~target-size files; `WHERE` limits
  scope to partitions (Cryo's Trino renderer already emits exactly this — see `cryo/engines/trino/sql.py`).
- Internally Trino's Iceberg connector: (1) plans the file scan for the selected partitions,
  **applying position/equality deletes** for MOR tables during read; (2) writes new data files at
  the target size; (3) commits an Iceberg **`RewriteFiles`** operation that atomically **removes the
  old data files and the delete files now fully applied**, and **adds** the new data files.
- Atomicity: single metastore/REST commit with **optimistic concurrency** + **conflict validation**
  (it re-checks that no conflicting delete files were added to the scanned files between plan and
  commit; if so it fails rather than losing deletes). This is the ACID behavior we must match.
- `ALTER TABLE t EXECUTE optimize_manifests` compacts manifests.

**Takeaway for us:** Trino's `RewriteFiles` = "add new + remove old data files + remove consumed
deletes, atomically, with conflict validation." Our COW subset needs only "add new + remove old
data files" = `OverwriteAction`. Our MOR subset additionally needs "remove consumed deletes".

### 2.2 nimtable `iceberg-compaction` — and why we can't copy it wholesale

nimtable is the closest reference (Rust + DataFusion). Its architecture (studied from
`core/src/compaction/mod.rs`, `core/src/executor/datafusion/*`):

- **Plan** → `FileGroup`s of `FileScanTask`s (each task carries its referenced `deletes`).
- **Execute (DataFusion)** — `DatafusionProcessor` builds a **merge-on-read SQL** over the scan:
  ```
  SELECT <cols> FROM data
    RIGHT ANTI JOIN position_deletes ON data.sys_file_path = pd.sys_file_path AND data.sys_pos = pd.sys_pos
    RIGHT ANTI JOIN equality_deletes ON data.<eqcols> = ed.<eqcols> AND data.sys_seq_num < ed.sys_seq_num
  ```
  i.e. position deletes remove rows by `(file_path, pos)`; equality deletes remove rows by value
  **only where the data file's sequence number is lower than the delete's** (correct Iceberg MOR
  semantics). Output rows are re-written as clean data files (optionally sorted).
- **Commit** — `CommitManager.rewrite_files()` uses `txn.rewrite_files().add_data_files(new)
  .delete_files(old).set_enable_delete_filter_manager(true)...` then `txn.commit(catalog)`, with
  **exponential-backoff retry** on `CatalogCommitConflicts` and a **row-count `CompactionValidator`**
  (reads input-with-deletes vs output; asserts equal row counts) after commit.
- **Custom snapshot properties preserved**; snapshot `Operation::Replace`.

**Why we cannot reuse it directly:** nimtable's `Cargo.toml` pins
`iceberg = { git = "https://github.com/risingwavelabs/iceberg-rust.git", rev = "7e63517..." }`.
The `txn.rewrite_files()` action **and** `set_enable_delete_filter_manager(true)` **only exist in
the RisingWave fork** — they are **not in upstream** `apache/iceberg-rust`. Per the product
constraint, we **reject the fork**. We reuse nimtable's *ideas* (the MOR merge-on-read SQL, the
retry/validate/commit discipline, custom-property preservation) but implement the commit on the
upstream primitive.

### 2.3 Upstream `apache/iceberg-rust` — what's actually available (verified mid-2026)

Confirmed against `apache/iceberg-rust@main`:

- **Transaction actions on `main`** (`crates/iceberg/src/transaction/mod.rs`): `fast_append`,
  `expire_snapshots`, `replace_sort_order`, `update_schema`, `update_table_properties`,
  `update_location`, `update_statistics`, `upgrade_table_version`. **No `overwrite`/`rewrite`
  action merged yet.**
- **Public manifest primitives** (`crates/iceberg/src/spec/manifest/writer.rs`):
  `ManifestWriterBuilder` (`build_v2_data`, `build_v2_deletes`, `build_v3_data`, `build_v3_deletes`),
  and the **public** `ManifestWriter::add_file(...)` → `Added`, `add_delete_file(...)` → `Deleted`,
  `add_existing_file(...)` → `Existing`. Plus `ManifestListWriter`.
- **Catalog commit** (`crates/iceberg/src/catalog/mod.rs`): `Catalog::update_table(TableCommit)`;
  `TableRequirement` includes `AssertRefSnapshotId` (optimistic concurrency). `TableCommit` is
  normally produced *by a `Transaction`* (its constructor isn't a stable public API), so raw
  hand-rolled commits are **not** the recommended route.
- **In-flight, apache-native (NOT a fork):**
  - **`OverwriteAction` — PR [#2185](https://github.com/apache/iceberg-rust/pull/2185)** (open):
    adds a `TransactionAction` producing `Operation::Overwrite` — "adds new data files and optionally
    removes existing data files by rewriting affected manifests with entries marked
    `ManifestStatus::Deleted`." Also adds `ManifestWriter::add_deleted_entry()`,
    `SnapshotProducer::snapshot_id()`, and preserves delete-only manifests across appends.
    **This is exactly our COW commit primitive.** It is explicitly framed as "first in a series
    toward full CoW and MoR; subsequent PRs add `RowDeltaAction` (MoR write), scan-side delete
    reconciliation (MoR read), and compaction."
  - **Delete-file processing in `SnapshotProducer` — PR [#2367](https://github.com/apache/iceberg-rust/pull/2367) / issue [#2242](https://github.com/apache/iceberg-rust/issues/2242)** (in progress): the machinery to write/remove delete files in a snapshot. **This is the MOR gate.**
  - **`SnapshotProduceOperation` trait** already exists publicly in `transaction/snapshot.rs`
    (`operation()`, `existing_manifest()`, `delete_entries()`), i.e. the overwrite/replace machinery
    is designed-in upstream; only the public *action* wrapper is pending.
  - **EPIC [#2269](https://github.com/apache/iceberg-rust/issues/2269)** tracks all missing write
    actions (Overwrite, ReplacePartitions, DeleteFiles, RowDelta, RewriteFiles).
  - **DataFusion-as-execution-engine — issue [#3554](https://github.com/apache/iceberg-rust/issues/3554)** and bounded-memory execution EPIC [#2716](https://github.com/apache/iceberg-rust/issues/2716): upstream is moving toward DataFusion for compute-heavy ops. Our design aligns with the upstream direction.

**Net:** COW compaction is achievable on **upstream** now via `OverwriteAction`. MOR compaction is
achievable on **upstream** once delete-file processing lands. No fork required for either.

---

## 3. Target architecture (upstream-only)

### 3.1 Data flow

```
Cryo Planner (Python)                      cryo-engine (Rust subprocess, upstream iceberg-rust + DataFusion)
────────────────────                       ───────────────────────────────────────────────────────────────
Operation(OPTIMIZE,                         1. load_table(REST catalog + OpenDAL S3/MinIO)   [already works]
  params, predicates)  ──JSON stdin──▶      2. scope scan: table.scan().with_filter(predicate).plan_files()
                                            3. read via DataFusion:
                                                 - COW: register data files, stream RecordBatches
                                                 - MOR: register data + position/equality deletes, anti-join
                                            4. rewrite: bin-pack (+ optional sort) → RollingFileWriter →
                                                 new Parquet DataFiles (streamed, bounded memory)
                                            5. commit ONE atomic snapshot:
                                                 Transaction::new(table)
                                                   .overwrite()                      [OverwriteAction, #2185]
                                                   .add_data_files(new)
                                                   .delete_files(old_data_files)
                                                   .apply(txn).commit(catalog)       [AssertRefSnapshotId + retry]
                                            6. validate (row-count oracle) ; audit ; progress
```

Everything left of the arrow already exists. Everything right of the arrow is this project, built on
`cryo-engine`.

### 3.2 The commit-primitive decision (upstream-only)

We need `OverwriteAction`. Choose the **most-upstream option available when you start Phase C**, in
this preference order (all three are "apache iceberg only" — none is the RisingWave fork):

1. **Best — it merged.** If `apache/iceberg-rust` has merged `OverwriteAction` (track #2185 / EPIC
   #2269), pin `cryo-engine` to that exact commit SHA and call `tx.overwrite()` directly. Nothing to
   vendor. *This is the target; the interim options below exist only to avoid being blocked on the
   merge timeline.*
2. **Interim — carry the apache PR as a reviewed patch.** Pin a specific upstream commit SHA and
   apply **PR #2185's diff** (~560 lines, 5 files, Apache-2.0, apache-authored) as a
   `[patch.crates-io]`/git-patch or a small vendored module `cryo-engine/vendor/overwrite_action/`,
   **behind a Cargo feature flag** `native-compaction-commit`. This is materially different from the
   RisingWave fork: it is **apache community code headed for `main`**, small and reviewable, and has
   a **hard deletion trigger** (delete it the day #2185 merges — track in `docs/`/issue). Add a CI
   check that fails once upstream exposes `tx.overwrite()` so we don't forget to remove the patch.
3. **Last resort — build the commit from public manifest primitives.** Upstream publicly exposes
   `ManifestWriter::add_file/add_delete_file/add_existing_file`, `ManifestListWriter`, and
   `Catalog::update_table`. In principle we can construct the Overwrite snapshot ourselves. **Avoid
   this** unless (1) and (2) are impossible: hand-computing snapshot summaries, sequence numbers,
   and the `TableCommit`/`AssertRefSnapshotId` envelope re-implements `SnapshotProducer` and is the
   single most likely place to **corrupt a table**. If forced here, treat it as its own hardened
   sub-project and lean 100% on the [§7](#7-testing-methodology--local-minio-iceberg-tables) corruption oracles.

> **Rule:** the commit code path lives behind the `native-compaction-commit` feature and the runtime
> flag already present (`CRYO_NATIVE_COMPACTION_EXECUTE` / `destination.config.compaction_execute`).
> Default OFF until Phase C exit criteria are met.

### 3.3 Dependency & version policy

- Pin **one** upstream `apache/iceberg-rust` commit SHA for `iceberg`, `iceberg-catalog-rest`,
  `iceberg-storage-opendal`, **and** `iceberg-datafusion` (add this crate). Record the SHA in
  `cryo-engine/Cargo.toml` with a comment linking to the OverwriteAction status.
- Align **DataFusion + Arrow + parquet** versions to whatever that iceberg-rust SHA compiles against
  (do **not** guess; let `cargo` resolve, then commit `Cargo.lock`). Expect DataFusion 5x + Arrow 5x;
  this will **change** `cryo-engine`'s current Arrow-58 pin — re-validate `expire_snapshots` /
  `remove_orphan_files` on the new SHA (they use stable APIs and should be fine).
- Keep `cryo-native/` (PyO3) **out of scope** here; do not conflate the two Rust stacks (finding #12).
  If sort/z-order helpers are wanted later, fold them into `cryo-engine`'s DataFusion pipeline rather
  than the PyO3 crate.
- Reproducible Docker build stage compiles `cryo-engine` for the image arch (fixes finding #4/#16).

---

## 4. ACID & correctness model — how we guarantee "no loss, no corruption"

This is the heart of the project. Compaction is a **rewrite that must be invisible to readers**:
the set of live rows before and after must be identical.

### 4.1 Atomicity
- Exactly **one** `Transaction` → **one** `OverwriteAction` → **one** `catalog.update_table` per
  planned chunk. Either the new snapshot becomes current (all new files in, all old files out) or
  nothing changes. There is **no intermediate table state** a reader can observe.
- Never commit partial results across chunks. Each chunk (predicate scope) is its own atomic commit;
  a failure in chunk N does not roll back chunk N-1 (they are independent snapshots) but also never
  leaves a chunk half-applied.

### 4.2 Consistency
- Attach `TableRequirement::AssertRefSnapshotId(plan_snapshot_id)` (via the action's
  `set_check_file_existence` / starting-snapshot config, exactly like nimtable's
  `use_starting_sequence_number` path). The commit is rejected if the branch moved to a snapshot the
  planner didn't see.
- **Conflict validation (MOR-critical):** before committing, re-verify that **no new delete files**
  were added to the data files we rewrote between plan-snapshot and commit-snapshot. If a concurrent
  writer added deletes to a file we're replacing, committing our rewrite would **resurrect deleted
  rows** = data corruption. On conflict → **abort** (do not commit) and let the next scheduled run
  re-plan. (Trino does exactly this; it's the `SnapshotValidator` concern, upstream #2243.)
- Schema/spec stability: assert `current_schema_id` and default partition-spec id are unchanged
  between plan and commit; abort otherwise.

### 4.3 Isolation
- **Optimistic concurrency:** upstream commit uses compare-and-swap on the branch ref. On
  `CatalogCommitConflicts`, **retry** with exponential backoff (reload table → re-validate → re-apply
  → commit), capped (e.g. 3–5 tries). Match nimtable's `CommitManagerRetryConfig`.
- **Per-table lock** (Cryo already has this in the worker) prevents *our own* concurrent maintenance
  on the same table; the CAS handles *external* writers.

### 4.4 Durability + the correctness **invariants** (the test oracles)
After a successful compaction commit, **all** of these must hold (these are the assertions the
test suite enforces — see §7):

1. **Row-count invariant:** live row count (deletes applied) is identical before and after. This is
   nimtable's `CompactionValidator` oracle and our primary safety net.
2. **Row-content invariant:** the multiset of live rows (all columns) is identical before and after
   (stronger than count; catch dup/drop/reorder-into-wrong-file bugs).
3. **No dangling references:** every `DataFile`/delete file referenced by the new snapshot's
   manifests physically exists in object storage.
4. **No orphaned live data:** no old data file that still holds *live* rows outside our rewrite scope
   was dropped. (Only files fully within scope are removed.)
5. **Snapshot lineage intact:** the new snapshot's `parent_snapshot_id` = the snapshot we planned
   from (or the retried-from snapshot); `Operation` is `Overwrite`/`Replace`; time-travel to the old
   snapshot still reads the old (pre-compaction) data.
6. **Schema/partition-spec/sort-order unchanged**; **custom snapshot properties preserved**
   (filter the auto-computed summary keys, like nimtable's `custom_snapshot_properties`).
7. **Delete-file correctness (MOR):** a delete file is dropped **only if** every data file it
   referenced was rewritten in this commit; otherwise it is retained. No live-but-deleted row
   reappears.
8. **Failure = no-op:** if any step fails (read, write, validate, commit conflict exhausted), the
   table is byte-for-byte unchanged; any Parquet we wrote becomes an orphan removable by
   `remove_orphan_files`.

> If you can't assert an invariant cheaply in production, assert it in tests. #1, #3, #7, #8 are
> cheap enough to run **in-process before every real commit** (guard rails); #2, #4, #5, #6 run in
> the test suite and periodically in a "deep validation" mode.

---

## 5. Cost-aware & performance best practices

The product owner requires that compaction **reduce**, not increase, cost. Compaction inherently
*spends* IO to *save* IO later, so we must be disciplined. Bake these into the design and the
planner params; each is testable in §7.6.

**Read/scan cost**
- **Never scan the whole table on a request path.** Always scope by the planner's partition
  predicate (`table.scan().with_filter(pred)`). Full-table `plan_files()` on a large table is a huge
  S3 LIST/GET bill and defeats the point.
- **Use pre-computed scan stats** (Cryo's scanner) to decide *whether* to compact; do **not** walk
  live `$files`/manifests on the hot path (finding: parity checklist §5).
- **Push down projection** to Parquet (only read columns needed); for MOR, only read the delete
  columns needed for the anti-joins.

**Write / storage cost**
- **Target file size** from table property `write.target-file-size-bytes` (fallback to planner
  `target_file_size_bytes`, default 128–512 MB). Fewer, larger files ⇒ fewer GET/LIST per future
  scan ⇒ lower query cost and better throughput. This is the main cost win.
- **`min-input-files` gate:** only compact a partition/group with enough small files to be worth it
  (Cryo planner `min_small_files`, default 128). Never rewrite a partition that's already optimal —
  that's pure write amplification (extra PUT + read for no benefit).
- **Don't re-compact big files:** candidates are files **below** `file_size_threshold` only
  (matches Trino `optimize(file_size_threshold => ...)`). Large files pass through untouched.
- **Avoid write amplification over time:** honor **cooldown** and **partition-idle-hours** (Cryo
  planner already emits these) so we don't recompact partitions that are still being written or were
  just compacted.

**Commit / metadata cost**
- **Bounded commit blast radius:** honor the planner's **GB-budget per chunk** and
  **max-partitions-per-chunk** caps. Smaller commits = smaller manifests, fewer CAS conflicts, cheaper
  retries, and less metadata churn.
- **Manifest hygiene:** compaction reduces data-file count; pair with `optimize_manifests` (Phase F,
  upstream-gated) to keep manifest count low → cheaper planning (fewer manifest GETs).

**The storage-transient rule (important, cost-aware):**
- A compaction commit **adds** new files and only **logically** removes old ones; the old files stay
  physically present for **time travel** until `expire_snapshots` + `remove_orphan_files` run.
  So compaction **temporarily increases** stored bytes. Cost-aware operation means: sequence
  `expire_snapshots → remove_orphan_files` after compaction (Cryo's DEEP mode already orders these),
  and communicate the transient in `docs/cost-savings.md` accounting. A compaction run measured
  *without* the subsequent expiry will look like it increased storage — that's expected; measure net
  cost **after** the maintenance cycle completes.

**Performance / memory**
- **Stream, don't buffer.** Replace the current `concat_batches(all_input)` (`compaction_write.rs`)
  with a DataFusion streaming plan + `RollingFileWriter` that rolls a new output file at target size.
  This keeps memory **bounded** regardless of partition size (aligns with upstream bounded-memory
  EPIC #2716) and avoids OOM on large partitions.
- **Concurrency caps:** rewrite chunks with `buffer_unordered(max_concurrent)` (nimtable pattern),
  bounded by the Governor's real capacity — don't rely on worker replica count.
- **SNAPPY/ZSTD** and sensible row-group sizes on output Parquet (match table write properties).
- **Reuse the OpenDAL/object_store client**; use multipart upload for large outputs; avoid many tiny
  PUTs (they cost per-request).

**Cost accounting hooks**
- Emit `input_files`, `output_files`, `bytes_read`, `bytes_written`, and estimated
  request-count delta per run (extend `OperationResult`; wire into `docs/s3-request-cost-tracking.md`).
  A run that doesn't reduce projected per-scan request count is a planner bug, not a success.

---

## 6. Phased implementation plan

Each phase leaves the repo in a correct, shippable state. **Do not start a phase until the previous
phase's exit criteria are green.** "DONE WHEN" is the objective completion signal.

### Phase A — Foundations: upstream pin, DataFusion, reproducible build (no behavior change)

**Objective:** `cryo-engine` compiles against a pinned upstream iceberg-rust SHA **with DataFusion +
`iceberg-datafusion`**, expire/orphan still pass, build is reproducible in CI/Docker.

Tasks:
1. Choose the pinned `apache/iceberg-rust` commit SHA (see §3.2/§3.3). Add `iceberg-datafusion` at
   the same SHA. Add `datafusion` at the compatible version. Let cargo resolve Arrow/parquet; commit
   `Cargo.lock`.
2. Re-validate `expire_snapshots` and `remove_orphan_files` on the new SHA (fix any API drift).
3. Multi-stage Dockerfile that **builds** `cryo-engine` for the image arch; worker startup
   health-check `cryo-engine --version` spawnable (fixes findings #4/#16).
4. Add the `native-compaction-commit` Cargo feature (empty for now) and keep runtime flags OFF.

**Exit criteria / DONE WHEN:** `cargo build --release` + existing Rust unit tests + the existing
MinIO e2e (`make test-native-e2e-local` for expire/orphan) pass on the new pin; Docker image builds
`cryo-engine` from scratch; CI is green.

### Phase B — COW read + rewrite pipeline with DataFusion (no commit)

**Objective:** given a predicate-scoped set of data files, produce correct, target-sized output
Parquet via a **streaming** DataFusion plan. No table mutation yet.

Tasks:
1. New module `cryo-engine/src/datafusion/mod.rs`: build a `SessionContext`, register the scoped
   `FileScanTask`s (from `table.scan().with_filter(pred).plan_files()`) as a DataFusion table
   provider backed by `iceberg::arrow::ArrowReaderBuilder` (reuse `iceberg-datafusion`'s scan/table
   provider so delete-application comes "for free" later).
2. Rewrite pipeline: stream batches → `RollingFileWriterBuilder` + `DataFileWriterBuilder` +
   `ParquetWriterBuilder`, rolling at `target-file-size-bytes`. Optional sort (table sort order or
   planner `sort_columns`).
3. Return the produced `Vec<DataFile>` + stats (`input_files`, `output_files`, `bytes_read`,
   `bytes_written`). Replace the in-memory `concat_batches` staging path.
4. Honor predicates: assert the produced files' partition values are all within the requested scope
   (guard rail: never widen scope).

**Exit criteria / DONE WHEN:** unit + local tests show that, for an append-only partitioned table,
the pipeline reads only the scoped partitions, writes ≥1 target-sized file per group, memory stays
bounded on a synthetic large partition (e.g. 2 GB) — and a byte-level read of outputs equals the
inputs' rows (row-content invariant #2), all **without** touching the table.

### Phase C — Atomic COW commit via `OverwriteAction`

**Objective:** ship real, atomic, ACID COW compaction for append-only / copy-on-write tables.

Tasks:
1. Obtain `OverwriteAction` per §3.2 (merged, or vendored apache PR #2185 behind the feature flag).
2. `cryo-engine/src/commit.rs`: `commit_overwrite(table, added: Vec<DataFile>, removed: Vec<DataFile>,
   plan_snapshot_id)`:
   - `Transaction::new(&table).overwrite().add_data_files(added).delete_files(removed)` with
     starting-snapshot / `AssertRefSnapshotId` + `check_file_existence(true)`.
   - Preserve custom snapshot properties (port `custom_snapshot_properties`).
   - `txn.commit(catalog)` with **exponential-backoff retry** on `CatalogCommitConflicts`
     (reload → re-validate → re-apply → commit).
3. **Pre-commit guard rails** (invariants #1, #3, #7-COW-trivial, #8): row-count check
   (input == output), file-existence check, schema/spec-id stability, conflict validation (no new
   deletes on scoped files → for COW tables there are no deletes, but still assert none appeared).
4. `rewrite_data_files` execute mode: analyze → read (Phase B) → commit (this phase) → validate →
   audit. On any failure: commit nothing; report `success:false` with a clear reason; leave staged
   files for orphan removal.
5. Distinct `input_files` vs `output_files` + bytes in the result (fixes finding #13).

**Exit criteria / DONE WHEN:** the §7 **COW scenario matrix** (single & multi-chunk, partitioned &
unpartitioned, small-table bypass, oversize-alone, concurrent-append conflict/retry, mid-run failure)
passes green with **all invariants (§4.4)** holding, on local MinIO. A fragmented table's file count
drops and per-scan GET count drops; stored bytes drop **after** expiry+orphan.

### Phase D — Worker wiring: re-enable native OPTIMIZE for COW

**Objective:** the platform routes COW OPTIMIZE to native by default, safely.

Tasks:
1. Add `OperationType.OPTIMIZE` to `NATIVE_SUPPORTED_OPERATIONS` **only for COW tables** — detect MOR
   (any delete files present in the target snapshot / `write.delete.mode=merge-on-read`) and keep
   those routed to Trino/Spark (`NATIVE_DISCONNECTED_OPERATIONS`) until Phase E.
2. Thread predicates + params fully (finding #2/#18 are already fixed; re-verify end-to-end).
3. Multi-op single subprocess (finding #23): one request executes the ordered plan atomically per op.
4. Progress streaming to run console; per-op audit rows with the new counters (findings #7/#8/#14).
5. Governor: native capacity from live in-flight accounting (finding #9).

**Exit criteria / DONE WHEN:** a DEEP run on a COW table via the worker completes: expiry + orphan +
**native COW optimize** + (Trino) manifests, with correct audit metrics and progress; MOR tables in
the same run still route optimize to Trino and are clearly labeled.

### Phase E — MOR compaction (upstream-gated)

**Objective:** native compaction for tables with position/equality delete files, applying deletes on
read and dropping consumed deletes on commit.

**Gate:** upstream delete-file processing in `SnapshotProducer` (#2367 / #2242) is merged/pinnable.
Until then, MOR stays on Trino/Spark (Phase D behavior). Do **not** implement a half-correct MOR
commit.

Tasks (once gated):
1. Read path: extend the Phase B DataFusion pipeline with the **merge-on-read anti-joins**
   (position deletes on `(file_path, pos)`; equality deletes on eq-cols **and**
   `data.seq_num < delete.seq_num`). Reuse `iceberg-datafusion`'s reader if it applies deletes;
   otherwise implement the anti-join plan (nimtable's SQL is the reference; the logic, not the fork).
2. Commit path: `overwrite()`/rewrite that additionally marks **fully-consumed delete files**
   deleted (a delete file is consumed iff all data files it referenced are in the rewrite scope).
   Enforce invariant #7 as a hard pre-commit guard.
3. Conflict validation is now **mandatory**: abort if new deletes appeared on scoped data files
   between plan and commit (§4.2).
4. v3 deletion-vector (Puffin) handling if the pinned upstream supports it; else keep v3-with-DV
   tables on Trino/Spark and document.

**Exit criteria / DONE WHEN:** the §7 **MOR scenario matrix** (position-only, equality-only, mixed,
overlapping deletes, delete-only partitions, concurrent-delete conflict) passes green with invariants
#1, #2, #7 holding; a MOR table's live rows are identical before/after and no deleted row reappears.

### Phase F — manifests, sort/z-order, ops hardening (upstream-gated where noted)

Tasks: `optimize_manifests` via manifest-list rewrite (gate on upstream support, issue #2556-class);
optional sort/z-order in the DataFusion pipeline; docs truth-in-advertising (finding #15); split
`cryo-engine` vs `cryo-native` naming/tests (finding #12); cost-report wiring
(`docs/s3-request-cost-tracking.md`, `docs/cost-savings.md`).

**Exit criteria / DONE WHEN:** native is the default DEEP workflow for COW (and MOR if E landed),
docs match implemented behavior for the pinned SHA, and the cost report shows net reduction across a
full maintenance cycle.

---

## 7. Testing methodology — local MinIO Iceberg tables

**Principle:** compaction is only "possible" (per the product owner) when **every** scenario below is
green, including concurrency and fault injection, with **no invariant (§4.4) ever violated**. This
section is the acceptance gate. Build it incrementally alongside the phases (COW scenarios gate
Phase C/D; MOR scenarios gate Phase E).

### 7.1 Environment (all local, no cloud)

Reuse the existing harness:
- `docker-compose.dev-datalake.yml`: **MinIO** (S3), `minio-init` (bucket), **iceberg-rest** catalog,
  and Trino/Spark (for differential tests).
- `make up-datalake-native` (MinIO + REST) and `make up-datalake` (adds Trino/Spark).
- Fixtures via `tests/integration/native/rest_env.py` + a new
  `tests/integration/native/generate_compaction_fixtures.py`.
- Local object-store assertions via boto3 listing (`list_object_sizes`) — already present.
- `cargo build --release` in `cryo-engine/` produces the binary the Python harness spawns.
- New make target `make test-native-compaction-e2e-local` (and CI job `native-compaction-e2e`)
  mirroring the existing `native-e2e` job.

### 7.2 Fixture table generator (build these table variants)

Write a generator (PyIceberg for setup, Spark for MOR-delete setup where PyIceberg can't) that
produces, on the local REST catalog + MinIO, each of:

| Fixture | Shape | Purpose |
|---|---|---|
| `f_unpart_small` | unpartitioned, N tiny files | small-table bypass, basic bin-pack |
| `f_part_small` | partitioned by `event_date` (day), many tiny files/partition | partition-scoped compaction |
| `f_part_skew` | one huge partition + many small | oversize-alone + budget caps |
| `f_mixed_sizes` | files above & below threshold | only-small-files-compacted |
| `f_empty_partition` | a partition with 0 rows / only deletes | delete-only edge case |
| `f_cow_deletes` | COW DELETE applied (rewrote data files, no delete files) | COW correctness |
| `f_mor_pos` | MOR position deletes | MOR position path |
| `f_mor_eq` | MOR equality deletes | MOR equality + seq-num |
| `f_mor_mixed` | both position & equality deletes | MOR mixed |
| `f_schema_evolved` | added/renamed/dropped column across snapshots | schema-safety |
| `f_partition_evolved` | partition spec changed across snapshots | spec-safety |
| `f_nested` | struct/map/list columns | nested-type read/write |
| `f_types` | date/timestamp/decimal/bool/binary/UUID | type round-trip |
| `f_v3_dv` | format v3 with deletion vectors (if supported) | v3 handling/gating |
| `f_multi_snapshot` | ≥5 snapshots | time-travel preservation |
| `f_custom_props` | snapshots with custom summary properties | property preservation |

### 7.3 The invariant oracles (assert on every scenario)

Implement these as reusable assertions (Python side, reading via PyIceberg/Trino + boto3):

- **O1 Row count:** `count(*)` (deletes applied) equal before/after. *(cheap; also a pre-commit guard)*
- **O2 Row content:** full-table (or scoped-partition) row multiset equal before/after — hash each
  row, compare sorted multisets. Catches drops, dups, corruption, wrong-partition writes.
- **O3 Physical existence:** every data/delete file in the new snapshot's manifests exists in MinIO.
- **O4 No live-data loss:** files removed by the commit were entirely within the requested predicate
  scope; files outside scope are byte-identical and still referenced.
- **O5 Lineage/time-travel:** new snapshot parent = planned snapshot; `Operation=Overwrite/Replace`;
  reading `AS OF` the old snapshot returns the old data unchanged.
- **O6 Metadata stability:** schema id, partition-spec id, sort-order id unchanged; custom snapshot
  properties preserved; auto-computed summary counters (`total-records`, etc.) recomputed correctly.
- **O7 MOR delete correctness:** deleted rows do **not** reappear; delete files dropped only when
  fully consumed; retained delete files still reference live data files.
- **O8 File-size effect:** output files ≤ `1.2 × target_file_size` (with a small tail file allowed);
  small-file count strictly decreases.
- **O9 Failure = no-op:** after an injected failure, the current snapshot id is unchanged and O2
  holds; any written Parquet is an orphan (removable by `remove_orphan_files`).

### 7.4 Scenario matrix (functional)

Run each applicable fixture through analyze **and** execute, asserting the relevant oracles:

1. **Bin-pack, unpartitioned** (`f_unpart_small`) → O1,O2,O3,O5,O6,O8.
2. **Partition-scoped** (`f_part_small`, predicate on one day) → O1,O2,O4 (other partitions
   untouched),O8. Verify **only** the scoped partition's files changed.
3. **Multi-chunk** (`f_part_small`, several predicates) → each chunk is its own atomic snapshot;
   after all, O1,O2 over the whole table.
4. **Oversize-alone + budget cap** (`f_part_skew`) → oversize partition committed alone; caps honored.
5. **Small-table bypass** (`f_unpart_small` below `chunk_size_gb`) → single full-table optimize.
6. **Only-small-files** (`f_mixed_sizes`) → large files pass through untouched (O4), small ones packed.
7. **Empty/delete-only partition** (`f_empty_partition`) → no crash; correct no-op or clean rewrite.
8. **COW deletes** (`f_cow_deletes`) → O1,O2,O7-trivial.
9. **MOR position** (`f_mor_pos`) → deletes applied, position-delete files consumed & dropped; O1,O2,O7.
10. **MOR equality** (`f_mor_eq`) → eq deletes applied only where `seq_num` lower; O1,O2,O7.
11. **MOR mixed** (`f_mor_mixed`) → O1,O2,O7.
12. **Schema evolved** (`f_schema_evolved`) → read/write with current schema; O2 with projection;
    O6 (compaction must not change schema).
13. **Partition evolved** (`f_partition_evolved`) → compaction operates within a single spec; O4,O6.
14. **Nested & wide types** (`f_nested`, `f_types`) → O2 exact round-trip (no type/precision loss).
15. **Time-travel preserved** (`f_multi_snapshot`) → O5 after compaction.
16. **Custom properties** (`f_custom_props`) → O6 property preservation.
17. **Idempotence:** run optimize twice; second run is a near no-op (nothing left below threshold),
    no wasted rewrite (O8 + write-amplification check §7.6).

### 7.5 Concurrency & fault injection (the corruption hunters)

These are where tables actually break. Each must end with the table valid (O2, O9):

- **C1 Concurrent append during compaction:** start compaction on a snapshot; append new files (via
  Trino/PyIceberg) before our commit. Expect: our commit either succeeds after retry **without
  dropping the concurrent append**, or aborts cleanly. Assert the concurrent rows survive (O2).
- **C2 Concurrent DELETE during MOR compaction:** add a delete file to a scoped data file mid-flight.
  Expect **abort** (conflict validation §4.2), never a resurrected deleted row (O7).
- **C3 Concurrent overwrite/compaction:** two compactions on overlapping scope. Expect one wins, the
  other aborts on CAS; no double-remove of the same files, no loss (O2).
- **C4 Commit conflict retry exhaustion:** force N conflicts; assert clean failure + no-op (O9).
- **C5 Crash between write and commit:** kill `cryo-engine` after writing Parquet, before commit.
  Assert O9 (table unchanged; new Parquet is orphan) and that a subsequent `remove_orphan_files`
  cleans it.
- **C6 Crash mid-commit / catalog error injection:** simulate REST 500 during `update_table`. Assert
  atomicity — either the snapshot fully applied or not at all (O9); never a partial manifest set.
- **C7 Object-store partial failure:** inject a failed PUT for one output file. Assert the whole
  chunk aborts, no snapshot is created referencing a missing file (O3).
- **C8 Duplicate/late retry:** ensure a retried commit can't apply the same overwrite twice
  (idempotency of the CAS envelope).

Implement injection via: a wrapper catalog/FileIO in a Rust integration test that fails on the Nth
call; and, at the Python e2e level, by interleaving Trino/PyIceberg writes with the native run.

### 7.6 Cost & performance assertions (§5 is testable)

- **P1 Scope containment:** compaction reads only the scoped partitions' files (assert via S3
  request log / object-access count); a full-table read on a partitioned fixture is a failure.
- **P2 Request-count reduction:** number of GET requests to scan the compacted partition **decreases**
  post-compaction (fewer, larger files). Compare a Trino scan's split count before/after.
- **P3 No storage growth after cycle:** stored bytes for the table **decrease** after
  compaction → expire_snapshots → remove_orphan_files (the storage-transient rule §5). A test that
  measures storage *without* the cycle is expected to show a transient increase — assert net decrease
  only after the full cycle.
- **P4 Bounded memory:** compacting `f_part_skew`'s 2 GB partition keeps `cryo-engine` RSS under a
  cap (e.g. < 1 GB) — proves streaming, not `concat_batches`.
- **P5 No write amplification:** the idempotence run (§7.4.17) writes ~0 new bytes and removes ~0
  files (nothing to do).

### 7.7 Differential testing vs Trino/Spark (ground truth)

For each fixture, run the **same** logical optimize via Trino (`ALTER TABLE ... EXECUTE optimize`)
on a **clone** and via native on another clone; assert both clones have **identical live row
multisets** (O2) and comparable file-count reduction. Trino is the trusted oracle for "correct
compaction." This is the strongest guarantee that our upstream-only commit matches established
engines.

### 7.8 CI gates & the "possible" bar

- Rust: `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` (unit incl. commit/manifest
  math + injection tests).
- Python: `make lint typecheck test` incl. payload/predicate/routing unit tests.
- E2E job `native-compaction-e2e`: brings up MinIO+REST(+Trino for differential), builds
  `cryo-engine`, runs §7.4 COW matrix + §7.5 C1/C4/C5/C6 + §7.6 + §7.7. MOR matrix + C2/C3 gated on
  Phase E.
- **Ship rule:** native OPTIMIZE default-ON only after the COW gate is green for ≥N consecutive CI
  runs (no flakes) and a manual "deep validation" (O2 over all fixtures) passes. MOR default-ON only
  after the MOR gate + C2/C3 are green.

---

## 8. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Upstream `OverwriteAction` (#2185) slips | COW native delayed | Interim vendored apache PR behind feature flag (§3.2 option 2), hard removal trigger; meanwhile Trino handles optimize |
| MOR delete-processing (#2367) not merged | No native MOR | Keep MOR on Trino/Spark; do **not** ship half-correct MOR commit (invariant #7) |
| Hand-rolled commit corrupts a table | Data loss | Prefer §3.2 option 1/2; if forced to option 3, gate on the **entire** §7 suite incl. differential vs Trino |
| Version churn (DataFusion/Arrow) on new pin breaks expire/orphan | Regression | Phase A re-validates on the exact pin; `Cargo.lock` committed; CI e2e |
| Concurrent writers → resurrected deletes | Corruption | Mandatory conflict validation (§4.2) + CAS retry + per-table lock; C2/C3 tests |
| Compaction increases cost if run wrong | Cost regression | §5 gates + §7.6 P1–P5; measure net cost only after full maintenance cycle |
| OOM on large partitions | Crash / perf | Streaming DataFusion + RollingFileWriter; P4 test |

---

## 9. References

- Cryo current state: [`docs/native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md);
  `cryo-engine/src/{operations,compaction,compaction_write,predicates,orphans}.rs`;
  `cryo/engines/native/{engine,runner,payload}.py`; `cryo/planner/`; `cryo/engines/trino/sql.py`,
  `cryo/engines/spark/sql.py`.
- Upstream apache/iceberg-rust: EPIC [#2269](https://github.com/apache/iceberg-rust/issues/2269);
  `OverwriteAction` PR [#2185](https://github.com/apache/iceberg-rust/pull/2185);
  delete-file processing PR [#2367](https://github.com/apache/iceberg-rust/pull/2367) / issue
  [#2242](https://github.com/apache/iceberg-rust/issues/2242); `RewriteFilesAction` PR
  [#1606](https://github.com/apache/iceberg-rust/pull/1606) (draft/closed) / issue
  [#2244](https://github.com/apache/iceberg-rust/issues/2244); DataFusion engine issue
  [#3554](https://github.com/apache/iceberg-rust/issues/3554); bounded-memory EPIC
  [#2716](https://github.com/apache/iceberg-rust/issues/2716);
  `crates/iceberg/src/spec/manifest/writer.rs`, `crates/iceberg/src/transaction/{mod,snapshot}.rs`,
  `crates/iceberg/src/catalog/mod.rs`.
- Reference (studied, **not** depended on): nimtable
  [`iceberg-compaction`](https://github.com/nimtable/iceberg-compaction) — `core/src/compaction/mod.rs`
  (CommitManager/retry/validation), `core/src/executor/datafusion/*` (merge-on-read anti-joins). It
  depends on the RisingWave iceberg-rust fork, which we do **not** use.
- Trino: `ALTER TABLE ... EXECUTE optimize` / `optimize_manifests`
  ([trinodb/trino](https://github.com/trinodb/trino)).

## Appendix — glossary

- **COW (Copy-on-Write):** DELETE/UPDATE rewrites whole data files; no delete files. Compaction only
  replaces data files.
- **MOR (Merge-on-Read):** DELETE/UPDATE writes *delete files* (position or equality); readers apply
  them at query time. Compaction must apply deletes on read and drop consumed delete files on commit.
- **Position delete:** removes a row by `(data_file_path, row_position)`.
- **Equality delete:** removes rows matching column values, applied to data files with **lower**
  sequence number than the delete.
- **OverwriteAction / RewriteFiles:** the atomic snapshot that adds new files and removes old ones.
- **Optimistic concurrency (CAS):** commit asserts the branch is still at the expected snapshot id;
  retries on conflict.
- **Storage transient:** compaction temporarily increases stored bytes until expiry+orphan reclaim
  the old files.
