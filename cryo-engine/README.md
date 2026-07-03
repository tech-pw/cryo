# cryo-engine

Native Rust execution engine for Cryo Iceberg maintenance operations.

## Status

✅ **Builds successfully** with iceberg-rust 0.10.x (git main branch).

Operations currently analyze table metadata and report findings. Full write operations
depend on iceberg-rust's table modification APIs which are still evolving upstream.

## Supported Operations

| Operation | Description | Status |
|-----------|-------------|--------|
| `rewrite_data_files` | Compact small files into larger files (binpack) | ⚠️ Read-only analysis |
| `expire_snapshots` | Remove old snapshots past retention | ⚠️ Read-only analysis |
| `remove_orphan_files` | Delete unreferenced files from storage | ⚠️ Read-only analysis |
| `rewrite_manifests` | Consolidate small manifest files | ⚠️ Read-only analysis |

## Building

### Prerequisites

- Rust 1.75+ (install via [rustup](https://rustup.rs/))

```bash
# Install Rust if needed
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Build the engine
cd cryo-engine
cargo build --release

# The binary will be at target/release/cryo-engine
```

## Usage

`cryo-engine` reads a JSON execution request from stdin and writes results to stdout.
Progress events are streamed to stderr as JSONL.

```bash
# Execute maintenance operations
echo '{
  "table_ref": "demo.orders",
  "catalog_uri": "http://localhost:8181",
  "warehouse": "s3://warehouse/",
  "mode": "dry_run",
  "operations": [
    {"type": "expire_snapshots", "params": {"retention_days": 7}},
    {"type": "rewrite_data_files", "params": {"target_file_size_bytes": 268435456}}
  ],
  "s3_endpoint": "http://localhost:9000",
  "s3_access_key": "minioadmin",
  "s3_secret_key": "minioadmin"
}' | ./target/release/cryo-engine execute
```

### Input Format

```json
{
  "table_ref": "schema.table",
  "catalog_uri": "http://iceberg-rest:8181",
  "warehouse": "s3://bucket/warehouse",
  "mode": "dry_run|execute",
  "operations": [
    {
      "type": "expire_snapshots|rewrite_data_files|remove_orphan_files|rewrite_manifests",
      "params": { ... }
    }
  ],
  "s3_endpoint": "http://minio:9000",
  "s3_access_key": "...",
  "s3_secret_key": "..."
}
```

### Output Format

```json
{
  "ok": true,
  "table_ref": "schema.table",
  "operations": 2,
  "results": [
    {
      "operation": "expire_snapshots",
      "success": true,
      "snapshots_expired": 5,
      "duration_ms": 1234
    },
    {
      "operation": "rewrite_data_files",
      "success": true,
      "files_rewritten": 42,
      "bytes_removed": 1073741824,
      "duration_ms": 5678
    }
  ]
}
```

### Progress Events (stderr, JSONL)

```json
{"event":"started","operation":"rewrite_data_files","progress_pct":0}
{"event":"progress","operation":"rewrite_data_files","progress_pct":50,"files_processed":21}
{"event":"completed","operation":"rewrite_data_files","progress_pct":100,"files_processed":42}
```

## Integration with Cryo

The Python adapter in `cryo/engines/native/engine.py` invokes this binary as a subprocess:

1. Python builds the JSON request from the `MaintenancePlan`
2. Python spawns `cryo-engine execute` and writes the request to stdin
3. Python tails stderr for progress events (forwarded to run console)
4. Python reads the final JSON result from stdout
5. Python records the result in the audit log

## Development

```bash
# Run tests
cargo test

# Check formatting
cargo fmt --check

# Run clippy lints
cargo clippy -- -D warnings

# Build optimized release
cargo build --release
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      cryo-engine                            │
├─────────────────────────────────────────────────────────────┤
│  main.rs          - Entry point, JSON protocol              │
│  operations.rs    - Operation implementations               │
│  progress.rs      - JSONL progress reporting                │
├─────────────────────────────────────────────────────────────┤
│                    Dependencies                             │
│  ┌──────────────────────┐  ┌──────────────┐                │
│  │ iceberg-rust 0.10.x  │  │ object_store │                │
│  │  (catalog, metadata) │  │  (S3 access) │                │
│  └──────────────────────┘  └──────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

## Dependencies

Uses iceberg-rust from git main branch to get latest fixes for chrono/arrow compatibility:

- `iceberg` + `iceberg-catalog-rest` — Apache Iceberg Rust implementation
- `object_store` — S3/cloud storage access
- `tokio` — Async runtime
- `tracing` / `tracing-subscriber` — Structured logging (JSON to stderr)
- `serde` / `serde_json` — JSON serialization
- `chrono` — Time handling
- `anyhow` / `thiserror` — Error handling

## License

Apache-2.0
