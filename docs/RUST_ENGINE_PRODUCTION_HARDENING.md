# Native Rust Engine - Production Hardening Guide

This document outlines the production hardening roadmap for Cryo's native Rust compaction engine.

## Current Status

The native Rust engine (`cryo-native`) has a working MVP that demonstrates 20× speed improvement over Spark for bin-pack compaction. The skeleton exists in:

- `cryo-native/` (Rust crate)
- DataFusion + Arrow for in-memory processing
- PyO3 bindings for Python integration
- PyIceberg integration for metadata commits

## Production Hardening Checklist

### 1. Error Handling & Resilience

- [ ] Comprehensive error types (`CompactionError`, `IOError`, `MetadataError`)
- [ ] Retry logic for transient S3 errors (with exponential backoff)
- [ ] Partial file recovery (checkpoint completed files, resume on failure)
- [ ] Graceful handling of schema evolution mid-compaction
- [ ] Memory pressure detection and graceful degradation

### 2. Observability

- [ ] Structured logging (JSON format for log aggregation)
- [ ] Prometheus metrics export:
  - `cryo_native_compaction_duration_seconds`
  - `cryo_native_files_processed_total`
  - `cryo_native_bytes_read_total`
  - `cryo_native_bytes_written_total`
  - `cryo_native_memory_usage_bytes`
- [ ] OpenTelemetry tracing integration
- [ ] Progress reporting (files processed / total)
- [ ] Detailed error context for debugging

### 3. Performance Optimization

- [ ] S3 multipart upload for large output files (>5GB)
- [ ] Parallel file reading (async I/O with tokio)
- [ ] Memory-mapped file I/O for large Parquet files
- [ ] Adaptive sort buffer sizing based on available memory
- [ ] Z-order space-filling curve optimization (Hilbert curve)
- [ ] Bloom filter generation during compaction
- [ ] Column statistics computation (NDV, min/max, null count)

### 4. Distributed Mode

- [ ] Coordinator-worker architecture (gRPC communication)
- [ ] Work-stealing scheduler for load balancing
- [ ] Partition-level parallelism across workers
- [ ] Fault tolerance (worker failure detection & task reassignment)
- [ ] Redis-based coordination for multi-replica deployment

### 5. Testing & Validation

- [ ] Property-based testing (proptest) for compaction correctness
- [ ] Fuzzing (cargo-fuzz) for parser robustness
- [ ] Benchmark suite against Spark baseline
- [ ] Integration tests with real Iceberg tables
- [ ] Schema compatibility matrix tests (v1/v2/v3, nested types, partitions)
- [ ] Correctness validation (row count, checksum, metadata consistency)

### 6. Deployment

- [ ] Docker image with optimized Rust binary
- [ ] Helm chart for Kubernetes deployment
- [ ] Resource requests/limits tuning guide
- [ ] Auto-scaling based on queue depth
- [ ] Health checks and readiness probes
- [ ] Graceful shutdown (finish in-progress compactions)

### 7. Configuration & Tuning

- [ ] Configurable memory limits (per-worker, per-partition)
- [ ] Tunable sort buffer size
- [ ] Configurable parallelism (threads per worker)
- [ ] S3 client configuration (connection pool, retry policy)
- [ ] Compaction strategy selection (bin-pack, sort, z-order)

### 8. Security

- [ ] IAM role-based S3 access (no long-lived keys)
- [ ] Credential rotation support
- [ ] Audit logging for all operations
- [ ] Table-level access control (inherited from Cryo API)

### 9. Compatibility

- [ ] Iceberg format version support (v1, v2, v3)
- [ ] Deletion file handling (equality deletes, position deletes)
- [ ] Partition evolution support
- [ ] Time travel queries (read historical snapshots)
- [ ] Schema evolution (add/drop/rename columns)

### 10. Documentation

- [ ] Deployment guide
- [ ] Performance tuning guide
- [ ] Troubleshooting guide
- [ ] API reference (Rust docs + Python bindings)
- [ ] Benchmarking methodology

## Benchmarking Targets

| Metric | Target | Current |
|--------|--------|---------|
| 50 GB table compaction | <5 minutes | ~4 minutes (MVP) |
| 500 GB table compaction | <30 minutes | Not tested |
| Memory usage (per worker) | <2 GB | ~1.5 GB (MVP) |
| Cost per TB compacted | <$1 | ~$0.80 (estimated) |
| Speedup vs Spark | 15-20× | ~20× (bin-pack only) |

## Effort Estimate

- **Error handling & resilience**: 2 weeks
- **Observability**: 1 week
- **Performance optimization**: 3 weeks
- **Distributed mode**: 4 weeks
- **Testing & validation**: 2 weeks
- **Deployment & docs**: 1 week

**Total**: ~3 months for full production hardening

## Priority Order

1. **P0**: Error handling, logging, metrics (weeks 1-3)
2. **P1**: Performance optimization, S3 multipart upload (weeks 4-6)
3. **P2**: Distributed mode, fault tolerance (weeks 7-10)
4. **P3**: Advanced features (z-order, bloom filters) (weeks 11-12)

## Success Criteria

- [ ] Processes 100+ tables/day in production without manual intervention
- [ ] <1% failure rate (transient failures auto-retry)
- [ ] 15× faster than Spark on average workload
- [ ] <$1/TB cost (spot instances)
- [ ] All integration tests pass
- [ ] Production deployment running for 30 days without incidents
