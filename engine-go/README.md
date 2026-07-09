# cryo-engine-go

Go subprocess for Cryo worker native maintenance. Speaks the same JSON stdin/stdout
protocol as the legacy Rust `cryo-engine` (`ExecuteRequest` / `ExecuteResponse`,
`CRYO_PROGRESS:` lines on stdout).

**Default build:** `CGO_ENABLED=0` (pure Go + gocloud FileIO). No DataFusion on the critical path.

## Pinned dependency

| Module | Version | Notes |
|--------|---------|-------|
| `github.com/apache/iceberg-go` | **v0.6.0** | Pinned in `go.mod`; commit `go.sum` is authoritative |

## Operations (truth-in-advertising)

With `CRYO_ENGINE_IMPL=go` (or `destination.config.engine_impl=go`) — **the default** when unset:

| Operation | Analyze | Execute | Notes |
|-----------|---------|---------|-------|
| `expire_snapshots` | ✅ | ✅ | Atomic metadata commit |
| `remove_orphan_files` | ✅ | ✅ | Object-store delete |
| `rewrite_data_files` (OPTIMIZE) **COW** | ✅ | ✅ | `Transaction.RewriteDataFiles` + atomic `Replace` |
| `rewrite_data_files` (OPTIMIZE) **MOR** | ✅ | ✅ | Deletes applied on read; consumed delete files dropped on commit (#851) |
| `rewrite_data_files` **sorted** | ✅ | ✅ | Uses table sort-order metadata on write when `strategy=sort` or `sort_columns` match table order |
| `rewrite_data_files` **z-order** | ✅ | ❌ routed to SQL | Rejected in Go; use Trino/Spark |
| `rewrite_data_files` **v3 partitioned DV** | ✅ | ❌ routed to SQL | Gated until iceberg-go #1135 lands |
| `optimize_manifests` / `rewrite_manifests` | ✅ manifest count | ❌ routed to SQL | Not in iceberg-go; worker routes to `sql_fallback_destination_id` |

Rust `cryo-engine` remains available via `CRYO_ENGINE_IMPL=rust` for expire/orphan parity during
transition; compaction is not supported on Rust.

## Partial-progress checkpointing (`params.partial_progress`)

When `rewrite_data_files` params set `partial_progress: true` (the Python payload
normalizer now defaults this on for OPTIMIZE), each compaction group is committed as
its own atomic Iceberg snapshot (Iceberg's `PARTIAL_PROGRESS_ENABLED` precedent) instead of one
all-or-nothing commit. This makes the run **resumable and commit-race-tolerant**:

- Each committed group emits a `CRYO_PROGRESS:{"event":"checkpoint",...}` line carrying
  `committed_groups` / `total_groups` — durable progress the worker records.
- On a retryable commit conflict (lost optimistic-concurrency race), the engine reloads the table
  and **re-plans from the current snapshot**. Groups already committed (by us or a concurrent
  writer) are no longer selected, so a lost race only forfeits the current group's attempt — not the
  whole run. Rounds are bounded by `appCommitRetries`.
- If a group still cannot land after the bounded rounds, the operation fails with a
  `commit_race_lost: …` error (`errCommitRaceLost`). The Python side (`runner.is_commit_race_lost`)
  turns this into a first-class "compaction is losing the commit race on table X" signal rather than
  a silent retry loop.

### File-group planning + memory bounds

File grouping matches Spark/Iceberg `rewrite_data_files` semantics (engine-side; Cryo's Python
planner only scopes **partitions**):

1. iceberg-go `PlanCompaction` bin-packs candidates toward `target_file_size_bytes`, requiring
   `min_input_files` per bin (default **2**).
2. Cryo may split oversized bins for memory (`max_files_per_group` / `max_bytes_per_group`).
3. After split, groups with fewer than `min_input_files` are **dropped** — never rewritten 1→1.

| Param | Default | Effect |
|-------|---------|--------|
| `target_file_size_bytes` | `256 MiB` | Bin-pack / writer target output size |
| `min_input_files` | `2` | Spark-style floor: skip groups with fewer files (distinct from planner `min_small_files`) |
| `scan_concurrency` | `1` | Caps parallel Parquet→Arrow readers (`WithCompactionScanConcurrency`) |
| `max_files_per_group` | `8` | Splits oversized bin-pack groups so each rewrite/commit processes ≤ N input files |
| `max_bytes_per_group` | `= target_file_size_bytes` | Compressed-byte memory cap; defaults to the output target so splits do not shred bin-pack groups. Lower for dense tables; sub-min groups are then skipped |
| `partial_progress` | `true` | Commits each (split) group independently so an OOM mid-run keeps prior groups |

The single-commit path remains available by setting `partial_progress: false`; MOR on non-REST
catalogs still retries the whole rewrite on conflict when that path is used.

## Optional PyO3 stack (out of scope here)

`cryo-native/` / `cryo_native` is a **separate** Rust+DataFusion PyO3 module for direct
`compact_files()` helpers and unit tests. It does **not** run in the worker subprocess path.

## Build & test

```bash
make build-engine-go          # engine-go/bin/cryo-engine-go
make test-engine-go           # go test ./...
make test-native-e2e-go       # expire + orphan subprocess e2e
make test-native-compaction-e2e-go   # COW + MOR compaction e2e
```

Set `CRYO_ENGINE_IMPL=go` and `CRYO_ENGINE_BINARY=engine-go/bin/cryo-engine-go` for Python harnesses.

## Protocol

See `docs/native-iceberg-go-compaction-plan.md` §1.3 and `internal/protocol/protocol.go`.
