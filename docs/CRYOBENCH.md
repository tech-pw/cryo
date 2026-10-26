# CryoBench fixture (synthetic inputs)

> [!NOTE]
> **This is not the published benchmark.** The measured results (Cryo versus scheduled and hand-run
> Spark on fleet health, a 100 GiB table, live CDC and query impact) are the **CryoBench V2
> benchmark** on the docs site: <https://tech-pw.github.io/cryo/benchmark/>. The V2 harness and raw
> results are not in this repository yet; that is tracked in
> [#273](https://github.com/tech-pw/cryo/issues/273). This page describes the **CryoBench
> fixture**, a deterministic synthetic fleet and fault matrix used as test input. It produces no
> performance numbers.

The CryoBench fixture is Cryo's reproducible input and evidence format. It does not turn synthetic
runs into production claims.

There is no hosted summary of the fixture. Regenerate it locally: the fixture records its seed and
SHA-256 workload digest, so two runs of the same version can be compared by digest.

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

The fixture is not a maintenance benchmark.
