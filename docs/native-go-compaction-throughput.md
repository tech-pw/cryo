# Native Go compaction throughput (local MinIO)

Goal: compact a **2–3 GB** dense, fragmented table in **≤20 minutes** on local MinIO
without raising peak RSS above the measured baseline.

## Why local MinIO

Wall time against ap-south-1 S3 from a laptop is dominated by WAN latency. All
speedup decisions must be measured against `make up-datalake-native` MinIO.

## Knobs (OPTIMIZE params → cryo-engine-go)

| Param | Payload default | Notes |
|-------|-----------------|-------|
| `group_concurrency` | `2` | Parallel stage; commits serial. Budget may serialize large groups |
| `compaction_memory_budget_bytes` | `3 GiB` | Admission cap (set to measured baseline RSS for hard RAM-neutral runs) |
| `memory_expansion_factor` | `96` | Estimated RSS ≈ compressed_group_bytes × factor |
| `read_batch_size` | `16384` | In-memory override (never catalog-committed) |
| `parquet_row_group_limit` | `65536` | In-memory override |
| `parquet_row_group_bytes` | `32 MiB` | In-memory override |
| `compression_codec` | `zstd` | Default Iceberg Parquet codec |
| `compression_level` | `3` | Balanced write CPU vs output size / reads (was 1 during speed loop) |
| `metrics_mode` | `truncate(16)` | Cuts `ByteArrayStatistics` cost (auditable) |

## Loop results (2026-07-09, local MinIO)

Fixture: `native_compaction.f_part_dense_perf` (29-col GeoIP-shaped, fragmented).

| Label | Files | Input MiB | Wall s | Peak RSS MiB | MB/s | Notes |
|-------|------:|----------:|-------:|-------------:|-----:|-------|
| baseline | 24 | 16 | 3.35 | 971 | 5.02 | iceberg-go defaults |
| tuned-mem | 24 | 16 | 3.84 | **685** | 4.37 | IO/metrics only → RSS↓ |
| tuned-par2 | 24 | 16 | **2.98** | **888** | 5.64 | +concurrency=2 under budget → wall↓ RSS≤baseline |
| baseline-48 | 48 | 41 | 6.50 | 870 | 6.39 | |
| tuned-par2-48 | 48 | 41 | 6.11 | 1186 | 6.79 | faster but **over** baseline RSS |
| tuned-par2-48b | 48 | 41 | 8.22 | 778 | 5.05 | expansion=96 forces serial → RSS ok, wall↑ |

**Takeaway:** memory IO tuning buys RSS headroom; `group_concurrency=2` wins when the
budget admits ≥2 groups (smaller groups / accurate expansion). When a single group’s
estimated footprint fills the budget, the gate correctly serializes (RAM-neutral).

### Compression level A/B (zstd, same 24-file fixture, 2026-07-09)

| Level | Wall s | Peak RSS MiB | Output bytes | Output/input |
|------:|-------:|-------------:|-------------:|-------------:|
| 1 | 4.73 | 668 | 12 440 488 | 0.707 |
| **3** (default) | 3.43 | 692 | 12 650 381 | 0.719 |

Level 3: wall not worse (noise at this scale), RSS ≈ flat (+24 MiB), output **+1.7%**.
Prefer **3** as the write/read middle line; size difference is small on this fixture.

CPU profile (baseline rewrite): dominated by `pqarrow` encode/decode, `ByteArrayStatistics`,
zstd, and `runtime.memmove` — matches the plan’s attribution.

**Extrapolation:** at ~6 MB/s compressed on MinIO, a 2 GiB compressed table is ~5–6 min
(<< 20 min). Original ~1 h was largely WAN S3 from a laptop.

## Loop commands

```bash
make up-datalake-native
make build-engine-go
make seed-native-compaction-perf-fixture

CRYO_PERF_LABEL=baseline make test-native-compaction-throughput-go
CRYO_ENGINE_PPROF_DIR=tmp/native-compaction-perf/pprof \
  CRYO_PERF_LABEL=baseline-pprof make test-native-compaction-throughput-go

CRYO_PERF_LABEL=tuned CRYO_PERF_GROUP_CONCURRENCY=2 \
  CRYO_PERF_BASELINE_RSS_BYTES=<bytes from baseline> \
  make test-native-compaction-throughput-go
# results → tmp/native-compaction-perf/{results.jsonl,results.csv}
```

Scale fixture with `CRYO_PERF_FIXTURE_FILES` / `CRYO_PERF_FIXTURE_ROWS_PER_FILE` /
`CRYO_PERF_RESEED=1`.

## Correctness gate (each iteration)

```bash
make test-engine-go
make test-native-compaction-perf-go
# full oracle suite when REST+MinIO up:
make test-native-compaction-e2e-go
```

## Implementation map

- Parallel coordinator: `engine-go/internal/ops/compaction_parallel.go`
- In-memory props clone: `engine-go/internal/ops/compaction_props.go`
- pprof: `CRYO_ENGINE_PPROF_DIR` in `engine-go/cmd/cryo-engine-go/main.go`
- Harness: `tests/integration/native/perf_harness.py`
- Fixture: `PERF_DENSE_PARTITION_TABLE` / `ensure_perf_compaction_fixture()`
