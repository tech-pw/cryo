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

When `rewrite_data_files` params set `partial_progress: true`, each compaction group is committed as
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

The single-commit path (default) is unchanged; MOR on non-REST catalogs still retries the whole
rewrite on conflict.

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
