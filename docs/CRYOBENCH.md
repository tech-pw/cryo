# CryoBench evidence harness

CryoBench is Cryo's reproducible input and evidence format. It does not turn synthetic runs into
production claims.

Generate the default customer-shaped fleet and control-plane fault matrix:

```bash
make cryobench-fixture
```

The generated `build/cryobench/fixture.json` contains:

- a versioned, deterministic 12,938-table heavy-tailed fleet;
- the seed and exact SHA-256 workload digest;
- tenants, sources, partition transforms, fragmentation, deletes, snapshots, and query activity;
- a declared 397,000-partition outlier matching the known production shape;
- summary percentiles suitable for run metadata;
- the control-plane fault matrix and exact test evidence for covered scenarios.

The matrix reports PostgreSQL failover, Redis loss, and clock-skew scenarios as partial. Local
fault models prove transaction rollback and retry, queue reconstruction from durable SQL state,
and database-authoritative intent expiry. They do not satisfy the infrastructure gates: real
PostgreSQL failover, Redis process/data loss during active work, and deployed cross-node clock-skew
injection remain open. A 30-day soak report must retain the generated manifest, code revision,
environment inventory, Prometheus export, fault results, and failures; it must not replace missing
production evidence with this fixture.
