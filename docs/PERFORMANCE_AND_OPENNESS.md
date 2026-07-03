# Cryo: Performance & Open Source Strategy

> **Research-backed recommendations for building a high-performance, community-driven
> Iceberg maintenance platform.**

---

## Executive Summary

This document analyzes the optimal technical and community strategy for Cryo based on:
- Competitor architectures (LakeOps, Floe, Firn, Amoro)
- Iceberg ecosystem tooling (iceberg-rust, DataFusion)
- Performance benchmarks (Rust vs Python vs JVM)
- Open source governance best practices (Apache Way)

**Key Recommendation:** Adopt a **hybrid architecture** with:
1. **Rust/DataFusion execution engine** for compaction and data operations (10-95x faster)
2. **Python orchestration layer** for API, scheduling, and user interaction
3. **Apache-style open governance** for sustainable community growth

---

## Part 1: Performance Architecture

### 1.1 Current State Analysis

Cryo's current architecture:
- **Backend:** Python 3.12, FastAPI, SQLAlchemy
- **Scanner:** PyIceberg for metadata reading
- **Execution:** Trino SQL and Spark on EMR
- **Limitations:** JVM startup overhead, GC pauses, cluster provisioning costs

### 1.2 Performance Landscape

| Tool | Language | Execution Engine | Claimed Performance |
|------|----------|------------------|---------------------|
| **LakeOps** | Rust | DataFusion | 95% faster than Spark, 10x cheaper |
| **Firn** | Go | DuckDB subprocess | No JVM, single binary |
| **Floe** | Java | Spark/Trino | Standard JVM performance |
| **Amoro** | Java | Spark/Flink | Standard JVM performance |
| **Cryo** | Python | Spark/Trino | Standard JVM performance |

### 1.3 The Case for Rust + DataFusion

#### Why Rust?

| Benefit | Impact |
|---------|--------|
| **No GC pauses** | Predictable latency, no runtime jitter |
| **Memory safety** | No "mystery leaks" during peak loads |
| **Zero-cost abstractions** | Native performance without overhead |
| **Efficient multithreading** | True parallelism without GIL |
| **Low memory footprint** | 10x less memory than JVM for same workload |

#### Benchmark Evidence

**Rust vs Python (ETL workloads):**
- 10M rows, same transform logic: **Rust 11.3x faster**
- Memory usage: **Rust 1.4x less**
- Throughput: **Rust 10.9x higher**

**Rust vs Spark (compaction):**
- LakeOps benchmark on 5.5TB across 10 tables:
  - Spark: 1,612 seconds
  - Rust/DataFusion: 221 seconds (**86% faster**)
- File count reduction: 101K → 19K (81% reduction)
- Peak throughput: 2,522 MB/s

**iceberg-rust optimizations (2026):**
- Range coalescing: 35% fewer S3 GETs
- File size passthrough: 17% CPU reduction (no `stat()` calls)
- Single-threaded fast path: 2.5x faster for small files

#### Why DataFusion?

Apache DataFusion is a Rust-native SQL query engine built on Apache Arrow:

| Feature | Benefit for Cryo |
|---------|------------------|
| **Arrow-native** | Zero-copy data transfer with iceberg-rust |
| **Vectorized execution** | SIMD-optimized operations |
| **Query optimizer** | Predicate pushdown, projection elimination |
| **Parquet native** | Direct integration, no format conversion |
| **Extensible** | Custom operators for compaction logic |
| **Production-proven** | Used by RisingWave, Spice.ai, LakeSail, Comet |

**DataFusion + Iceberg Integration:**
```
iceberg-rust (native Iceberg implementation)
    │
    └── iceberg-datafusion crate
            │
            ├── TableProvider trait → SQL table access
            ├── Predicate pushdown → Skip irrelevant files
            ├── Limit pushdown → Early termination
            ├── Delete file resolution → MoR support
            └── INSERT INTO → Atomic commits
```

---

## Part 2: Recommended Architecture

### 2.1 Hybrid Architecture Design

