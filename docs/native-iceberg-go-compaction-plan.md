# Native Compaction on Apache **iceberg-go** — End-to-End Migration Plan

> **Audience:** a mid-level engineer who will implement this without prior context on the reference
> systems. Read this top to bottom once, then use it as the working checklist. Every phase has a
> **"DONE WHEN"** so you always know when you're finished.
>
> **Status:** design + implementation plan. **Supersedes** the Rust-based headline compaction path in
> [`docs/native-datafusion-compaction-plan.md`](./native-datafusion-compaction-plan.md) and unblocks
> the work that stalled in Phase 3 of
> [`docs/native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md) (compaction
> blocked upstream on `apache/iceberg-rust` write actions, EPIC #2269 / #2367 / #2556).
>
> **Why this document exists:** `apache/iceberg-rust` (pinned at `6144e10`, crate 0.9.0) still has **no
> atomic overwrite/rewrite commit action** and **no delete-file processing in `SnapshotProducer`**, so
> our Rust `cryo-engine` can *stage* compacted Parquet but **cannot commit**, and **cannot do MOR at
> all**. `apache/iceberg-go` (v0.6.x, mid-2026) **already ships** the exact primitives we need:
> `Transaction.RewriteDataFiles` compaction that reads data **with position/equality deletes applied**,
> writes clean files, and **removes the consumed delete files in one atomic `Replace` commit**, plus
> optimistic-concurrency conflict detection with retry. That is the whole reason to migrate.

---

## 0. TL;DR — the decision, in one screen

**Goal:** make Cryo's native engine perform real, atomic, ACID Iceberg **compaction** (small-file
bin-pack + optional sort) for both **Copy-on-Write (COW)** and **Merge-on-Read (MOR)** tables — with
**zero data loss or table corruption** across single commits *and* concurrent writers — by replacing
the Rust `cryo-engine` subprocess with a **Go subprocess built on `apache/iceberg-go`**.

**The crux:** compaction = "replace a set of old files with a set of new files **in one atomic
snapshot**, applying (and dropping) any delete files that covered them." `apache/iceberg-rust` does
**not** expose this yet. `apache/iceberg-go` **does**, today, as `Transaction.RewriteDataFiles`.

**Decisions (justified in [§3](#3-technical-challenge-evaluation-iceberg-go-vs-iceberg-rust) and [§4](#4-target-architecture)):**

1. **Keep the subprocess boundary and its JSON protocol byte-for-byte.** The Python worker already
   talks to a subprocess (`cryo-engine execute`, JSON on stdin, `ExecuteResponse` on stdout,
   `CRYO_PROGRESS:` progress lines). We build a **new Go binary that speaks the exact same protocol**,
   so the Planner → Worker → audit contract does **not** change. This makes the migration a
   *drop-in engine swap*, not a platform rewrite.
2. **iceberg-go does the whole job natively — DataFusion is NOT on the critical path.** iceberg-go's
   scan applies deletes on read, `RewriteDataFiles` bin-packs and commits atomically, and sort-order
   enforcement on write is built in. DataFusion in Go only exists as an **immature cgo bridge**
   (`datafusion-contrib/datafusion-go`, DataFusion 53, requires cgo + a downloaded native `.so`).
   **We keep the default build pure-Go and CGO-free**, and treat DataFusion as an **optional, isolated,
   later** capability for advanced global-sort / z-order only (see [§4.5](#45-the-datafusion-question-answered-honestly) and [Appendix D](#appendix-d--datafusion-integration-options-if-ever-needed)).
3. **Ship in order of blast-radius:** expire/orphan parity first (prove the swap), then COW compaction,
   then MOR compaction, then manifests. **`optimize_manifests` is the one iceberg-go gap** (blank in its
   feature matrix, same gap as rust) → stays routed to Trino/Spark until we implement or upstream lands
   it ([§7 Phase F](#phase-f--manifests-sortz-order-and-hardening)).

**Capability matrix (target end state, all on upstream `apache/iceberg-go`):**

| Table type | Read (apply deletes) | Rewrite | Atomic commit primitive | Ships in |
|---|---|---|---|---|
| Unpartitioned / partitioned, **append-only** | trivial (no deletes) | `RewriteDataFiles` bin-pack (+ optional sort) | `Replace` snapshot (`ReplaceDataFilesWithDataFiles`) | **Phase B** |
| **COW** DELETE/UPDATE (no delete files) | trivial | bin-pack | `Replace` snapshot | **Phase B** |
| **MOR** position deletes | iceberg-go scan applies `(file_path, pos)` deletes | bin-pack, deletes applied | `Replace` + **drop consumed position deletes** (#851) | **Phase D** |
| **MOR** equality deletes | iceberg-go scan applies eq-col + `seq_num` deletes | bin-pack, deletes applied | `Replace` + **drop consumed equality deletes** (#851) | **Phase D** |
| **v3** deletion vectors (Puffin) | read supported; **write partitioned in progress (#1135)** | bin-pack | as above where supported | **Phase D+** (partial-gated) |
| `optimize_manifests` | n/a | rewrite manifests | **not in iceberg-go** → route to Trino/Spark, or build from `Create New Manifests` | **Phase F** |

**Definition of Done (the whole project):** every scenario in the
[Testing Methodology (§8)](#8-testing-methodology--local-minio-iceberg-tables) passes green in CI —
including the concurrency and fault-injection cases — with the correctness **invariants ([§5.4](#54-durability--the-correctness-invariants-the-test-oracles))**
never violated, and differential tests confirm the Go engine produces an **identical live-row multiset**
to Trino/Spark on every fixture. Only then do we flip native OPTIMIZE on by default and retire the Rust
`cryo-engine`.

---

## 1. Where Cryo is today (baseline you are replacing)

The native engine is a **Rust subprocess** the Python worker invokes. You are **not** touching the
Planner, Governor, Scheduler, Worker orchestration, API, or UI — only the thing behind the subprocess
boundary.

### 1.1 The pieces (all live today)

| Layer | Path | Role |
|---|---|---|
| Rust subprocess | `cryo-engine/src/{main,operations,compaction,compaction_write,predicates,orphans,catalog_preview,progress}.rs` | Executes maintenance ops; pins `apache/iceberg-rust @ 6144e10` (crate 0.9.0), Arrow 58, `object_store` 0.11. **No DataFusion.** |
| Python adapter | `cryo/engines/native/{engine,runner,payload,render}.py` | `NativeEngine` spawns the binary, builds the JSON request from the plan, maps results back to `ExecResult`/audit. |
| Engine interface | `cryo/engines/base.py` | `MaintenanceEngine` protocol: `probe_capacity / render / session_setup / execute / execute_plan`. |
| Planner (source of truth) | `cryo/planner/` | Emits `MaintenancePlan(operations=[Operation(type, params, predicates)])`. **Engine must consume, never re-plan.** |
| Worker | `cryo/worker/worker.py` | Runs the plan op-by-op via the engine; writes `AuditRecord`s; streams progress. |

> ⚠️ **Do not conflate the two Rust stacks.** `cryo-engine/` (the subprocess, iceberg-rust, **no
> DataFusion**) is what you replace. `cryo-native/` (a separate PyO3 crate that *does* use DataFusion 43)
> is only used by `NativeEngine.compact_files()` helper and direct tests — **out of scope**. Finding #12
> in the rust-errors doc warns against treating PyO3 tests as evidence the worker path works.

### 1.2 Current operation status (from the rust-errors doc, re-validated)

| Op | Analyze | Execute | Commits atomically? |
|---|---|---|---|
| `expire_snapshots` | ✅ | ✅ metadata-only | ✅ (but leaks files without paired orphan removal — finding #20) |
| `remove_orphan_files` | ✅ | ✅ object delete | n/a |
| `rewrite_data_files` (OPTIMIZE) | ✅ candidate scan | ⚠️ **staged Parquet only, always `success:false`** | ❌ **blocked upstream** |
| `rewrite_manifests` | ✅ count | ❌ blocked | ❌ |
| MOR compaction | ❌ not attempted | ❌ | ❌ **iceberg-rust cannot** |

Worker routing today (`cryo/engines/native/engine.py`) advertises only
`NATIVE_SUPPORTED_OPERATIONS = (EXPIRE_SNAPSHOTS, REMOVE_ORPHAN_FILES)` and marks
`OPTIMIZE`/`OPTIMIZE_MANIFESTS` **SKIPPED** (`NATIVE_DISCONNECTED_OPERATIONS`).

### 1.3 The contract you MUST preserve (the whole point of the drop-in strategy)

**Invocation:** `cryo-engine execute` with a single JSON `ExecuteRequest` on **stdin**; a single
`ExecuteResponse` JSON as the **last non-prefixed line of stdout**; `CRYO_PROGRESS:<json>` progress lines
on stdout during execution; logs on **stderr**; exit `0` when `ok`, else `1`; missing `execute` arg →
exit `2`.

**Request (from `cryo/engines/native/payload.py` + `cryo-engine/src/main.rs`):**

```json
{
  "table_ref": "display_catalog.schema.table",
  "catalog_uri": "http://iceberg-rest:8181",
  "warehouse": "s3://warehouse",
  "mode": "execute",                          // "execute" | "analyze" | "dry_run"
  "operations": [
    {
      "type": "rewrite_data_files",           // aliases: optimize|rewrite_data_files,
                                              //          expire_snapshots, remove_orphan_files,
                                              //          optimize_manifests|rewrite_manifests
      "params": { "target_file_size_bytes": 268435456, "min_small_files": 128, "...": "..." },
      "predicates": [
        { "terms": [ { "base_column": "event_date", "transform": "day",
                       "value": 20180, "col_type": "date", "partition_field": "event_date_day" } ] }
      ]
    }
  ],
  "params": { "compaction_execute": true },   // request-level flags
  "s3_endpoint": "http://minio:9000",
  "s3_access_key": "…", "s3_secret_key": "…",
  "catalog_properties": { "s3.region": "us-east-1", "s3.path-style-access": "true",
                          "token": "…", "credential": "…", "…": "…" }
}
```

**Response (`ExecuteResponse` / `OperationResult`, consumed by `cryo/engines/native/runner.py`):**

```json
{
  "ok": true,
  "table_ref": "schema.table",
  "operations": 1,
  "results": [
    {
      "operation": "rewrite_data_files",
      "success": true,
      "files_rewritten": 128,
      "input_files": 128,
      "output_files": 3,
      "bytes_removed": 0,
      "bytes_written": 402653184,
      "snapshots_expired": null,
      "manifests_rewritten": null,
      "error": null,
      "duration_ms": 41231
    }
  ]
}
```

**Progress line (`cryo-engine/src/progress.rs` schema, parsed by `runner.is_progress_event`):**

```
CRYO_PROGRESS:{"event":"progress","operation":"rewrite_data_files","progress_pct":42.0,"files_processed":54,"bytes_processed":1234567,"message":"…"}
```
`event` ∈ `{started, progress, completed, failed}` **and** a non-empty `operation` field — anything else
on stdout that isn't the final JSON is ignored.

> **Golden rule for the whole migration:** if the Go binary emits the same request-in / response-out /
> progress bytes, the Python side barely changes. Keep a byte-level protocol conformance test (see
> [§8.8](#88-ci-gates--the-possible-bar)).

### 1.4 Two footguns the rust path had — do NOT reproduce them

- `compaction_write.rs` does `concat_batches(all_input)` in memory → **OOM on large partitions**. The Go
  pipeline must **stream** (iceberg-go's `ToArrowRecords` yields one batch at a time; `RewriteDataFiles`
  uses a rolling writer). Never materialize a whole partition.
- Predicates flowed Python→Rust but were **ignored** by Rust (findings #2/#18). The Go engine must
  **honor them exactly** and **never widen scope** — this is the entire reason the Planner exists.

---

## 2. Why iceberg-go (the rationale in one table)

`apache/iceberg-go` feature status (verified mid-2026, v0.6.x; see
[go.iceberg.apache.org/feature-status](https://go.iceberg.apache.org/feature-status.html) and issue
[#829](https://github.com/apache/iceberg-go/issues/829)):

| Capability we need | `apache/iceberg-rust @6144e10` | `apache/iceberg-go` v0.6.x | Evidence |
|---|---|---|---|
| Atomic **overwrite/replace** commit (add new + remove old data files) | ❌ not merged (EPIC #2269) | ✅ `ReplaceDataFilesWithDataFiles`, `Overwrite`, `RewriteDataFiles` | #723, #867 (OpReplace) |
| **Compaction** action wiring it together | ❌ blocked | ✅ `Transaction.RewriteDataFiles(ctx, []CompactionTaskGroup, opts)` | #892 |
| Compaction **planning / bin-pack** | partial (candidate scan only) | ✅ `table/compaction` pkg, `CompactionConfig.Plan()`, `SlicePacker` | #850 |
| Compaction **dry-run / analyze** | ✅ (analyze only) | ✅ `AnalyzeCompaction` | #893 |
| **Read applies position deletes** | n/a (no MOR) | ✅ scan + `ReadTasks` materialize with pos deletes | #781, #825, #762 |
| **Read applies equality deletes** (+ seq-num) | n/a | ✅ | #818, #823 |
| **Drop consumed delete files** in the same commit | ❌ | ✅ delete-file removal in overwrite commits | **#851** (the key gap they closed) |
| **Write** pos/eq delete files (fixtures/tests) | ❌ | ✅ `WriteEqualityDeletes`, `NewRowDelta` | #809, #823, #721 |
| **Concurrent-writer conflict detection** + retry | manual CAS only | ✅ OCC validators, `commit.retry.*`, refresh-and-replay | #830 |
| `expire_snapshots` (+ delete unreferenced files) | ✅ (metadata only; leaks) | ✅ `ExpireSnapshots(WithPostCommit(true))` | API docs |
| `remove_orphan_files` | ✅ | ✅ `DeleteOrphanFiles(WithFilesOlderThan, WithDryRun, …)` | API docs |
| `rewrite_manifests` | ❌ blocked (#2556) | ❌ **not implemented** (blank in matrix) | feature-status |
| **Deletion vectors (v3) write, partitioned** | ❌ | 🟡 **in progress** (#1135, PR #1151) | feature-status |

**Net:** every hard requirement in the query — COW + MOR compaction, delete files handled, atomic ACID
commits, no fork — is satisfied by **upstream iceberg-go today**, with only `optimize_manifests` and
`v3-partitioned-DV-write` as known gaps to route/gate. iceberg-rust satisfies none of the compaction
requirements without a fork.

---

## 3. Technical challenge evaluation (iceberg-go vs iceberg-rust)

This section answers the query's explicit ask: *evaluate the technical challenges of implementing
iceberg-go instead of iceberg-rust.* Each row has a concrete **mitigation** wired into the phases.

### 3.1 Challenge scorecard

| # | Challenge | iceberg-rust (today) | iceberg-go (this plan) | Severity | Mitigation / where handled |
|---|---|---|---|---|---|
| C1 | **Atomic compaction commit** | Blocked (no action) | Native `RewriteDataFiles` → `Replace` | — (solved) | Phase B/D; §5 |
| C2 | **MOR delete handling** | Impossible | Native (read applies + commit drops) | — (solved) | Phase D; §5.2 |
| C3 | **DataFusion is Rust; how to use from Go** | (was the plan) | Only via cgo bridge; **avoid on critical path** | Medium | §4.5, Appendix D — keep pure-Go |
| C4 | **`optimize_manifests` missing** | Blocked upstream too | **Not in iceberg-go** | Medium | Phase F: route to Trino/Spark, or build from `Create New Manifests` |
| C5 | **v3 deletion-vector write (partitioned)** | n/a | In progress (#1135) | Medium | Gate: v3-partitioned-DV tables → Trino/Spark; document |
| C6 | **Rewrite the whole Rust engine in a new language** | — | New `engine-go/` module + CLI | Medium | Phase A: identical JSON protocol = small, contained port |
| C7 | **Catalog auth + S3 config parity** (REST OAuth2/SigV4, MinIO path-style/region) | partially forwarded | iceberg-go REST/Glue/SQL/Hive + gocloud FileIO | Medium | Phase A: map `catalog_properties` → iceberg-go `Properties`; MinIO smoke test |
| C8 | **Memory on large partitions** | OOM (`concat_batches`) | Streaming `ToArrowRecords` + rolling writer | Low | §6; test P4 |
| C9 | **Commit conflicts / concurrency** | Manual CAS | OCC validators + `commit.retry.*` + isolation levels | Low–Medium | §5.3; tests C1–C4 |
| C10 | **Build/packaging** (cgo?) | Rust toolchain in Docker | Go toolchain; **CGO_ENABLED=0** for default build | Low | Phase A Docker stage; keep gocloud FileIO (pure Go) |
| C11 | **Arrow/Parquet type fidelity** (decimal, timestamp(ns), uuid, nested, variant) | Arrow 58 | iceberg-go Arrow mapping (Appendix in API) | Medium | §8.4 type round-trip fixtures `f_types`/`f_nested` |
| C12 | **Reproducible dependency pin** | git SHA pin (good) | Go module pseudo-version pin | Low | Phase A: pin exact commit; commit `go.sum` |
| C13 | **Predicate → scope fidelity** | ignored (bug) | must translate Cryo predicates → iceberg-go `Expr` / task filter | Medium | Phase B; never widen scope; guard-rail assert |
| C14 | **Two engines during transition** | — | Rust + Go coexist | Low | `CRYO_ENGINE_IMPL=go\|rust` flag; retire rust in Phase F |

### 3.2 The three real risks, expanded

- **C3 — DataFusion-from-Go.** DataFusion is a Rust engine. The only Go path is
  `datafusion-contrib/datafusion-go` (created 2026-05-29, DataFusion 53.1.0, **requires cgo**, downloads
  a native `libdatafusion_go.{so,dylib}` at runtime, "transactions not supported", 4 GitHub stars). Using
  it would (a) reintroduce a Rust dependency we're trying to escape, (b) force `CGO_ENABLED=1` + a C
  toolchain + a per-arch native lib into our image, and (c) add Arrow FFI memory-ownership hazards. **And
  it buys us nothing for the stated goal:** iceberg-go already applies deletes on read, bin-packs, and
  enforces sort order on write. **Decision: pure-Go default; DataFusion is an optional, walled-off
  capability for advanced z-order only, never required for COW/MOR compaction correctness.**
- **C4 — `optimize_manifests`.** Neither implementation has it (rust blocked #2556; iceberg-go blank).
  Cryo already routes it fine to Trino/Spark. **Keep it there** (`NATIVE_DISCONNECTED_OPERATIONS`) until
  Phase F. Do **not** hand-roll manifest-list rewrites early — that is the single most likely way to
  corrupt a table.
- **C5 — v3 deletion vectors (partitioned write).** In progress upstream (#1135). **Detect and gate:**
  if a table is v3 with partitioned deletion vectors, route OPTIMIZE to Trino/Spark and log why. v2
  position/equality deletes are fully supported and are the majority case.

---

## 4. Target architecture

### 4.1 Data flow (Python unchanged; Go replaces Rust)

```
Cryo Planner (Python)                       cryo-engine-go (Go subprocess, apache/iceberg-go)
────────────────────                        ───────────────────────────────────────────────────
Operation(OPTIMIZE,                          1. catalog.Load(props) + cat.LoadTable(ident)   [REST/Glue/SQL/Hive + gocloud S3]
  params, predicates)  ──JSON stdin──▶       2. build compaction scope from predicates:
                                                  scan := tbl.Scan(WithRowFilter(expr), WithSelectedFields(...))
                                                  tasks := scan.PlanFiles(ctx)               [delete files matched per task]
                                             3. plan groups: compaction.Plan(cfg, tasks) → []CompactionTaskGroup
                                                  (bin-pack by size + delete-count; honor GB budget & max-parts caps)
                                             4. rewrite + commit ONE atomic snapshot:
                                                  txn := tbl.NewTransaction()
                                                  res := txn.RewriteDataFiles(ctx, groups, opts)  [reads w/ deletes applied,
                                                                                                   writes clean files,
                                                                                                   drops consumed deletes]
                                                  newTbl := txn.Commit(ctx)                   [OCC validate + commit.retry.*]
                                             5. validate (row-count / file-existence guard rails)
                                             6. emit OperationResult + CRYO_PROGRESS lines
```

Everything left of the arrow already exists and does not change. Everything right of the arrow is this
project.

### 4.2 Module & binary layout

Create a new Go module; **do not** delete the Rust crate until Phase F.

```
engine-go/                        # new Go module (module path e.g. github.com/<org>/cryo/engine-go)
  go.mod  go.sum
  cmd/cryo-engine-go/main.go      # CLI: `cryo-engine-go execute`; reads stdin, writes stdout
  internal/protocol/              # ExecuteRequest/Response/OperationResult structs (mirror the JSON EXACTLY)
  internal/catalog/               # load catalog + FileIO from catalog_properties (REST/Glue/SQL/Hive + gocloud)
  internal/predicate/             # Cryo PartitionPredicate(JSON) → iceberg.BooleanExpression + scope guard
  internal/ops/
    expire.go                     # ExpireSnapshots(WithOlderThan, WithRetainLast, WithPostCommit)
    orphans.go                    # DeleteOrphanFiles(WithFilesOlderThan, WithDryRun, WithMaxConcurrency)
    compaction.go                 # RewriteDataFiles COW+MOR; analyze + execute
    manifests.go                  # analyze-only until Phase F (returns disconnected/skipped signal)
  internal/progress/              # emit "CRYO_PROGRESS:{…}" lines on stdout
  internal/validate/              # pre-commit guard rails + post-commit oracles (row count, existence)
```

**Binary name strategy (transition-safe):**

- Build the Go binary and **also expose it as `cryo-engine`** on `PATH` inside the image so
  `runner.resolve_binary_path` finds it with zero Python change; keep the Rust binary at a fallback path.
- Add a runtime switch **`CRYO_ENGINE_IMPL=go|rust`** (env / `destination.config`) so we can flip per
  destination during rollout. Add `cryo-engine-go` to `DEFAULT_BINARY_NAMES` in
  `cryo/engines/native/runner.py`.
- The Go CLI must mirror the exit-code contract: no/unknown subcommand → **exit 2** (so
  `verify_cryo_engine_spawnable` stays happy), `execute` failure → exit 1, success → exit 0.

### 4.3 Dependency & version policy

- Pin **one** `apache/iceberg-go` commit (pseudo-version) that contains **#892** (RewriteDataFiles
  execute), **#893** (AnalyzeCompaction), **#851** (delete-file removal in overwrite commits), **#850**
  (bin-pack), **#903** (compact CLI), **#830** (conflict detection) **and ideally the refresh-and-replay
  retry PR** (flips `commit.retry.num-retries` default 0→4 and wraps `ErrCommitFailed` for non-REST
  catalogs). Record the commit + the PR list in a comment in `go.mod`. Commit `go.sum`.
- **`CGO_ENABLED=0`** for the default build. Use iceberg-go's **gocloud FileIO** (`_ "github.com/apache/iceberg-go/io/gocloud"`) for S3/GCS/Azure — it is pure Go.
- Go toolchain ≥ **1.24** (datafusion-go, if ever used, needs 1.24; keep parity). Iceberg-go uses
  `arrow-go/v18`.
- Do **not** add `datafusion-go` to the default module (see §4.5). If it is ever added it lives behind a
  Go **build tag** `//go:build datafusion` in a separate package, and the default image never links it.

### 4.4 Configuration mapping (catalog + storage)

Translate the existing `catalog_properties` map (already assembled by
`payload.source_catalog_config`) into iceberg-go catalog/FileIO properties. Do **not** hard-code an
allow-list; pass the map through generically and let iceberg-go consume the keys it knows:

| Cryo request field / property | iceberg-go |
|---|---|
| `catalog_uri` | REST `uri` (or SQL/Glue/Hive per `type`) |
| `catalog_type` | catalog registry key (`rest` / `hive` / `glue`) — see note below |
| `warehouse` | `warehouse` |
| `s3_endpoint` / `s3.endpoint` | gocloud S3 endpoint |
| `s3_access_key` / `s3.access-key-id`, `s3_secret_key` / `s3.secret-access-key` | S3 creds |
| `s3.region` | S3 region (MinIO usually `us-east-1`) |
| `s3.path-style-access` | S3 path-style (MinIO **needs** this) |
| `token` / `credential` / `oauth2-server-uri` / `scope` | REST OAuth2 |
| `rest.sigv4-enabled` / `rest.signing-name` / `rest.signing-region` | REST SigV4 |

**Catalog type selection (do not hard-code `rest`):** `payload.source_catalog_config` forwards the
source's `type` as `catalog_type`, and `internal/catalog.ResolveCatalogType` picks the iceberg-go
registry key. An explicit type wins (aliases like `hms` → `hive` are normalized); otherwise the type is
inferred from the URI scheme. This matters because a Hive Metastore speaks Thrift (`thrift://host:9083`)
and iceberg-go registers it under `hive` — **not** the `thrift` scheme — so an unresolved thrift URI
loaded as REST fails with `unsupported protocol scheme "thrift"`. `internal/catalog` imports the
`catalog/hive` build tag alongside `catalog/rest` so both clients are registered.

**Commit-path S3 credentials (Hive/HMS):** iceberg-go builds the read/scan FileIO from *catalog*
properties (which carry Cryo's per-source S3 creds), but `catalog.CommitTable → internal.WriteMetadata`
rebuilds the S3 FileIO from *table* metadata properties, which do **not** carry those creds. On a Hive
catalog (client writes `*.metadata.json` itself, unlike REST where the server persists it) the commit
therefore falls back to the default AWS credential chain; in role-based deployments that triggers an STS
`AssumeRole` and fails with `InvalidClientTokenId`. `internal/catalog.WithSourceAWSConfig` fixes this by
attaching an explicit static-credential `aws.Config` (from the request's S3 keys) to the context in
`ops.Execute`; gocloud's `createS3Bucket` honors `utils.GetAwsConfig(ctx)` before the fallback, so both
the scan and the metadata write use the same static credentials. When no static keys are provided the
context is left untouched so IRSA / instance-profile auth still works. A custom endpoint (MinIO) is
propagated to the property-less write path via the `AWS_S3_ENDPOINT` env var. Note iceberg-go v0.6.0's
gocloud FileIO registers `s3`, `s3a`, `s3n`, and `oss`, so Hadoop-style `s3a://` metadata/data
locations resolve without extra work.

**`native_table_ref` parity:** the Python side already strips the display-catalog prefix
(`display_catalog.schema.table` → `schema.table`). The Go binary receives the stripped `table_ref` and
must parse `schema.table`, `ns1.ns2.table`, and multi-level namespaces the same way `parse_table_ident`
does today.

### 4.5 The DataFusion question, answered honestly

The query asks for "iceberg-go **and** datafusion." Here is the truthful engineering position, so the
implementer isn't surprised:

- **For the stated goal (compact COW & MOR with ACID commits), DataFusion is not needed and should not
  be on the critical path.** iceberg-go's own Arrow scan applies position/equality deletes, and
  `RewriteDataFiles` bin-packs and commits atomically. Sort order on write is native (#1157). This fully
  covers COW and MOR compaction.
- **Where DataFusion *could* add value later:** global multi-file sort, z-order/space-filling-curve
  clustering, or SQL-expressed rewrite transforms beyond bin-pack. These are *optimizations*, not
  correctness requirements.
- **If we ever want it,** the isolated integration options (in preference order) are in
  [Appendix D](#appendix-d--datafusion-integration-options-if-ever-needed). The recommended one keeps
  the default image pure-Go and only pulls DataFusion into an **opt-in build** behind a Go build tag.

> **Bottom line for the plan:** implement Phases A–F on pure iceberg-go. Treat DataFusion as a **stretch,
> opt-in** capability evaluated only after the COW+MOR gate is green, and only if a real z-order/global-sort
> need is proven with data.

---

## 5. ACID & correctness model — how we guarantee "no loss, no corruption"

Compaction is a **rewrite that must be invisible to readers**: the set of live rows before and after
must be identical. This section maps the ACID guarantees onto iceberg-go primitives. These are the same
invariants the rust plan defined; the *mechanism* is now native to iceberg-go.

### 5.1 Atomicity
- Exactly **one** `Transaction` → one `RewriteDataFiles` → one `Commit` per planned chunk. iceberg-go
  commits a single `Replace` snapshot: all new files in, all old data files (and consumed delete files)
  out, or nothing changes. There is **no intermediate table state** a reader can observe.
- Each chunk (predicate scope) is its **own** atomic commit. A failure in chunk N does not roll back
  chunk N-1 (independent snapshots) and never leaves a chunk half-applied.

### 5.2 Consistency (MOR-critical)
- **Delete-file correctness (#851):** `RewriteDataFiles` reads each data file **with its matched delete
  files applied**, writes clean rows, and marks a delete file `Deleted` **only when every data file it
  referenced was rewritten in this commit**. If a delete file still covers a live data file outside
  scope, it is **retained**. Enforce this as a hard pre-commit guard (invariant #7).
- **Conflict validation:** iceberg-go runs per-producer validators before the commit lands (see
  [Concurrent Writes](https://go.iceberg.apache.org/concurrent-writes.html)). If a concurrent writer
  added deletes to a data file we're replacing, committing would **resurrect deleted rows** — the
  validator/OCC must reject it. Set isolation explicitly (see §5.3) and **abort** on unresolved conflict
  rather than lose deletes (Trino does exactly this).
- Assert schema id, default partition-spec id, and sort-order id are unchanged between plan and commit;
  abort otherwise.

### 5.3 Isolation
- **Optimistic concurrency (CAS on the branch ref).** On `ErrCommitFailed`, iceberg-go retries with
  **refresh-and-replay** (reload metadata → re-run validators → re-submit if still valid). Configure
  explicitly — the default `commit.retry.num-retries` was `0` until the refresh-and-replay PR flipped it
  to `4`; **set it yourself** to be safe:

  ```
  commit.retry.num-retries = 4
  commit.retry.min-wait-ms = 100
  commit.retry.max-wait-ms = 60000
  commit.retry.total-timeout-ms = 1800000
  ```
- **Isolation level per operation** (table properties): `write.delete.isolation-level` and
  `write.update.isolation-level` default `serializable`; `serializable` rejects the commit if any
  concurrent snapshot added data matching the operation's filter, `snapshot` only rejects when concurrent
  deletes touch the same files. **Use `serializable` for MOR compaction** (safest against resurrecting
  deleted rows).
- **Per-table lock** (Cryo already holds this in the worker) prevents *our own* concurrent maintenance;
  OCC handles *external* writers.
- **Caveat:** conflict-retry currently engages on the **REST** catalog (which wraps `ErrCommitFailed`);
  Glue/SQL/Hive may not wrap it until the follow-up lands. For non-REST catalogs, add an
  **application-level retry** (reload table → re-plan scope → re-run) in `internal/ops/compaction.go`.

### 5.4 Durability + the correctness **invariants** (the test oracles)
After a successful compaction commit, **all** of these must hold (these are the §8 assertions):

1. **Row-count invariant:** live row count (deletes applied) identical before/after. *(cheap; also a
   pre-commit guard)*
2. **Row-content invariant:** the multiset of live rows (all columns) is identical before/after.
3. **No dangling references:** every data/delete file referenced by the new snapshot physically exists.
4. **No orphaned live data:** no data file holding *live* rows outside our rewrite scope was dropped.
5. **Snapshot lineage intact:** new snapshot's parent = the snapshot we planned from (or retried-from);
   `operation = replace`; time-travel to the old snapshot still reads pre-compaction data.
6. **Schema / partition-spec / sort-order unchanged; custom snapshot properties preserved.**
7. **Delete-file correctness (MOR):** a delete file is dropped **only if** every data file it referenced
   was rewritten; else retained. No live-but-deleted row reappears.
8. **Failure = no-op:** if any step fails (read, write, validate, commit conflict exhausted), the table
   is byte-for-byte unchanged; any Parquet written becomes an orphan removable by `remove_orphan_files`.

> Assert #1, #3, #7, #8 **in-process before every real commit** (guard rails in `internal/validate/`).
> Assert #2, #4, #5, #6 in the test suite and a periodic "deep validation" mode.

---

## 6. Cost-aware & performance best practices

The product owner requires compaction to **reduce**, not increase, cost. Bake these into the Go engine
and honor the planner params; each is testable in [§8.6](#86-cost--performance-assertions).

**Read/scan cost**
- **Never scan the whole table on a request path.** Always scope by the planner's predicate
  (`tbl.Scan(WithRowFilter(expr))` / task filter). Full-table `PlanFiles` on a big table is a huge S3
  LIST/GET bill.
- Use Cryo's **pre-computed scanner stats** to decide *whether* to compact; don't walk live manifests on
  the hot path.
- Push projection down (only needed columns); for MOR, only read delete columns needed for matching.

**Write / storage cost**
- **Target file size** from `write.target-file-size-bytes` (fallback planner `target_file_size_bytes`,
  default 128–512 MB). Fewer, larger files ⇒ fewer future GET/LIST ⇒ the main cost win.
- **`min-input-files` gate** (`min_small_files`, default 128): never rewrite an already-optimal partition
  (pure write amplification). iceberg-go's `CompactionConfig` classifies candidates by size + delete
  count — set thresholds from the planner params.
- **Don't re-compact big files:** only files **below** the size threshold are candidates (oversized files
  pass through untouched unless their delete-count exceeds the threshold — iceberg-go supports exactly
  this).
- Honor **cooldown** / **partition-idle-hours** (planner emits these) — don't recompact partitions still
  being written.

**Commit / metadata cost**
- **Bounded blast radius:** honor the planner's **GB-budget per chunk** and **max-partitions-per-chunk**.
  Smaller commits = smaller manifests, fewer CAS conflicts, cheaper retries. Use iceberg-go
  `MaxFilesToRewrite` to cap a single run.
- Pair with `optimize_manifests` (Trino/Spark for now) to keep manifest count low.

**The storage-transient rule (cost-aware, important):** a compaction commit **adds** new files and only
**logically** removes old ones; old files persist for time-travel until `expire_snapshots +
remove_orphan_files` run. So compaction **temporarily increases** stored bytes. Sequence
`expire → orphan` after compaction (Cryo DEEP mode already does) and measure **net** cost only **after**
the full maintenance cycle.

**Performance / memory**
- **Stream, don't buffer.** Consume `ToArrowRecords` (one batch at a time) and let `RewriteDataFiles`'s
  rolling writer roll a new file at target size. Memory stays **bounded** regardless of partition size.
- **Concurrency caps:** bounded parallel rewrite of groups, capped by the Governor's real capacity — not
  worker replica count.
- Match table write properties (compression codec, row-group size).
- Reuse the FileIO client; prefer multipart upload for large outputs; avoid many tiny PUTs.

**Cost accounting hooks:** emit `input_files`, `output_files`, `bytes_written`, `bytes_removed` in
`OperationResult` (already in the schema); wire into `docs/s3-request-cost-tracking.md`. A run that
doesn't reduce projected per-scan request count is a planner bug, not a success.

---

## 7. Phased implementation plan

Each phase leaves the repo in a correct, shippable state. **Do not start a phase until the previous
phase's exit criteria are green.** "DONE WHEN" is the objective completion signal. All subagent-assisted
research/scaffolding for these phases should use **composer-2.5 (non-fast)**.

### Phase A — Foundations: Go module, protocol parity, expire+orphan (prove the swap)

**Objective:** a Go binary that speaks the exact JSON protocol and correctly executes the two ops that
already work in Rust (`expire_snapshots`, `remove_orphan_files`), building reproducibly in CI/Docker —
so we can flip the engine with **zero behavior change**.

Tasks:
1. Scaffold `engine-go/` ([§4.2](#42-module--binary-layout)); pin `apache/iceberg-go`
   ([§4.3](#43-dependency--version-policy)); commit `go.sum`.
2. `internal/protocol/`: structs mirroring `ExecuteRequest`/`OperationRequest`/`PredicateRequest`/
   `ExecuteResponse`/`OperationResult` **field-for-field** (JSON tags identical). Include the operation
   type aliases (`optimize|rewrite_data_files`, etc.).
3. `internal/catalog/`: load catalog + gocloud S3 FileIO from `catalog_properties`
   ([§4.4](#44-configuration-mapping-catalog--storage)); MinIO path-style + region.
4. `internal/ops/expire.go`: `ExpireSnapshots(WithOlderThan(retention_days), WithRetainLast(retain_last),
   WithPostCommit(true))`. Drive the reported `snapshots_expired` from the actual before/after snapshot
   set (fixes rust finding #21). **Pair expiry with orphan removal so we don't leak (finding #20).**
5. `internal/ops/orphans.go`: `DeleteOrphanFiles(WithFilesOlderThan(min_age_hours), WithDryRun(mode!=execute),
   WithMaxConcurrency(n))`. Honor the `orphan_min_age_hours` / `retention_days` contract.
6. `internal/progress/`: emit `CRYO_PROGRESS:{…}` lines matching the schema; logs to stderr only.
7. CLI exit codes: no/unknown subcommand → 2; `execute` failure → 1; success → 0. Compute top-level `ok`
   as `all(results.success)` (fixes rust finding #6).
8. **Reproducible build:** add a Go builder stage to the `Dockerfile` (`FROM golang:1.24-bookworm AS
   cryo-engine-go-builder` → `CGO_ENABLED=0 go build`), copy the binary onto `PATH` as `cryo-engine-go`
   (and, for drop-in, also `cryo-engine`); add `cryo-engine-go` to `DEFAULT_BINARY_NAMES`.
9. CI: add a `cryo-engine-go` job (`go vet`, `golangci-lint`, `go test`, `go build`) mirroring the
   existing `cryo-engine` job; make it verify-spawnable from Python.
10. Add `CRYO_ENGINE_IMPL` switch and thread it through `resolve_binary_path`.

**Phase A progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Scaffold `engine-go/` + pin iceberg-go | ✅ Done | `engine-go/` module; `apache/iceberg-go v0.6.0`; `go.sum` committed |
| 2 | `internal/protocol/` JSON structs | ✅ Done | Field-for-field mirror of Rust `ExecuteRequest`/`OperationResult` |
| 3 | `internal/catalog/` REST + gocloud S3 | ✅ Done | Maps `catalog_properties`, MinIO path-style + region |
| 4 | `internal/ops/expire.go` | ✅ Done | Incremental expire for REST multi-snapshot removal; `WithPostCommit(false)` + paired orphan op |
| 5 | `internal/ops/orphans.go` | ✅ Done | `DeleteOrphanFiles`; dry-run preview for byte metrics; age/concurrency params |
| 6 | `internal/progress/` | ✅ Done | `CRYO_PROGRESS:{…}` on stdout; logs on stderr |
| 7 | CLI exit codes + `ok = all(success)` | ✅ Done | exit 2/1/0 contract; top-level `ok` requires every op success |
| 8 | Dockerfile Go builder stage | ✅ Done | `cryo-engine-go-builder`; `cryo-engine-go` + `cryo-engine` symlink on PATH |
| 9 | CI `cryo-engine-go` job | ✅ Done | `go vet`, `go test`, `go build`, spawnable check; `native-e2e-go` job added |
| 10 | `CRYO_ENGINE_IMPL` switch | ✅ Done | `runner.resolve_binary_path`; env + `destination.config.engine_impl` |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| `native-e2e` pass with `CRYO_ENGINE_IMPL=go` | ✅ Passed locally (`make test-native-e2e-go` / `test_native_expire_orphan_e2e.py`) |
| Docker builds Go binary `CGO_ENABLED=0` | ✅ Dockerfile stage added |
| Byte-level protocol conformance test | ✅ `tests/test_native_protocol_conformance.py` + Go `protocol` unit tests |
| CI green | 🟡 Pending CI — Trino wait loop fixed in `ci.yml`; differential e2e green locally |

**Exit criteria / DONE WHEN:** with `CRYO_ENGINE_IMPL=go`, the **existing** `native-e2e` test
(`tests/integration/native/test_native_expire_orphan_e2e.py`, `make test-native-e2e-local`) passes with
**identical audit metrics** to the Rust engine (snapshots expired, orphan bytes removed); Docker builds
the Go binary from scratch (`CGO_ENABLED=0`); the byte-level protocol conformance test passes; CI green.

### Phase B — COW compaction: read + bin-pack + atomic `RewriteDataFiles` commit

**Objective:** ship real, atomic, ACID COW compaction (append-only / copy-on-write, **no delete files**),
consuming the planner's predicates and params.

Tasks:
1. `internal/predicate/`: translate Cryo `PartitionPredicate` (OR-of-AND terms, transforms
   identity/day/month/year) → iceberg-go `BooleanExpression` (or an equivalent task filter). Reject
   unsupported transforms (`bucket`, `truncate`) in execute mode with a clear error, exactly like the
   rust predicate guard.
2. `internal/ops/compaction.go` **analyze**: `AnalyzeCompaction` / `compaction.Plan(cfg, tasks)` for the
   predicate scope; return estimated `input_files`, `output_files`, bytes. Never re-plan Cryo's chunking —
   consume it.
3. `internal/ops/compaction.go` **execute**:
   ```go
   scan := tbl.Scan(table.WithRowFilter(expr), table.WithSelectedFields(cols...))
   tasks, _ := scan.PlanFiles(ctx)
   cfg := compaction.NewConfig(targetSize, minSize, maxSize, deleteThreshold, maxFilesToRewrite)
   groups, _ := cfg.Plan(tasks)                    // bin-pack candidates by size (+ delete count)
   txn := tbl.NewTransaction()
   res, _ := txn.RewriteDataFiles(ctx, groups, table.RewriteDataFilesOptions{})
   newTbl, err := txn.Commit(ctx)                  // OCC + commit.retry.*
   ```
4. **Streaming**: ensure the rewrite consumes batches incrementally (bounded memory). No whole-partition
   materialization.
5. **Pre-commit guard rails** (invariants #1, #3, #8): row-count in==out (no deletes in COW), file
   existence, schema/spec-id stability, assert produced partition values ⊆ requested scope (never widen).
6. **Metrics**: distinct `input_files` vs `output_files`, `bytes_written`, `bytes_removed` (0 pre-expiry).
7. Custom snapshot property preservation on the commit.

**Phase B progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | `internal/predicate/` Cryo → iceberg-go filter | ✅ Done | OR-of-AND terms; identity/day/month/year; bucket/truncate rejected |
| 2 | `compaction.go` analyze path | ✅ Done | Scoped `PlanFiles` + `compaction.PlanCompaction`; returns est. input/output/bytes |
| 3 | `compaction.go` execute path | ✅ Done | `Scan` → `PlanCompaction` → `RewriteDataFiles` → `Commit` via iceberg-go |
| 4 | Streaming (bounded memory) | ✅ Done | Delegates to iceberg-go rolling writer; caps `scan_concurrency` (default 1), splits primarily at `max_bytes_per_group` (defaults to `target_file_size_bytes`; optional `max_files_per_group` soft ceiling, unset by default so tiny-file bin-packs are not shredded), then **re-applies `min_input_files` (default 2)** so memory splits cannot produce 1→1 rewrites; `partial_progress` commits each group. **Throughput (2026-07-09):** memory-budgeted `group_concurrency` + in-memory IO/metrics props — see [`native-go-compaction-throughput.md`](./native-go-compaction-throughput.md) |
| 5 | Pre-commit guard rails (#1, #3, #8) | 🟡 Partial | Row-count + metadata + MOR gate in-process; partial-progress path fixed (`executePartialProgress`); e2e oracles O1/O3/O6 |
| 6 | Metrics (`input_files`, `output_files`, bytes) | ✅ Done | Distinct counts in `OperationResult`; `files_rewritten` = candidate input count |
| 7 | Custom snapshot property preservation | ✅ Done | Non-system parent summary props copied into `RewriteDataFilesOptions.SnapshotProps` |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| §8 COW scenario matrix green on MinIO | 🟡 Partial — `f_unpart_small`, `f_part_identity`, `f_part_small`; Go e2e green except day-partition execute (xfail) |
| Differential test vs Trino (§8.7) | 🟡 Partial — `test_native_differential_e2e.py` green for unpartitioned + identity; day full-table xpasses; **scoped day execute** still xfail (iceberg-go partition typing) |
| All invariants (§5.4) on local MinIO | 🟡 Partial — O1/O3/O4/O5/O6/O8 covered in compaction e2e; day-partition execute blocked upstream |
| Go unit tests (predicate + protocol) | ✅ `go test ./...` green |
| `make test-native-compaction-e2e-local-go` | ✅ Green locally (4 pass, 1 xfail day execute, 1 skip Rust staged) |

**Exit criteria / DONE WHEN:** the §8 **COW scenario matrix** (unpartitioned, partition-scoped,
multi-chunk, oversize-alone + budget cap, small-table bypass, only-small-files, schema-evolved,
partition-evolved, nested/wide types, time-travel preserved, custom properties, idempotence) passes
green with **all invariants (§5.4)** holding on local MinIO, and the **differential test vs Trino**
([§8.7](#87-differential-testing-vs-trinospark-ground-truth)) shows identical live-row multisets. A
fragmented table's file count drops and per-scan GET count drops; stored bytes drop **after**
expiry+orphan.

### Phase C — Worker wiring: re-enable native OPTIMIZE for COW

**Objective:** the platform routes COW OPTIMIZE to native by default, safely; MOR still routes to
Trino/Spark until Phase D.

Tasks:
1. In `cryo/engines/native/engine.py`, add `OperationType.OPTIMIZE` to `NATIVE_SUPPORTED_OPERATIONS`
   **only when the target snapshot has no delete files** (COW). Detect MOR (any position/equality delete
   files present, or `write.delete.mode=merge-on-read` with deletes) and keep those in
   `NATIVE_DISCONNECTED_OPERATIONS` → Trino/Spark.
2. Verify predicates + params thread end-to-end into the Go request (they already do in `payload.py`;
   confirm the Go side honors them).
3. Single multi-op subprocess (one `execute_plan` runs the ordered plan) — already the pattern; ensure op
   order `expire → orphan → optimize`.
4. Progress → run console; per-op audit rows with `input_files`/`output_files`/bytes.
5. Governor: native capacity from live in-flight subprocess accounting (rust finding #9), not a static
   CPU cap.

**Phase C progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | COW-only OPTIMIZE in native routing | ✅ Done | `routing.py`: `native_cow_optimize_enabled()`; MOR → SQL fallback or skip |
| 2 | Predicates + params end-to-end | ✅ Done | `payload.py` forwards predicates; Go `predicate.BuildRowFilter` honors them; unit tests |
| 3 | Multi-op subprocess order | ✅ Done | `filter_native_operations()` preserves planner order (expire → orphan → optimize) |
| 4 | Progress + per-op audit metrics | ✅ Done | `CRYO_PROGRESS` → run console; audit `input_files`/`output_files`/bytes on optimize |
| 5 | Governor native capacity | ✅ Done | `LiveCapacityProvider._probe_native()` uses `slot_cap`; governor subtracts queue in-flight |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| DEEP COW run: expire + orphan + native optimize + Trino manifests | 🟡 Partial — routing + fallback wired; full worker e2e pending |
| MOR optimize routes to Trino with clear label | ✅ Phase C only — **Phase D** now routes MOR to native; v3-partitioned-DV still falls back |
| No leaked files after cycle | ⏳ Pending worker e2e |
| Unit tests green | ✅ `tests/test_native_routing.py` + updated `test_native_runner.py` |

**Exit criteria / DONE WHEN:** a DEEP run on a **COW** table via the worker completes end-to-end
(expire + orphan + **native COW optimize** + Trino manifests) with correct audit metrics and streamed
progress; MOR tables in the same run still route optimize to Trino and are clearly labeled; no leaked
files after the cycle.

### Phase D — MOR compaction (position + equality deletes)

**Objective:** native compaction for tables with position/equality delete files — applying deletes on
read and dropping consumed deletes on commit — with mandatory conflict validation.

Tasks:
1. **Read path is free:** iceberg-go's scan + `ReadTasks` already materialize rows with position (#825)
   and equality (#818, seq-num-aware) deletes applied. Confirm the scan filter still scopes to the
   predicate.
2. **Commit path:** `RewriteDataFiles` already removes consumed delete files (#851). Add the hard
   pre-commit guard for invariant #7 (a delete file is dropped **iff** all its referenced data files are
   in scope; otherwise retained).
3. **Conflict validation is now mandatory:** set `write.delete.isolation-level=serializable`; on conflict
   → abort (do not commit); rely on OCC + `commit.retry.*`; add application-level retry for non-REST
   catalogs (§5.3 caveat).
4. **v3 deletion vectors:** if the pinned iceberg-go supports partitioned-DV write (#1135), enable;
   otherwise **gate** v3-partitioned-DV tables to Trino/Spark and document. v2 pos/eq deletes ship now.
5. Extend MOR detection in Phase C routing to send MOR OPTIMIZE to native.

**Phase D progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Read path: scan applies deletes + predicate scope | ✅ Done | Existing `planScopedTasks` + `BuildRowFilter`; iceberg-go `ReadTasks` applies pos/eq deletes |
| 2 | Commit path + invariant #7 guard | ✅ Done | `validate.AssertDeleteFileInvariant`; `CollectDeadEqualityDeletes` via `ExtraDeleteFilesToRemove` |
| 3 | Conflict validation (`serializable` + `commit.retry.*`) | ✅ Done | `ensureMORCompactionProperties` on MOR txn; app-level retry for non-REST catalogs |
| 4 | v3 partitioned deletion vectors gate | ✅ Done | Go `AssertPartitionedDeletionVectorsUnsupported`; Python `has_v3_partitioned_deletion_vectors` |
| 5 | MOR OPTIMIZE routes to native | ✅ Done | `routing.py`: `native_optimize_enabled()` includes MOR; v3-partitioned-DV → SQL fallback |
| 6 | MOR e2e fixtures + harness (§8) | ✅ Done | Go `seed-native-fixtures` (seed/count/live-column/seed-table/commit-equality-delete); `test_native_mor_compaction_e2e.py`; `make test-native-compaction-e2e-go` |
| 7 | MOR concurrency matrix C2/C3 (§8.5) | ✅ Done | `CRYO_TEST_PAUSE_BEFORE_COMMIT` hook; `test_native_mor_concurrency_e2e.py` |
| 8 | Fault-injection harness C4–C8 (§8.5) | ✅ Done | Go `testhook` (catalog/IO/commit hooks); `fault_injection_harness.py`; `test_native_fault_injection_e2e.py` |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| §8 MOR scenario matrix (pos/eq/mixed/partial) | 🟡 Partial — Go seeder (`seed-table` per test), `mor_inspect.py` Go live-row oracle, `test_native_mor_compaction_e2e.py` green (pos/eq/mixed execute; partial precondition); mixed O7 asserts eq-delete only (pos on `f_mor_pos`) |
| §8.5 concurrency/fault matrix (C2/C3) | 🟡 Partial — `test_native_mor_concurrency_e2e.py` (C2 concurrent delete, C3 dual compaction); C1 pending |
| §8.5 fault-injection matrix (C4–C8) | ✅ Done — `test_native_fault_injection_e2e.py` + Go `internal/testhook` |
| §8.5 partial-progress commit-race matrix (C9/C10) | ✅ Done — `test_native_checkpoint_resume_e2e.py` (C9 transient loss resumes to completion, C10 sustained loss → `commit_race_lost` no-op) |
| Differential vs Trino/Spark on MOR fixtures | 🟡 Partial — `test_native_mor_differential_e2e.py`: **eq** full Trino reference; **pos/mixed** Go-oracle baseline (Trino cannot read fixture position-delete Parquet yet) |
| Invariants #1, #2, #7 in-process | 🟡 Partial — #1 + #7 guarded pre-commit; MOR e2e asserts O1/O7 on REST+MinIO |

**Exit criteria / DONE WHEN:** the §8 **MOR scenario matrix** (position-only, equality-only, mixed,
overlapping deletes, delete-only partitions) **and** the **concurrency/fault matrix**
([§8.5](#85-concurrency--fault-injection-the-corruption-hunters)) pass green with invariants #1, #2, #7
holding; a MOR table's live rows are identical before/after (differential vs Trino/Spark) and **no
deleted row reappears** under concurrent-delete injection (C2).

### Phase E — Hardening, capacity, docs cutover-readiness

**Objective:** make native a first-class, correctly-governed default; results trustworthy and observable.

Tasks:
1. Live native capacity from queue/DB in-flight subprocess accounting (rust finding #9).
2. Map all operation-specific audit fields (`snapshots_expired`, `manifests_rewritten`, distinct
   input/output counts) — already in the protocol; verify end-to-end (findings #13/#14).
3. Docs truth-in-advertising: state exactly which ops execute natively per pinned iceberg-go commit
   (finding #15).
4. Split naming/tests: `cryo-engine-go` (worker subprocess) vs the optional `cryo_native` PyO3 helpers
   (finding #12) — never cite PyO3 tests as evidence the worker path works.
5. Benchmark suite: large-partition memory (P4), file-count reduction, request-count reduction.

**Phase E progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Live native capacity from queue in-flight | ✅ Done | `probe_native_capacity*`; `LiveCapacityProvider(queue)`; API destination test shows in-flight |
| 2 | Audit fields end-to-end (#13/#14) | ✅ Done | `tests/test_native_audit_fields.py`; worker already maps to `AuditRecord` |
| 3 | Docs truth-in-advertising (#15) | ✅ Done | `engine-go/README.md` ops matrix at pinned iceberg-go v0.6.0 |
| 4 | Split subprocess vs PyO3 naming/tests (#12) | ✅ Done | `test_native_engine.py` class rename + docstring; subprocess tests separated |
| 5 | Benchmark suite (P2/P4/P5) | 🟡 Partial | `test_native_compaction_perf.py` + `perf_harness.py`; P2 request-count vs Trino pending |
| 6 | Resumable checkpointing + commit-race signal | ✅ Done (2026-07-08) | `executePartialProgressResumable` commits each group atomically; a lost commit race reloads + re-plans from the current snapshot (idempotent resume) and only forfeits the current group; `progress.Checkpoint` event per committed group; exhausted races tagged `commit_race_lost` (`errCommitRaceLost`, `runner.is_commit_race_lost`) so the worker surfaces a first-class alert instead of a silent retry loop. Go unit tests: `compaction_checkpoint_test.go`, `progress_test.go`; MinIO/REST injected-conflict e2e: `test_native_checkpoint_resume_e2e.py` (C9 resume-to-completion, C10 exhausted-race no-op) |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| Full §8 suite green ≥N consecutive CI runs | 🔴 Pending |
| Deep validation O2 over all fixtures | 🔴 Pending |
| Docs match pinned commit behavior | 🟡 Partial — `engine-go/README.md` + this plan updated |
| Checkpointing resilient to per-group commit-race loss | ✅ Done — resumable per-group commits + `commit_race_lost` signal (2026-07-08); validated by Go unit tests **and** the MinIO/REST injected-conflict e2e `test_native_checkpoint_resume_e2e.py` (C9/C10) |

**Exit criteria / DONE WHEN:** CI runs the full §8 suite (COW + MOR + concurrency + cost/perf +
differential) green for **≥N consecutive runs** with no flakes; the "deep validation" (O2 over all
fixtures) passes; docs match implemented behavior for the pinned commit.

### Phase F — Manifests, sort/z-order, and retirement of the Rust engine

**Objective:** close the remaining gaps and remove the dead Rust path.

Tasks:
1. **`optimize_manifests`** (the one iceberg-go gap): **Option 1 (recommended, low-risk):** keep it routed
   to Trino/Spark (`NATIVE_DISCONNECTED_OPERATIONS`), document it. **Option 2 (advanced, gated on heavy
   §8 tests):** implement manifest rewrite in Go from the `Create New Manifests` primitives + a
   metadata-only `Replace`-style commit — treat as its own hardened sub-project with the full corruption
   oracle suite; do **not** attempt casually.
2. **Optional sort** in the rewrite using iceberg-go's native sort-order enforcement on write (#1157).
3. **z-order / global sort (optional, opt-in):** evaluate only if a real need is proven; if pursued, use
   an isolated approach from [Appendix D](#appendix-d--datafusion-integration-options-if-ever-needed)
   behind a build tag — never in the default image.
4. **Retire Rust:** once Go is default-ON and stable, remove `cryo-engine/` (Rust), its Docker builder
   stage, the `cryo-engine` CI job, and the rust-specific "blocked upstream → skipped" error mapping in
   `engine.py`. Update `todo.md` P9 and the docs.

**Exit criteria / DONE WHEN:** native (Go) is the default DEEP workflow for **COW and MOR**;
`optimize_manifests` behavior is documented and correct (routed or natively implemented + tested); the
Rust engine is removed; the cost report shows net reduction across a full maintenance cycle; docs match
reality.

**Phase F progress (updated 2026-07-08):**

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | `optimize_manifests` Option 1 (route to SQL) | ✅ Done | `routing.py` `NATIVE_ALWAYS_DISCONNECTED`; Go analyze counts current-snapshot manifests; execute returns routed message |
| 2 | Optional sort on rewrite (#1157) | ✅ Done | `compaction_sort.go`: honors table sort order on write; validates `strategy=sort` / `sort_columns`; rejects unsorted tables + column mismatch |
| 3 | z-order / global sort (opt-in) | ✅ Deferred | Explicit rejection in Go + route to Trino/Spark; DataFusion build tag remains out of default image |
| 4 | Retire Rust `cryo-engine/` | 🟡 Partial | `CRYO_ENGINE_IMPL` default → `go`; Rust crate/CI/Docker stage kept until Phase E §8 gate green |
| 5 | Web UI engine preset support | ✅ Done | Split Native (iceberg-go/rust) in Connections; `engine_impl` round-trip; preset/bulk labels; grey-out `optimize_manifests` + manifest-only for native |

**Exit criteria status:**

| Criterion | Status |
|-----------|--------|
| Native Go default for COW + MOR deep workflow | 🟡 Partial — default `engine_impl=go`; seed preset already uses Go |
| `optimize_manifests` documented + routed correctly | ✅ Done |
| Optional sort behavior matches pinned iceberg-go | ✅ Done — table sort-order metadata on write; explicit sort_columns validated |
| Rust engine removed | 🔴 Pending — blocked on Phase E consecutive CI green |
| Docs match reality | 🟡 Partial — this plan + `engine-go/README.md` + Cryo UI labels updated |

---

## 8. Testing methodology — local MinIO Iceberg tables

> **Interpretation note:** the query says "test iceberg-**rust** based datafusion table rewrite compaction
> and manifest rewrites." Given the entire task is to move **off** iceberg-rust (which *cannot* do
> compaction), this is read as testing the **iceberg-go** implementation — the acceptance gate for
> "implementation is possible." Where useful, we still **differentially test against Trino/Spark** as the
> trusted ground truth (that is the strongest correctness guarantee). If you truly also want a rust
> control arm, run the same fixtures through the old staged rust path for analyze-only comparison — but
> the pass/fail gate is the Go engine.

**Principle:** compaction is only "possible" (per the product owner) when **every** scenario below is
green, including concurrency and fault injection, with **no invariant ([§5.4](#54-durability--the-correctness-invariants-the-test-oracles))
ever violated**. This section is the acceptance gate. Build it incrementally alongside the phases (COW
scenarios gate Phase B/C; MOR scenarios gate Phase D).

### 8.1 Environment (all local, no cloud)

Reuse the existing harness:
- `docker-compose.dev-datalake.yml`: **MinIO** (S3), `minio-init` (bucket), **iceberg-rest** catalog, and
  Trino/Spark (for differential tests).
- `make up-datalake-native` (MinIO + REST) and `make up-datalake` (adds Trino/Spark).
- Fixtures via `tests/integration/native/rest_env.py` + a new
  `tests/integration/native/generate_compaction_fixtures.py`.
- Local object-store assertions via boto3 listing (already present).
- `go build` (or `make build-engine-go`) produces the binary the Python harness spawns.
- New make target `make test-native-compaction-e2e-local` and a CI job `native-compaction-e2e` mirroring
  the existing `native-e2e` job.

### 8.2 Fixture table generator (build these variants)

Write a generator (PyIceberg for COW setup; **Spark** for MOR position/equality-delete setup where
PyIceberg can't; iceberg-go's `WriteEqualityDeletes`/`NewRowDelta` can also seed deletes for self-tests)
that produces, on the local REST catalog + MinIO, each of:

| Fixture | Shape | Purpose |
|---|---|---|
| `f_unpart_small` | unpartitioned, N tiny files | small-table bypass, basic bin-pack |
| `f_part_small` | partitioned by `event_date` (day), many tiny files/partition | partition-scoped compaction |
| `f_part_skew` | one huge partition + many small | oversize-alone + budget caps + memory |
| `f_mixed_sizes` | files above & below threshold | only-small-files-compacted |
| `f_empty_partition` | a partition with 0 rows / only deletes | delete-only edge case |
| `f_cow_deletes` | COW DELETE applied (rewrote data files, no delete files) | COW correctness |
| `f_mor_pos` | MOR position deletes | MOR position path |
| `f_mor_eq` | MOR equality deletes | MOR equality + seq-num |
| `f_mor_mixed` | both position & equality deletes | MOR mixed |
| `f_mor_partial` | delete files that cover **some but not all** referenced data files | invariant #7 (retain vs drop) |
| `f_schema_evolved` | added/renamed/dropped column across snapshots | schema-safety |
| `f_partition_evolved` | partition spec changed across snapshots | spec-safety |
| `f_nested` | struct/map/list columns | nested-type read/write |
| `f_types` | date/time/timestamp/timestamp_ns/decimal/bool/binary/uuid | type round-trip (see iceberg-go Arrow map) |
| `f_v3_dv` | format v3 with deletion vectors (partitioned + unpartitioned) | v3 handling / gating |
| `f_multi_snapshot` | ≥5 snapshots | time-travel preservation |
| `f_custom_props` | snapshots with custom summary properties | property preservation |

### 8.3 The invariant oracles (assert on every scenario)

Implement as reusable assertions (Python side, reading via PyIceberg/Trino + boto3):

- **O1 Row count:** `count(*)` (deletes applied) equal before/after. *(cheap; also a pre-commit guard)*
- **O2 Row content:** full-table (or scoped-partition) row multiset equal before/after — hash each row,
  compare sorted multisets. Catches drops, dups, corruption, wrong-partition writes.
- **O3 Physical existence:** every data/delete file in the new snapshot's manifests exists in MinIO.
- **O4 No live-data loss:** files removed were entirely within the requested predicate scope; files
  outside scope are byte-identical and still referenced.
- **O5 Lineage/time-travel:** new snapshot parent = planned snapshot; `operation=replace`; reading `AS OF`
  the old snapshot returns the old data unchanged.
- **O6 Metadata stability:** schema id, partition-spec id, sort-order id unchanged; custom properties
  preserved; auto counters (`total-records`, etc.) recomputed correctly.
- **O7 MOR delete correctness:** deleted rows do **not** reappear; delete files dropped only when fully
  consumed; retained delete files still reference live data files.
- **O8 File-size effect:** output files ≤ `1.2 × target_file_size` (small tail file allowed); small-file
  count strictly decreases.
- **O9 Failure = no-op:** after an injected failure, current snapshot id unchanged and O2 holds; any
  written Parquet is an orphan removable by `remove_orphan_files`.

### 8.4 Scenario matrix (functional)

Run each applicable fixture through analyze **and** execute, asserting the relevant oracles:

1. **Bin-pack, unpartitioned** (`f_unpart_small`) → O1,O2,O3,O5,O6,O8.
2. **Partition-scoped** (`f_part_small`, predicate on one day) → O1,O2,O4 (others untouched),O8. Verify
   **only** the scoped partition's files changed.
3. **Multi-chunk** (`f_part_small`, several predicates) → each chunk its own atomic snapshot; after all,
   O1,O2 over the whole table.
4. **Oversize-alone + budget cap** (`f_part_skew`) → oversize partition committed alone; caps honored.
5. **Small-table bypass** (`f_unpart_small` below `chunk_size_gb`) → single full-table optimize.
6. **Only-small-files** (`f_mixed_sizes`) → large files pass through untouched (O4), small ones packed.
7. **Empty/delete-only partition** (`f_empty_partition`) → no crash; correct no-op or clean rewrite.
8. **COW deletes** (`f_cow_deletes`) → O1,O2,O7-trivial.
9. **MOR position** (`f_mor_pos`) → deletes applied, position-delete files consumed & dropped; O1,O2,O7.
10. **MOR equality** (`f_mor_eq`) → eq deletes applied only where `seq_num` lower; O1,O2,O7.
11. **MOR mixed** (`f_mor_mixed`) → O1,O2,O7.
12. **MOR partial-consumption** (`f_mor_partial`) → delete files covering out-of-scope data files are
    **retained**; only fully-consumed ones dropped; O7.
13. **Schema evolved** (`f_schema_evolved`) → read/write with current schema; O2 with projection; O6.
14. **Partition evolved** (`f_partition_evolved`) → compaction operates within a single spec; O4,O6.
15. **Nested & wide types** (`f_nested`, `f_types`) → O2 exact round-trip (no type/precision loss).
16. **Time-travel preserved** (`f_multi_snapshot`) → O5 after compaction.
17. **Custom properties** (`f_custom_props`) → O6 property preservation.
18. **Idempotence:** run optimize twice; second run near no-op (nothing below threshold), no wasted
    rewrite (O8 + write-amplification check §8.6).
19. **v3 DV** (`f_v3_dv`) → if supported, O1,O2,O7; if gated, assert clean route to Trino/Spark + clear log.

### 8.5 Concurrency & fault injection (the corruption hunters)

These are where tables actually break. Each must end with the table valid (O2, O9):

- **C1 Concurrent append during compaction:** start compaction on a snapshot; append new files (via
  Trino/PyIceberg) before our commit. Expect: our commit succeeds after retry **without dropping the
  concurrent append**, or aborts cleanly. Assert the concurrent rows survive (O2).
- **C2 Concurrent DELETE during MOR compaction:** add a delete file to a scoped data file mid-flight.
  Expect **abort** under `serializable` isolation, never a resurrected deleted row (O7).
- **C3 Concurrent overwrite/compaction:** two compactions on overlapping scope. Expect one wins, the
  other aborts on CAS; no double-remove of the same files, no loss (O2).
- **C4 Commit conflict retry exhaustion:** force N conflicts; assert clean failure + no-op (O9).
- **C5 Crash between write and commit:** kill `cryo-engine-go` after writing Parquet, before commit.
  Assert O9 (table unchanged; new Parquet is orphan) and that a subsequent `remove_orphan_files` cleans it.
- **C6 Crash mid-commit / catalog error injection:** simulate REST 500 during commit. Assert atomicity —
  snapshot fully applied or not at all (O9); never a partial manifest set.
- **C7 Object-store partial failure:** inject a failed PUT for one output file. Assert the whole chunk
  aborts; no snapshot references a missing file (O3).
- **C8 Duplicate/late retry:** ensure a retried commit can't apply the same rewrite twice (idempotency of
  the CAS envelope).
- **C9 Transient commit-race loss with partial progress:** ✅ Done — with `partial_progress` on a
  3-group fixture, fail the first per-group commit (no in-process OCC retry). Assert the engine reloads,
  re-plans from the current snapshot, and lands **every** group (idempotent resume), emitting a
  `checkpoint` progress event per committed group (`test_native_checkpoint_resume_e2e.py`).
- **C10 Sustained commit-race loss:** ✅ Done — fail *every* commit; assert the bounded checkpoint rounds
  are exhausted, the run surfaces `commit_race_lost`, and the table is a byte-for-byte no-op (O9)
  (`test_native_checkpoint_resume_e2e.py`).

Implement injection via a wrapper catalog/FileIO in a Go integration test that fails on the Nth call, and
at the Python e2e level by interleaving Trino/PyIceberg writes with the native run.

### 8.6 Cost & performance assertions (§6 is testable)

- **P1 Scope containment:** compaction reads only the scoped partitions' files (assert via object-access
  count); a full-table read on a partitioned fixture is a failure.
- **P2 Request-count reduction:** GET requests to scan the compacted partition **decrease** post-compaction
  (compare a Trino scan's split count before/after).
- **P3 No storage growth after cycle:** stored bytes **decrease** after compaction → expire → orphan (the
  storage-transient rule §6). Measuring without the cycle is expected to show a transient increase; assert
  net decrease only after the full cycle.
- **P4 Bounded memory:** compacting `f_part_dense` (high row-density single partition; GeoIP-shaped)
  keeps `cryo-engine-go` RSS under a 1 GiB cap via optional tight `max_bytes_per_group` +
  `scan_concurrency=1` (default `max_bytes_per_group` follows `target_file_size_bytes`; sub-
  `min_input_files` groups are skipped, not rewritten 1→1). Docker validation:
  `scripts/validate_dense_compaction_memory.sh` (`--memory=1g`). Exemplar failure: run
  `858ad9eb` on `geoip.silver_geoip_locations`.
- **P5 No write amplification:** the idempotence run (§8.4.18) writes ~0 new bytes and removes ~0 files.

### 8.7 Differential testing vs Trino/Spark (ground truth)

For each fixture, run the **same** logical optimize via Trino (`ALTER TABLE … EXECUTE optimize`) / Spark
(`CALL … system.rewrite_data_files`) on a **clone**, and via the Go native engine on another clone;
assert both clones have **identical live row multisets** (O2) and comparable file-count reduction. Trino
is the trusted oracle for "correct compaction." This is the strongest guarantee that our iceberg-go
commit matches established engines — **especially for MOR**, where the whole risk is mishandling deletes.

### 8.8 CI gates & the "possible" bar

- **Go:** `go vet`, `golangci-lint run`, `go test ./...` (unit incl. predicate translation, bin-pack math,
  commit/validate guard rails, and fault-injection tests).
- **Python:** `make lint typecheck test` incl. payload/predicate/routing unit tests + the **byte-level
  protocol conformance test** (Go response parses identically to the Rust one).
- **E2E job `native-compaction-e2e`:** brings up MinIO+REST (+Trino/Spark for differential), builds
  `cryo-engine-go`, runs §8.4 COW matrix + §8.5 C1/C4/C5/C6 + C9/C10 (partial-progress commit-race) +
  §8.6 + §8.7. MOR matrix + C2/C3 gated on Phase D.
- **Ship rule:** native OPTIMIZE default-ON only after the **COW gate** is green for ≥N consecutive CI runs
  (no flakes) and a manual "deep validation" (O2 over all fixtures) passes. **MOR default-ON** only after
  the MOR gate + C2/C3 + differential-vs-Trino are green. **The implementation is declared "possible"
  only when the full matrix (functional + concurrency + fault + cost + differential) is green with no
  invariant ever violated.**

---

## 9. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| iceberg-go compaction API churns on the pinned commit | Build/behavior break | Pin exact commit + `go.sum`; §8 e2e re-validates on the pin; upgrade deliberately |
| `optimize_manifests` gap | No native manifest rewrite | Route to Trino/Spark (Phase F opt.1); only build native manifest rewrite behind the full §8 oracle suite |
| v3 partitioned deletion-vector write not ready (#1135) | No native v3-DV compaction | Detect + gate to Trino/Spark; ship v2 pos/eq now; document |
| Non-REST catalog doesn't wrap `ErrCommitFailed` | Retry won't engage | Application-level retry (reload → re-plan → re-run) in `compaction.go`; §5.3 |
| Concurrent DELETE resurrects deleted rows (MOR) | **Corruption** | `serializable` isolation + conflict validation + abort-on-conflict; test C2 |
| Hand-rolled manifest rewrite corrupts table | Data loss | Prefer routing; if built, gate on entire §8 suite incl. differential vs Trino |
| DataFusion cgo path drags in Rust + native lib + FFI hazards | Complexity/regressions | Keep default build pure-Go (`CGO_ENABLED=0`); DataFusion opt-in behind build tag only |
| OOM on large partitions | Crash/perf | Streaming `ToArrowRecords` + rolling writer; test P4 |
| Two engines during transition drift | Confusion/bugs | `CRYO_ENGINE_IMPL` flag; protocol conformance test; retire rust in Phase F |
| Type/precision loss (decimal/ts_ns/uuid/nested) | Silent corruption | `f_types`/`f_nested` O2 round-trip in CI |

---

## 10. References

- **Cryo current state:** [`docs/native-iceberg-rust-engine-errors.md`](./native-iceberg-rust-engine-errors.md),
  [`docs/native-datafusion-compaction-plan.md`](./native-datafusion-compaction-plan.md);
  `cryo-engine/src/*`; `cryo/engines/native/{engine,runner,payload}.py`; `cryo/planner/`;
  `cryo/engines/base.py`; `cryo/worker/worker.py`; `cryo/engines/trino/sql.py`, `cryo/engines/spark/sql.py`.
- **apache/iceberg-go:** [feature status](https://go.iceberg.apache.org/feature-status.html),
  [API](https://go.iceberg.apache.org/api.html),
  [concurrent writes](https://go.iceberg.apache.org/concurrent-writes.html);
  v2 tracking issue [#829](https://github.com/apache/iceberg-go/issues/829);
  compaction issue [#832](https://github.com/apache/iceberg-go/issues/832);
  bin-pack PR [#850](https://github.com/apache/iceberg-go/pull/850);
  delete-file removal in overwrite PR [#851](https://github.com/apache/iceberg-go/pull/851);
  RewriteDataFiles PR [#892], AnalyzeCompaction PR [#893], compact CLI [#903];
  conflict detection/retry [#830](https://github.com/apache/iceberg-go/commit/41822100df472621fec39c60aa12ebc634c38e35);
  MOR delete PR [#721](https://github.com/apache/iceberg-go/pull/721); v3 DV partitioned write [#1135].
- **DataFusion-from-Go (evaluated, not adopted by default):**
  [`datafusion-contrib/datafusion-go`](https://github.com/datafusion-contrib/datafusion-go),
  [`datafusion-ffi`](https://crates.io/crates/datafusion-ffi),
  [`datafusion-contrib/datafusion-c`](https://github.com/datafusion-contrib), Go-binding issue
  [apache/datafusion#2485](https://github.com/apache/datafusion/issues/2485).
- **Trino/Spark parity:** `ALTER TABLE … EXECUTE optimize` / `optimize_manifests`;
  `CALL … system.rewrite_data_files` / `rewrite_manifests` / `expire_snapshots` / `remove_orphan_files`.

---

## Appendix A — glossary

- **COW (Copy-on-Write):** DELETE/UPDATE rewrites whole data files; no delete files. Compaction only
  replaces data files.
- **MOR (Merge-on-Read):** DELETE/UPDATE writes *delete files* (position or equality); readers apply them
  at query time. Compaction must apply deletes on read and drop consumed delete files on commit.
- **Position delete:** removes a row by `(data_file_path, row_position)`.
- **Equality delete:** removes rows matching column values, applied to data files with **lower** sequence
  number than the delete.
- **Deletion vector (v3):** a Puffin-encoded bitmap of deleted positions; the v3 successor to position
  delete files.
- **`RewriteDataFiles` / `Replace`:** iceberg-go's atomic snapshot that adds new files and removes old
  data files (and consumed delete files).
- **Optimistic concurrency (OCC/CAS):** commit asserts the branch is still at the expected snapshot id;
  retries on conflict.
- **Storage transient:** compaction temporarily increases stored bytes until expiry+orphan reclaim the old
  files.

## Appendix B — JSON protocol reference (must match byte-for-byte)

See [§1.3](#13-the-contract-you-must-preserve-the-whole-point-of-the-drop-in-strategy). The Go
`internal/protocol` structs must serialize/deserialize identically to `cryo-engine/src/main.rs`
`ExecuteRequest`/`OperationRequest`/`PredicateRequest`/`PredicateTermRequest` and
`ExecuteResponse`/`OperationResult`. Operation type aliases: `optimize|rewrite_data_files`,
`expire_snapshots`, `remove_orphan_files`, `optimize_manifests|rewrite_manifests`. Progress prefix:
`CRYO_PROGRESS:`; events `{started, progress, completed, failed}` with a non-empty `operation`.

## Appendix C — iceberg-go API cheat-sheet (the calls you'll actually use)

```go
// Catalog + table
cat, _ := catalog.Load(ctx, "cryo", props)                 // REST/Glue/SQL/Hive via `type`
tbl, _ := cat.LoadTable(ctx, catalog.ToIdentifier("schema", "table"))

// Scoped read (delete files applied automatically for MOR)
scan := tbl.Scan(table.WithRowFilter(expr), table.WithSelectedFields(cols...))
tasks, _ := scan.PlanFiles(ctx)

// Plan + execute compaction (COW & MOR) in one atomic commit
groups, _ := compaction.Plan(cfg, tasks)                   // table/compaction bin-pack
txn := tbl.NewTransaction()
res, _ := txn.RewriteDataFiles(ctx, groups, table.RewriteDataFilesOptions{})
newTbl, err := txn.Commit(ctx)                             // OCC + commit.retry.*

// Expire (with file deletion) and orphan cleanup
txn2 := tbl.NewTransaction()
_ = txn2.ExpireSnapshots(table.WithOlderThan(dur), table.WithRetainLast(n))
_, _ = txn2.Commit(ctx)                                    // WithPostCommit(true) to delete files
_, _ = tbl.DeleteOrphanFiles(ctx, table.WithFilesOlderThan(dur), table.WithDryRun(false))

// Seeding MOR fixtures for self-tests
df, _ := txn.WriteEqualityDeletes(ctx, []int{1, 2}, records)
rd := txn.NewRowDelta(nil); rd.AddDeletes(df...); _ = rd.Commit(ctx)
```
Set retry/isolation via table properties: `commit.retry.num-retries=4`,
`write.delete.isolation-level=serializable`. Import `_ "github.com/apache/iceberg-go/io/gocloud"` for S3.

## Appendix D — DataFusion integration options (if ever needed)

Only pursue after the COW+MOR gate is green **and** a real global-sort/z-order need is proven. In
preference order (all keep the **default** image pure-Go):

1. **Do nothing (recommended).** Use iceberg-go's native sort-order enforcement on write (#1157) for
   sorted compaction. Covers most layout needs; zero extra dependencies.
2. **Rust sidecar over Arrow IPC/Flight (best if DataFusion is truly required).** A tiny separate Rust
   binary embeds DataFusion; the Go engine streams Arrow batches to it and reads sorted/clustered batches
   back over Arrow IPC (or Arrow Flight). Clean process/memory isolation; no cgo in the Go binary; the
   sidecar can even reuse the existing `cryo-native` DataFusion crate. Ship it as a separate, optional
   image layer.
3. **`datafusion-contrib/datafusion-go` cgo bridge (last resort).** In-process DataFusion via cgo:
   requires `CGO_ENABLED=1`, a C toolchain, and a per-arch `libdatafusion_go` native library downloaded at
   runtime; "transactions not supported"; young project. Gate behind a Go build tag `//go:build datafusion`
   in a separate package; the default build must never link it. Watch Arrow FFI memory-ownership rules
   (registered tables can outlive the Go call; copy buffers as the library documents).

Whichever option, the **atomic Iceberg commit always stays in iceberg-go** — DataFusion, if used, only
transforms Arrow batches between read and write; it never touches Iceberg metadata.
