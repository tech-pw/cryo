# cryo-engine

Native Rust execution engine for Cryo Iceberg maintenance operations.

## Overview

`cryo-engine` is a high-performance alternative to the Trino/Spark execution engines in Cryo.
It uses [iceberg-rust](https://github.com/apache/iceberg-rust) and [DataFusion](https://github.com/apache/datafusion)
to execute maintenance operations directly, without requiring an external compute cluster.

## Supported Operations

| Operation | Description | Status |
|-----------|-------------|--------|
| `rewrite_data_files` | Compact small files into larger files (binpack) | ⚠️ Scaffold |
| `expire_snapshots` | Remove old snapshots past retention | ⚠️ Scaffold |
| `remove_orphan_files` | Delete unreferenced files from storage | ⚠️ Scaffold |
| `rewrite_manifests` | Consolidate small manifest files | ⚠️ Scaffold |

**Note:** The current implementation is a scaffold that demonstrates the integration pattern
with iceberg-rust. Full operation implementation requires completing the iceberg-rust table
modification APIs, which are still evolving.

## Building

### Prerequisites

- Rust 1.75+ (install via [rustup](https://rustup.rs/))
- System libraries for OpenSSL (for AWS S3 access)

```bash
# Install Rust if needed
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

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
  "table_ref": "db.schema.table",
  "catalog_uri": "http://localhost:8181",
  "warehouse": "s3://bucket/warehouse",
  "mode": "standard",
  "operations": [
    {"type": "expire_snapshots", "params": {"retention_days": 7}},
    {"type": "rewrite_data_files", "params": {"target_file_size_bytes": 268435456}}
  ]
}' | ./target/release/cryo-engine execute
```

### Input Format

```json
{
  "table_ref": "catalog.schema.table",
  "catalog_uri": "http://iceberg-rest:8181",
  "warehouse": "s3://bucket/warehouse",
  "mode": "light|standard|deep",
  "operations": [
    {
      "type": "expire_snapshots|rewrite_data_files|remove_orphan_files|rewrite_manifests",
      "params": { ... }
    }
  ]
}
```

### Output Format

```json
{
  "ok": true,
  "table_ref": "catalog.schema.table",
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
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ iceberg-rust │  │  DataFusion  │  │ object_store │      │
│  │  (catalog,   │  │  (query      │  │  (S3 access) │      │
│  │   metadata)  │  │   execution) │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## License

Apache-2.0