```
┌─────────────────────────────────────────────────────────────────┐
│                     Cryo Platform                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Python Orchestration Layer                   │   │
│  │                                                           │   │
│  │  • FastAPI (REST API)                                     │   │
│  │  • Scheduler (cron + event-driven)                        │   │
│  │  • Governor (admission control)                           │   │
│  │  • Web UI (React)                                         │   │
│  │  • PyIceberg (metadata scanning)                          │   │
│  │  • SQLAlchemy (state management)                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              │ gRPC / subprocess                 │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Rust Execution Engine (cryo-engine)          │   │
│  │                                                           │   │
│  │  • iceberg-rust (native Iceberg)                          │   │
│  │  • DataFusion (query execution)                           │   │
│  │  • Arrow (in-memory format)                               │   │
│  │  • object_store (S3/GCS/Azure)                            │   │
│  │                                                           │   │
│  │  Operations:                                              │   │
│  │  ├── rewrite_data_files (compaction)                      │   │
│  │  ├── apply_deletes (delete file resolution)               │   │
│  │  ├── sort_files (z-order, clustering)                     │   │
│  │  └── rewrite_manifests (metadata optimization)            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              External Engines (optional)                  │   │
│  │                                                           │   │
│  │  • Trino (for SQL-based maintenance on existing clusters) │   │
│  │  • Spark on EMR (for very large tables > 1TB)             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Responsibilities

| Component | Language | Responsibility | Rationale |
|-----------|----------|----------------|-----------|
| **API Server** | Python | REST API, auth, routing | Developer productivity, ecosystem |
| **Scheduler** | Python | Cron, event triggers | Integration with existing infra |
| **Governor** | Python | Admission control, load balancing | Business logic, flexibility |
| **Scanner** | Python | Metadata reading via PyIceberg | Sufficient performance for metadata |
| **Web UI** | TypeScript | User interface | Standard web tooling |
| **Execution Engine** | Rust | Compaction, sorting, delete resolution | Performance-critical path |

### 2.3 Why Keep Python for Orchestration?

| Reason | Explanation |
|--------|-------------|
| **Developer velocity** | Faster iteration, easier debugging |
| **Ecosystem** | FastAPI, SQLAlchemy, Celery, etc. |
| **PyIceberg** | Mature, official Apache project |
| **Orchestration isn't the bottleneck** | Scheduling/API adds milliseconds, not minutes |
| **Easier contributions** | Lower barrier for community contributors |

### 2.4 Why Rust Only for Execution?

| Reason | Explanation |
|--------|-------------|
| **Compaction is the bottleneck** | 95% of runtime is data processing |
| **iceberg-rust maturity** | Production-ready, active development |
| **DataFusion fit** | Compaction is a specialized query pipeline |
| **Bounded scope** | Single binary, well-defined interface |
| **Subprocess isolation** | Crashes don't bring down orchestrator |

---

## Part 3: Implementation Roadmap

### Phase 1: Foundation (4-6 weeks)

**Goal:** Prove Rust execution is viable without disrupting existing architecture.

| Task | Description |
|------|-------------|
| Create `cryo-engine` crate | Rust binary with DataFusion + iceberg-rust |
| Implement binpack compaction | Basic file merging without sorting |
| Add subprocess interface | Python calls Rust binary with JSON config |
| Benchmark vs Spark | Validate performance claims on test tables |
| Fallback to Spark/Trino | Keep existing engines for compatibility |

**Success Criteria:**
- Rust compaction completes 5x faster than Spark on 100GB table
- Memory usage < 4GB for 100GB compaction
- Zero data loss (SHA256 verification)

### Phase 2: Feature Parity (6-8 weeks)

**Goal:** Rust engine handles all compaction scenarios.

| Task | Description |
|------|-------------|
| Sort-based compaction | Z-order, single-column sort |
| Delete file resolution | Apply position and equality deletes |
| Partition-aware execution | Parallel compaction per partition |
| Partial progress commits | Resume interrupted compaction |
| Manifest rewriting | Metadata optimization in Rust |

**Success Criteria:**
- Rust engine handles 90% of production compaction workloads
- Spark/Trino reserved for edge cases (very large tables, custom SQL)

### Phase 3: Advanced Features (8-12 weeks)

**Goal:** Surpass competitors with unique capabilities.

| Task | Description |
|------|-------------|
| Distributed execution | Multi-worker compaction for petabyte scale |
| Query-aware sorting | Analyze query patterns, optimize sort order |
| Cost-based planning | Choose binpack vs sort based on ROI |
| Streaming integration | Coordinate with active writers |
| Deletion vector support | V3 format optimization |

### Phase 4: Production Hardening (Ongoing)

| Task | Description |
|------|-------------|
| Chaos testing | Network failures, OOM, partial writes |
| Observability | Prometheus metrics, structured logging |
| Benchmarking suite | Continuous performance regression testing |
| Documentation | Architecture docs, contributor guide |

---

## Part 4: Open Source Strategy

### 4.1 Governance Model Options

| Model | Description | Examples | Recommendation |
|-------|-------------|----------|----------------|
| **BDFL** | Benevolent dictator | Linux, Python | ❌ Not scalable |
| **Corporate-led** | Single company controls | Databricks Delta | ❌ Community distrust |
| **Foundation** | Neutral governance | Apache Iceberg, Amoro | ✅ Recommended |
| **Community-led** | Loose coordination | Many GitHub projects | ⚠️ Initial phase only |

### 4.2 Apache Way Principles

If pursuing Apache Foundation incubation:

| Principle | Implementation for Cryo |
|-----------|------------------------|
| **Community over code** | Prioritize contributor onboarding |
| **Consensus decisions** | Major changes require discussion on dev@ |
| **Meritocracy** | Committer status earned through contributions |
| **Transparency** | All decisions on public mailing list |
| **Vendor neutrality** | No single company dominates PMC |

### 4.3 Recommended Governance Timeline

| Phase | Timeline | Actions |
|-------|----------|---------|
| **Phase 1: Bootstrap** | Months 1-6 | GitHub-based, core team makes decisions |
| **Phase 2: Community** | Months 6-12 | CONTRIBUTING.md, public roadmap, RFC process |
| **Phase 3: Foundation** | Year 2+ | Consider Apache Incubator if community grows |

### 4.4 Building Community

| Activity | Purpose |
|----------|---------|
| **Clear documentation** | Lower barrier to entry |
| **Good first issues** | Onboard new contributors |
| **Architecture docs** | Help contributors understand codebase |
| **Discord/Slack** | Real-time community support |
| **Blog posts** | Share technical decisions, benchmarks |
| **Conference talks** | Build awareness, recruit contributors |
| **Office hours** | Regular maintainer availability |

### 4.5 Licensing Recommendation

| License | Pros | Cons | Recommendation |
|---------|------|------|----------------|
| **Apache 2.0** | Permissive, business-friendly, patent grant | None significant | ✅ Recommended |
| **MIT** | Simple, permissive | No patent grant | ⚠️ Less protection |
| **GPL** | Copyleft ensures openness | Limits commercial adoption | ❌ Too restrictive |
| **BSL** | Time-delayed open source | Community distrust | ❌ Not truly open |

**Recommendation:** Apache 2.0 — aligns with Iceberg ecosystem, enables commercial use,
provides patent protection.

---

## Part 5: Competitive Positioning

### 5.1 Differentiation Matrix

| Feature | Cryo (Proposed) | LakeOps | Floe | Firn | Amoro |
|---------|-----------------|---------|------|------|-------|
| **Open source** | ✅ Apache 2.0 | ❌ Commercial | ✅ Apache 2.0 | ✅ Apache 2.0 | ✅ Apache 2.0 |
| **Rust execution** | ✅ Planned | ✅ Yes | ❌ JVM | ✅ Go/DuckDB | ❌ JVM |
| **Self-hosted** | ✅ Yes | ⚠️ SaaS/VPC | ✅ Yes | ✅ Yes | ✅ Yes |
| **Web UI** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ No | ✅ Yes |
| **Health grades** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ No | ❌ No |
| **Query-aware sort** | 🔜 Planned | ✅ Yes | ❌ No | ❌ No | ❌ No |
| **Continuous mode** | 🔜 Planned | ✅ Yes | ❌ No | ✅ Yes | ✅ Yes |
| **Cost tracking** | ✅ Yes | ✅ Yes | ❌ No | ❌ No | ❌ No |
| **Multi-catalog** | ✅ Yes | ✅ Yes | ✅ Yes | ⚠️ REST only | ✅ Yes |
| **Production-ready** | 🔜 Soon | ✅ Yes | ✅ Yes | ⚠️ Early | ✅ Yes |

### 5.2 Cryo's Unique Position

**"The open-source LakeOps"** — combining:
1. High-performance Rust execution (like LakeOps)
2. Self-hosted deployment (unlike LakeOps SaaS)
3. Apache 2.0 license (full openness)
4. Health grades and cost tracking (unlike Floe/Firn)
5. Web UI and API (unlike Firn)
6. Python ecosystem integration (unlike pure-Rust tools)

---

## Part 6: Risk Analysis

### 6.1 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| iceberg-rust instability | Low | High | Pin versions, contribute fixes upstream |
| DataFusion memory issues | Medium | Medium | Bounded memory, spillable execution |
| Rust learning curve | Medium | Medium | Isolate Rust to execution engine |
| Integration complexity | Medium | Medium | Clean interface, extensive testing |

### 6.2 Community Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Low contributor interest | Medium | High | Strong documentation, marketing |
| Single-company dominance | Medium | Medium | Encourage diverse contributors |
| Fork by competitor | Low | Low | Stay ahead on features, community |
| Maintainer burnout | Medium | High | Build core team, shared ownership |

### 6.3 Business Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LakeOps open-sources | Low | High | Move fast, build community |
| Cloud vendors build native | Medium | Medium | Focus on multi-cloud, self-hosted |
| Iceberg format changes | Low | Medium | Active in Iceberg community |

---

## Part 7: Success Metrics

### 7.1 Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Compaction speed vs Spark | 5x faster | Benchmark suite |
| Memory efficiency | 4x less | Peak RSS during compaction |
| S3 request reduction | 30% fewer | Request count comparison |
| Cost per TB compacted | 10x cheaper | Cloud cost tracking |

### 7.2 Community Metrics

| Metric | Year 1 Target | Measurement |
|--------|---------------|-------------|
| GitHub stars | 1,000 | GitHub API |
| Contributors | 20 | Unique commit authors |
| Production deployments | 10 | Self-reported |
| Discord/Slack members | 200 | Member count |

### 7.3 Feature Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Catalog support | 5 types | REST, Glue, HMS, Polaris, Nessie |
| Maintenance operations | 6 types | All Iceberg procedures |
| Health metrics tracked | 20+ | Scanner coverage |

---

## Appendix A: iceberg-rust Ecosystem

### Key Crates

| Crate | Purpose | Maturity |
|-------|---------|----------|
| `iceberg` | Core Iceberg implementation | Production |
| `iceberg-datafusion` | DataFusion integration | Production |
| `iceberg-catalog-rest` | REST catalog client | Production |
| `iceberg-catalog-hms` | Hive Metastore client | Beta |
| `iceberg-catalog-glue` | AWS Glue client | Beta |

### Recent Performance Improvements (2026)

- **v0.9.0**: DDL support, limit pushdown, predicate pushdown improvements
- **Byte range coalescing**: 35% fewer S3 GETs
- **File size passthrough**: Eliminate `stat()` calls
- **Metadata size hints**: Skip unnecessary metadata lookups

---

## Appendix B: Reference Implementations

### Firn (Go)

```
Architecture:
├── Maintenance daemon (single Go binary)
├── DuckDB subprocess for compaction
├── Iceberg REST API for catalog
└── S3 API for storage

Strengths:
- Zero JVM dependencies
- Simple deployment
- Cloud-agnostic

Weaknesses:
- No UI
- Limited catalog support
- Early stage
```

### Floe (Java)

```
Architecture:
├── Policy engine (Java)
├── Spark/Trino for execution
├── Web UI (React)
└── Multi-catalog support

Strengths:
- Mature policy system
- Good UI
- Many catalogs

Weaknesses:
- JVM-based execution
- Requires Spark/Trino cluster
```

### LakeOps (Rust)

```
Architecture:
├── Control plane (Rust)
├── DataFusion execution engine
├── Telemetry collection
└── SaaS deployment

Strengths:
- Best performance
- Query-aware optimization
- Enterprise features

Weaknesses:
- Not open source
- SaaS-first
- Vendor lock-in risk
```

---

## Appendix C: Decision Log

| Decision | Options Considered | Choice | Rationale |
|----------|-------------------|--------|-----------|
| Execution language | Python, Go, Rust, Java | Rust | Performance, iceberg-rust maturity |
| Orchestration language | Rust, Python | Python | Developer velocity, ecosystem |
| Query engine | Custom, DuckDB, DataFusion | DataFusion | Arrow-native, Iceberg integration |
| License | MIT, Apache 2.0, GPL | Apache 2.0 | Patent protection, ecosystem alignment |
| Governance | BDFL, Corporate, Foundation | Foundation-ready | Sustainability, trust |

---

## Conclusion

Cryo has an opportunity to become **the open-source standard for Iceberg table maintenance**
by combining:

1. **Performance**: Rust/DataFusion execution engine (5-10x faster than JVM)
2. **Usability**: Python API and Web UI (low barrier to entry)
3. **Openness**: Apache 2.0 license with transparent governance
4. **Features**: Health grades, cost tracking, multi-catalog support

The hybrid architecture allows Cryo to:
- Ship faster (Python orchestration)
- Run faster (Rust execution)
- Build community (approachable codebase)
- Compete with commercial tools (performance parity)

**Next Steps:**
1. Create `cryo-engine` Rust crate with basic compaction
2. Benchmark against Spark on representative workloads
3. Publish architecture RFC for community feedback
4. Set up contributor documentation and onboarding

---

## References

1. [Building a Distributed Compaction Engine with Rust + DataFusion](https://medium.com/@amitgil87/building-a-distributed-compaction-engine-for-apache-iceberg-with-rust-datafusion-09faa63c1438)
2. [iceberg-rust Performance Epic](https://github.com/apache/iceberg-rust/issues/2172)
3. [Apache DataFusion](https://datafusion.apache.org/)
4. [The Apache Way](https://apache.org/theapacheway/)
5. [Python vs Rust for Data Engineering](https://medium.com/@saehwanpark/python-vs-rust-arent-we-asking-the-wrong-question-ff1f5d2a47b1)
6. [Floe: Policy-Based Table Maintenance](https://github.com/nssalian/floe)
7. [Firn: Writer-Agnostic Iceberg Maintenance](https://github.com/basekick-labs/firn)
8. [RisingWave DataFusion Integration](https://medium.com/@yingjunwu/blazing-fast-iceberg-queries-with-apache-datafusion-in-risingwave-a406b7720c2d)
