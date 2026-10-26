# CryoBench V2 protocol

Run ID `20261020-spark-001`, V2 campaign. Written 2026-10-22 (UTC) **before any scored V2 trial ran**. Changes made after the pilots are listed under "Pilot amendments" with the reason; nothing below was changed after scored data was seen.

V1 results (controlled compaction trials, 4.37 GB tables) are preserved unchanged in `benchmarks/eks/results/`. They are archived with SHA-256 sums in `benchmarks/eks/archive/` and the files are read-only.

## Question

Why would an engineering organization adopt Cryo instead of maintaining Iceberg tables with its own scheduled Spark (Airflow-style) jobs? V2 measures automation decisions, compute spent, query outcome and operating cost against matched DIY baselines. It also measures how far Cryo's control plane scales.

## Environment (pinned)

| Component | Pin |
|---|---|
| Cluster | EKS `<benchmark-cluster>`, namespace `cryo-bench` only |
| Storage | `s3://<benchmark-bucket>/warehouse/` only. Every script asserts this prefix. The S3 credentials are broader than the prefix, so this is application-level protection, not IAM isolation. |
| Catalog | `apache/iceberg-rest-fixture@sha256:4d085796…` (Iceberg 1.9.2). JdbcCatalog on SQLite with WAL, on a PVC, `CATALOG_CLIENTS=1`. Restart- and concurrency-tested in V1. |
| Cryo | `ghcr.io/tech-pw/cryo@sha256:b440406d…`, built from upstream `6e2f4c5`. Same deployment as V1. |
| Cryo Spark image | `ghcr.io/tech-pw/cryo-spark@sha256:11143265…` (Spark 4.1.2, Iceberg 1.11.0). Pinned both in the V2 destination and in the worker env, replacing V1's `:latest` tag. |
| Benchmark Spark (DIY arms, generators) | Same Spark image, local mode |
| Query engine | `trinodb/trino@sha256:1565e8ca…` (480). `bench-trino` for phases 1 and 3; `bench-trino-b`, identical config with node anti-affinity, for phases 2 and 6. 6 vCPU / 20 GiB Guaranteed QoS on on-demand r6a.8xlarge. `iceberg.security=read_only`, metadata cache off. |
| Harness | `benchmarks/eks/v2/` (code), `benchmarks/eks/runs/20261020-spark-001/v2/` (applied manifests) |

### Cryo configuration changes for V2, and why

| Change | V1 | V2 | Reason |
|---|---|---|---|
| Spark slot cap (`CRYO_SPARK_K8S_SLOT_CAP` on governor, worker, api, scheduler) | 1 | 3 | Lets Cryo run up to 3 Spark jobs at once. The DIY arms get a comparable CPU budget (see phase 1). Applied with `kubectl set env`, because re-rendering the Helm chart produced unrelated diffs. Recorded in `runs/…/v2/cryo-values-v2.yaml`. |
| Spark image env | `:latest` | digest | Pinning |
| Worker replicas | 1 | 3 (`kubectl scale`; chart default is 2) | A Cryo worker processes one job at a time: `WorkerRunner._tick` calls `process_next()`, which blocks until the Spark application ends. With 1 worker, slot cap 3 has no effect. The phase 1 pilot measured this (10 tables, one job at a time, a second Spark application per table for `expire_snapshots`). For the scored runs, Cryo's effective concurrency matches its slot cap and the DIY arms' 3 cores. |
| Destination | `bench-spark-k8s` with flat keys, which Cryo ignored | new `v2-spark-k8s` with nested `spark_k8s` keys | V1's flat keys were silently ignored. V2 sets the executor ceiling (16 × 4 vCPU × 8 GiB, driver 1 vCPU / 2 GiB). Cryo's own `scale_executor_profile` and `cap_dispatch_profile` still choose the per-job size, so a 1 GB table still gets 1 executor × 0.5 vCPU. |
| A3 preset | V1 `bench-optimize-default` (params `{}`) | `v2-a3-default`: a field-for-field copy of Cryo's shipped default preset `default-spark-compact` (mode standard, operations `["optimize"]`, params `{"min_small_files": 10}`), with `cooldown_hours: 24` removed | One benchmark cycle stands in for one day. A 24-hour wall-clock cooldown would stop Cryo from acting in every cycle after the first. This is the only policy field changed. |
| Namespace ResourceQuota (my own guardrail from V1) | 16 / 40Gi limits | 48 vCPU / 140Gi limits, 40 / 120Gi requests (raised twice, 2026-10-22) | Lets phases run in parallel: same total work, less wall time. Cost is tracked in the budget ledger below. |
| Scanner cron | `0 2 * * *`, `num_chunks=4` | unchanged in phases 1–3; disabled before the scheduler is scaled up in phase 4 | All V2 scans are explicit, with `num_chunks: 1`, which accounts for #154 |

### How Cryo decides which operations run (this build)

A preset's `operations` list is **not** an allowlist in the deployed build (`cryo/planner/execution.py`, changed 2026-10-13). For mode `standard`, the planner decides per table which operations it needs:
- `expire_snapshots` with 7-day retention, or 1–3 days when 100 or more snapshots are retained;
- delete conversion or compaction, based on the delete ratio;
- `optimize`, based on `min_small_files`.

Only an explicit `operations` field on `POST /maintenance/run` bounds the plan.

- **Phase 1 A3** sends no `operations` field, so Cryo makes every decision, as on its cron path.
- **Phase 2** sends `operations: ["optimize"]`. This is a safety bound for the zero-copy clones, which share data files and must never be expired. As a result, phase 2 measures Cryo's optimize decision and its sizing, not its delete-file policy.

### Known Cryo issues and how they are handled

| Issue | Status | Handling in V2 |
|---|---|---|
| [#153](https://github.com/tech-pw/cryo/issues/153) runs stay `queued` | Fixed upstream by [#151](https://github.com/tech-pw/cryo/pull/151), merged after the deployed build | Completion is read from job status, not run status. Run status is recorded as evidence of the bug. |
| [#154](https://github.com/tech-pw/cryo/issues/154) chunked scans skip tables | Open | Every scan uses `num_chunks: 1`. Before planning, the controller checks that Cryo knows every expected table, and aborts otherwise. |
| [#155](https://github.com/tech-pw/cryo/issues/155) Spark-on-K8s jobs record no file counts | Open | Files rewritten come from Iceberg metadata measured by the harness (Trino `$files` before and after), not from Cryo's job record. |
| Scanner metadata-read errors | — | The controller aborts the window if a scan is not `succeeded`, or if any expected table is missing. Zero work is never recorded for an unscanned table. |

## Budget

The cap is $1,000 for the whole campaign, including V1. The benchmark's AWS identity cannot read Cost Explorer, the Pricing API or VPC endpoints (checked 2026-10-22, `v2-aws-probe` Job). So every figure here is modeled. Whether S3 traffic crosses a NAT gateway is unknown, so the worst case is assumed: NAT data processing at $0.056/GB.

| Item | Estimate (upper bound) |
|---|---|
| Prior spend (V0/V1, about 26 h): compute, S3 storage and requests, and about 2 TB of possible NAT traffic | ≤ $160 |
| V2 compute: about 48 vCPU average for about 24 h at about $0.057/vCPU-h (r6a on-demand per-vCPU rate) | ≤ $70 |
| V2 S3 storage: about 1.5 TB for about 2 days | ≤ $3 |
| V2 S3 requests | ≤ $10 |
| V2 NAT worst case: about 5.5 TB moved | ≤ $310 |
| **V2 cap I hold myself to** | **$500** |

The controller does not meter bytes moved. Instead each phase has a planned byte budget, and the results report actual bytes from Trino `physicalInputBytes` and Iceberg file sizes.

## Common methodology

- **Matched inputs.** Arms within a trial get byte-identical data. In phase 1 the same cached DataFrame is appended to every arm. Phase 2 uses zero-copy clones registered from one metadata file. Equivalence is verified, not assumed: Spark health snapshots at decision time, plus a Trino fingerprint per table (`count`, `checksum(id)`, `sum(amount)`, `checksum(id‖status‖amount)`) after every cycle.
- **Order.** Maintenance windows rotate by `(trial + cycle) mod 3` in phase 1, and by trial in phase 2. Query order is shuffled per round with a fixed seed.
- **Warmup and repetitions.** 1 unscored warmup round, then 3 scored rounds per measurement point.
- **Timing boundaries.**
  - DIY window: Kubernetes Job creation to Job completion, including pod scheduling and JVM start.
  - Cryo window: scan request to the last job terminal. Recorded separately: scan time, then `POST /maintenance/run` to the last job terminal.
- **Compute.** Allocated vCPU-seconds and GiB-seconds come from `v2-podacct`, a 5 s pod-list poll recording requests and limits and container start/finish for every pod in the namespace, including Spark executors that are deleted on exit. A pod is attributed to an arm by its name or labels plus the window it ran in; windows never overlap within a phase. Cryo's control plane is always on and is reported as a fixed cost.
- **S3 requests.**
  - Trino: measured per query from the native S3 file-system JMX `total` counter. Client-side counts, not billing.
  - Spark: not measurable here (no CloudTrail data events or S3 access logs). Modeled from files read and written, and labeled modeled.
- **Statistics.** The unit of replication is the trial. For each metric: the per-trial value, then the mean or geometric mean across trials with a 95% t-interval (t = 4.303 for n = 3, 2.776 for n = 5). Latency percentiles are pooled within trial and arm across tables of the same class and query, and reported only where the sample supports them. With 3 trials × 3 rounds × 2 tables = 18 samples per class, query and cycle, p99 is not supported. The results print the sample size beside every percentile.
- **Integrity.** Any fingerprint mismatch between arms, failed commit or failed query is reported, never dropped.
- **No invented numbers.** Anything not measured is `NOT_ESTABLISHED` or `NOT_RUN`, with the reason.

## Phase 1 (P0): autonomous Cryo vs scheduled Spark

### Fleet, per trial and arm (10 tables, unpartitioned, Iceberg v2, zstd)

| Class | Tables | Initial state | Per-cycle ingestion |
|---|---|---|---|
| healthy | 2 | 4 files × ~96.5 MiB | +1 file × ~96.5 MiB |
| moderate | 2 | 150 files × ~1 MiB (5 commits) | +40 files (2 commits) |
| severe | 2 | 600 files × ~1 MiB (10 commits) | +150 files (5 commits) |
| updates (merge-on-read) | 2 | 4 files × ~31 MiB | 5 `UPDATE … WHERE id % 997 = k` commits (≈160 rows each), writing position-delete files |
| lowtraffic | 2 | 12 files × ~1 MiB | none |

Rows are deterministic: seed `7000 + trial`, plus table and batch. The schema is `id, event_date, customer_id, category, status, amount, qty, ts, note`, where `note` is about 1 KB of incompressible base64.

### Arms

| Arm | What runs each cycle | Frozen configuration |
|---|---|---|
| A0 | nothing | — |
| A1 naive scheduled Spark (basic Airflow DAG) | One Spark application, `local[3]`, 3 vCPU / 6 GiB, loops over all 10 tables: `rewrite_data_files(table)` with Iceberg defaults, `rewrite_manifests(table)`, `expire_snapshots(older_than = now − 7 days, retain_last = 1)`. | `v2/p1/spark_p1.py`, `diy()` with `a1` |
| A2 candidate: threshold-gated scheduled Spark | Same application shape and resources. Per table it reads `files` metadata and runs only when needed: if small files (< 32 MiB) > 16 or delete files > 8, `rewrite_data_files` binpack with a 256 MB target and `min-input-files=2`, plus `delete-file-threshold=1` and `remove-dangling-deletes=true` when the delete debt is over target. `rewrite_manifests` runs if manifests > 50. No expire, since 7-day retention makes it a no-op. | `diy()` with `a2`. **Not independently reviewed**, so it is reported as "A2 candidate", never as an expert baseline. |
| A3 Cryo automatic | `POST /scans` (scope = this arm's schema, `num_chunks=1`), then `POST /maintenance/run` for all 10 tables with preset `v2-a3-default` on destination `v2-spark-k8s`. Cryo's scanner stats, planner, governor admission, routing, dynamic sizing and Spark-on-K8s dispatch decide everything else. Slot cap 3. | Preset and destination above |

**Matched resources.** A1 and A2 get 3 dedicated vCPUs for the whole window. Cryo can run 3 concurrent jobs, which for these tables means about 1 vCPU each (driver 0.5 plus executor 0.5) plus a submitter pod. Peak allocation is therefore about 3 vCPU per arm. Actual allocated vCPU-seconds are measured, not assumed.

**Trigger.** The A3 run is started by the controller at the same point in the cycle as the DIY DAGs, not by Cryo's cron. This keeps trigger timing identical across arms, so only the decisions differ. Cryo's cron path is exercised separately in phase 4, where the scheduler builds runs from a rule.

### Predefined targets, identical for every arm

| Target | Definition |
|---|---|
| HT-small | data files smaller than 32 MiB ≤ 16 per table |
| HT-delete | delete files (position or equality) ≤ 8 per table |
| Healthy | HT-small and HT-delete both met |
| Necessary operation | an operation on a table that was not healthy at decision time, measured after ingestion and before any window |
| Unnecessary operation | an operation on a table that was healthy at decision time, or that changed nothing (0 files rewritten, 0 snapshots expired, manifests unchanged) |
| Restored | not healthy at decision time, healthy after the arm's window |
| Query objective (QO) | per table and cycle, median `q_agg` ≤ 1,500 ms, median `q_filter` ≤ 1,000 ms and median `q_point` ≤ 1,000 ms. Fixed a priori from V1 latencies, not tuned to any arm. |
| Freshness SLA | every ingestion commit succeeds; after the cycle, every arm's row count and fingerprint equal A0's; the arm's window finishes within 20 minutes |
| Backlog | tables not healthy at the end of the arm's window, summed over cycles |

### Measurements

Per cycle and arm:
- the window wall time;
- operations run, with Spark job count;
- tables restored, and tables left unhealthy (backlog);
- unnecessary operations;
- bytes rewritten: the sum of data-file bytes removed from the current snapshot (`$files` before and after);
- allocated vCPU-hours and GiB-hours from `v2-podacct`;
- Cryo's split: scan time, planner skips, governor admissions, time from queued to running, and Spark time;
- per table: Trino `q_agg`, `q_filter` and `q_point`, each with 1 warmup and 3 scored runs, latency and S3 requests;
- failures and retries.

### Scale

3 scored trials (1, 2, 3) × 4 arms × 10 tables × 4 cycles, plus a cycle-0 baseline measured before any maintenance. Trial 0 is the unscored pilot.

### Analysis, pre-registered

1. **Unnecessary maintenance avoided.** (A1 unnecessary operations − A3 unnecessary operations) / A1 unnecessary operations, per trial. Mean and CI across trials.
2. **Maintenance compute saved.** 1 − (A3 allocated vCPU-h, all pods Cryo dispatched) / (A1 allocated vCPU-h), per trial. Reported both with and without the Cryo control plane's amortized share.
3. **Query performance.** Per class and query, the ratio of arm to A0 medians in each cycle, aggregated as a geometric mean over trials. A3 is compared with A1 and A2 head-to-head.
4. **Health outcome.** % restored, backlog, and QO and SLA compliance per arm.

The headline claim is published only if A3's query objective and health compliance are equal to or better than A1's. Otherwise the claim is reported as not supported.

## Phase 2 (P0): realistic large table, target-size policy

### Table, per trial

`v2p2_r{t}.base`:
- partitioned by `event_date`, 60 days;
- 17 columns: numeric, decimal, date, timestamp, low- and high-cardinality strings, and free text;
- target physical size `TARGET_GIB` = 100 GiB, calibrated from measured bytes per row;
- days 1–20 written as 4–8 MiB files (4 commits per day), days 21–40 as 16–32 MiB (2 per day), days 41–60 as 64–128 MiB (1 per day);
- then, merge-on-read: a `DELETE` of channel `test` on 3 cold days, and 3 `UPDATE` commits on the 10 hot days;
- plus `dim_product`, 20,000 rows, for joins.

### Arms

Four zero-copy clones registered from the same metadata file:

| Arm | What runs | Preset and configuration |
|---|---|---|
| `a0` | untouched | — |
| `c256` | Cryo default | `v2-a3-default`: Cryo's optimize SQL with a 256 MB target |
| `c128` | Cryo tuned | `v2-p2-t128`: same preset plus `target_file_size_bytes=134217728` |
| `c512` | Cryo tuned | `v2-p2-t512`: `target_file_size_bytes=536870912` |
| `d256` | manual Spark | Cryo's renderer (`cryo.engines.spark.sql.render`) applied to the plan Cryo produced for `c256`, run as a plain local-mode Spark job on the `d256` clone. CPU is matched to Cryo's dispatch profile for the table's size tier: 9 vCPU at 100 GiB. This arm always runs last, because it needs `c256`'s plan. |

Phase 6 runs inside this phase. A foreground loop runs on `a0` (untouched) during a baseline window with no maintenance, during each Cryo compaction, and during the `d256` compaction.

The clones share data files. The presets are optimize-only, and no expire or orphan removal is ever run on them.

### Procedure and queries

**Procedure.** Cryo runs one arm at a time, in an order rotated by trial. Before maintenance, Trino measures `a0` only, since every clone is identical by construction. After maintenance it measures all four arms, with rounds shuffled.

**Queries:**
- `p_agg_all`: a group-by over the whole table;
- `p_day_small`, `p_day_mid`, `p_day_large`: one day in each file-size band;
- `p_point`: an order-id lookup with no partition filter;
- `p_selective`: a customer-id filter, which scans one column of the whole table;
- `p_join`: a 7-day join with the dimension table;
- `p_full`: reads the free-text column of the whole table.

**Scale.** One 100 GiB base table, `v2p2_r1.base`, generated once. The generator runs at about 150k rows/s on 12 cores, so each 100 GiB table takes about 3 hours.

Each of 3 scored trials (1, 2, 3) registers its own fresh set of five clones from that base table and runs every maintenance arm and measurement independently. **The maintenance process is replicated; data generation is not.** Confidence intervals therefore cover compaction and query variance on this dataset, not variance across datasets.

Trial 90 is a smoke test at about 3.5 GB (the requested 1 GiB rounds up to one file per day), unscored. Trial 0 was an aborted smoke test; its partial base table is retained, never used.

**Analysis.** Per query, the speedup of each arm over `a0` with a CI across trials. Also the compaction cost of each arm (allocated vCPU-h, wall time, bytes rewritten), and the tradeoff between the two. Default and tuned results are reported separately.

## Phase 3 (P1): continuous CDC

**Arms.** The same micro-batches are applied to every arm by one stream job (`v2/p3/`):
- **A0:** no maintenance.
- **A1:** the naive scheduled DAG from phase 1, every 30 minutes.
- **A3:** Cryo every 10 minutes. A schema scan, then `POST /maintenance/run` with no `operations` field, so Cryo's planner decides everything.

**Tables per arm.**
- `orders`: merge-on-read, 600k rows initially. Each batch is a `MERGE` of 3,000 updates plus 1,500 inserts.
- `events`: append-only, 3 files of about 0.5 MiB per batch.

**Schedule.** 36 batches at 200 s intervals, about 2 hours.

**Probes.** Trino probes run on `bench-trino-c`, a third identical instance, so they never overlap phase 1's scored measurements. Query set: `c_agg`, `c_point`. Probes run every 10 minutes and at the end.

**Measured:**
- latency over time;
- small-file and delete-file counts every 3 batches;
- maintenance runs, and their operations and outcomes;
- commit conflicts between ingestion and maintenance, with retries;
- allocated vCPU-seconds.

The results report duration and batch count. A 2-hour run is never described as endurance.

## Phase 4 (P1): fleet scalability (real registered tables)

**Fleets.** Real Iceberg tables in the same REST catalog: `v2p4_n100`, `v2p4_n1000` and `v2p4_n10000`. In each fleet, half the tables have 1 file and half have 12 small files; 5 "needle" tables have 130 small files. Ground truth comes from the generator.

**Steps per fleet:**
1. a scoped Cryo scan for discovery, then a second scan for the steady-state rescan;
2. a comparison of scanned stats with ground truth;
3. a cron preset `v2-p4-fleet-N`, built on Cryo planner defaults (`min_small_files=128`), selecting the fleet by a rule on `schema_name`; the scheduler, scaled to 1 replica, builds the run, and the preset is disabled after one firing;
4. measurement of:
   - run build time, and jobs created versus tables (duplicates or misses);
   - planner skips versus dispatches (only the 5 needles should dispatch);
   - planner throughput, admission decisions and failures;
   - controller CPU and memory, from pod metrics where readable.

No synthetic metadata fixtures are needed, because the tables are real. Synthetic simulation is used only if creating 10,000 tables proves impractical, and it would be labeled as such.

**Fixture tables set `gc.enabled=false`.** In standard mode Cryo's planner adds `expire_snapshots` to every table unconditionally (`cryo/planner/planner.py`). On the Spark-on-K8s path every planned job is dispatched as its own Spark application, with no check for an empty expire. A cron fire over 10,000 default tables would therefore launch up to 500 Spark applications per fire, the scheduler's default cap. At slot cap 3 and about 1–2 minutes each, that is hours of wall time per fire and an uncontrolled cost.

Cryo honours `gc.enabled=false` by dropping expire and orphan removal. That isolates the path being measured: discovery, stats, prioritization, run building, admission and the optimize decision. The per-table expire dispatch itself is measured in phases 1 and 3 and reported as a Cryo cost.

**Scheduler defaults that apply:**
- `max_jobs_per_scheduled_run = 500` per cron fire, tables ranked by priority. At N = 1,000 and 10,000, a correct result is that all 5 needle tables are in the first fire.
- 180-day job retention, so no V1 or V2 job rows are pruned.

**Scanner cron.** It is disabled through `PATCH /scanner/config` while the scheduler runs, and restored afterwards.

**Control-plane CPU and memory.** The cluster has no metrics-server. Values come from each control-plane pod's cgroup v2 counters (`cpu.stat` `usage_usec`, `memory.current`), sampled every 15 s with read-only `kubectl exec` (`v2/p4/cp_sampler.sh`).

## Phase 5 (P1): cost and ROI

The costs are separated as follows:
1. S3 storage (bytes from Iceberg metadata);
2. S3 requests (Trino measured, Spark modeled);
3. Spark maintenance compute (allocated vCPU-h and GiB-h × the published on-demand per-vCPU rate);
4. Cryo control plane (allocated resources × time);
5. query compute (Trino CPU-seconds from query stats × rate);
6. snapshot-retained bytes (measured, under the normal 7-day retention, never shortened);
7. net total.

Everything is modeled from published <region> prices and measured usage, and labeled modeled. Break-even and ROI are computed per query frequency.

## Phase 6 (P1): production workload protection

Foreground Trino queries run in a loop on an untouched clone on `bench-trino-b`. This happens:
- during each phase 2 Cryo compaction;
- during a DIY Spark compaction of an extra clone at matched resources (`d256`, if the budget allows);
- during a baseline window with no maintenance.

Measured: foreground p50, p95 and p99 latency, with n stated; failures; and maintenance completion time.

Cryo's governor has no Trino load signal configured in this install. That is reported, not hidden. A run that avoids interference by never executing maintenance counts as not successful.

## Pilot amendments (frozen 2026-10-22 05:40 UTC, before any scored trial)

The pilots ran on 2026-10-21 between 19:21 and 20:33 UTC: phase 1 trial 0 for 1 cycle, and phase 2 trial 90 at about 3.5 GB. Raw logs are in `results/v2/raw/`. Neither is scored. Changes made because of them:

1. **Quota failures were a harness defect.** 4 of the 10 Cryo jobs in the phase 1 pilot window were refused by the namespace ResourceQuota (`exceeded quota … limits.cpu`). That quota is my own guardrail, not Cryo's. The quota is now 80 vCPU / 240 GiB limits.

   Every controller now aborts a trial as **not scorable** if any Cryo job error contains `exceeded quota` (`harness_guard`). Such trials are reported, never silently dropped.
2. **Worker replicas went from 1 to 6.** In the pilot, one worker processed the jobs one at a time. Each phase has its own destination with slot cap 3: `v2-spark-k8s` for phase 1, and `-p2`, `-p3`, `-p4`. Admission concurrency per phase is therefore 3, matching the DIY arms' 3 cores, and 6 workers ensure that workers are never the bottleneck.
3. **Source scans were replaced by schema-scoped scans.** Schema scans also discover new tables (`_discover_candidates` lists the catalog). This keeps scan time proportional to the arm, not the whole catalog.
4. **The phase 2 fingerprint column is configurable** (`FP_ID_COL=order_id`). The pilot's phase 2 Trino fingerprint used phase 1's `id` column and failed. Health metadata and the fingerprint are now recorded independently, so one failing never hides the other.

   For the smoke run itself, all 8 queries returned identical result hashes on all 5 arms.
5. **The `/tables` stats fields are nested under `stats`.** The pilot logged nulls; this is fixed (`tstats`).
6. **Phase 2 generation was much faster than estimated.** The 100 GiB base `v2p2_r1.base` took 32 minutes on 12 cores. It contains 108.4 GB of data in 7,594 data files and 1,044 delete files, with 1,651,017,129 live rows and 145 snapshots. Generation is still done once (see the phase 2 section).

**What the pilot showed about Cryo's default planner** (used for interpretation, not for tuning anything):
- healthy tables get a standalone `expire_snapshots` Spark application;
- low-traffic tables (12 small files, within the protocol's target) get `optimize` plus `expire_snapshots`, because of the shipped preset's `min_small_files=10`;
- moderate and severe tables get `optimize` plus `expire_snapshots`;
- merge-on-read update tables get `convert_deletes` plus `expire_snapshots`.

Every operation runs as its own Spark application.

**Pilot window durations** (1 cycle, not scored):
- A2 candidate: 200 s;
- A1 naive: 556 s;
- A3 Cryo: 848 s, with 1 worker and the quota failures above.

**Cluster access gap.** The operator's API credentials failed from about 21:50 to 05:20 UTC. Jobs inside the cluster were unaffected. Local control-plane cgroup sampling has no data for that period.

**Idle cost during the gap.** Both Trino instances stayed scaled up and idle for about 9 hours. That is included in the cost ledger as waste.

### Amendment after launch (2026-10-22 06:05 UTC)

**First scored attempt aborted.** The first scored attempts of phase 1 (trial 1) and phase 3 (trial 1) were aborted by `harness_guard` after about 30 minutes. The Kubernetes **Services** quota, 12 in my own V1 guardrail, refused Spark driver Services. Every Spark-on-K8s driver creates one.

**Fix.** The Services quota is now 80.

**Relaunch.** Logs of the aborted attempts are kept, unscored, in `results/v2/raw/unscored/`. Phase 1 restarted as trials 4, 5 and 6; three consecutive trial numbers still cover all three window-order rotations. Phase 3 restarted as trial 2.

**Phase 2 unaffected.** Phase 2 had submitted no Cryo work by then and continued.

### Amendment: Cryo database lost (2026-10-22 ~07:00 UTC)

**What happened.** At 06:59 UTC the `cryo-postgres` pod was evicted from its node. It is the same ReplicaSet; node consolidation is the likely cause, but the node and its events are gone, so this cannot be confirmed. The replacement pod started with an empty database. The Postgres Deployment I wrote in V1 (`runs/…/cryo-deps.yaml`) had no volume. Every Cryo record was lost: sources, destinations, presets, tables, runs and jobs, for V1 and V2. This is a harness defect, not a Cryo defect. Benchmark data was not affected: the Iceberg catalog is on its own PVC and S3 was untouched.

**Effect on trials.** Each item below is unscored; its logs are in `results/v2/raw/unscored/dbloss/`.
- **Phase 1 trial 4** crashed with HTTP 500 at the cycle 2 A3 scan. Trials 5 and 6 never started.
- **Phase 2 trial 1** crashed with HTTP 500 during the `c512` arm. `c128` had finished, but its Cryo job records are gone.
- **Phase 3 trial 2** ran to the end, but every A3 window from about 07:05 UTC failed with `HTTP 500 POST /scans`. A3 therefore did no maintenance for the second half of the stream.

V1's results were taken from exported evidence and do not depend on the Cryo database. The V1 archive and its SHA-256 sums are unchanged.

**Fix (08:10–08:15 UTC).**
1. Added PVC `cryo-postgres-data` (gp3, 20 Gi), with `PGDATA` on it and strategy `Recreate` (`runs/…/v2/postgres-pvc.yaml`, `postgres-deploy-after.yaml`).
2. Added `karpenter.sh/do-not-disrupt` to the pod templates of Postgres, Redis, the Cryo API, worker, scanner, governor, iceberg-rest and the three Trino deployments.
3. Re-ran `alembic upgrade head` with the pinned Cryo digest (`cryo-bench-migrate-v2r`). The migration re-seeds the shipped `default-spark-compact` preset; its mode, operations, params and guardrails are identical to the copy logged before the loss.
4. `controller.py ACTION=recover` re-created source `bench-rest`, destination `bench-spark-k8s`, `v2-spark-k8s` and `v2-a3-default` with the recorded configs. S3 keys come from the secret and are never printed.

The env-level Cryo settings (slot cap 3, 6 workers) are on the Deployments and were not affected.

**Relaunch (08:15 UTC).** Phase 1 runs as trials 7, 8 and 9, which still cover all three window-order rotations. Phase 2 runs as trials 2, 3 and 4, cloned from the unchanged base `v2p2_r1.base`. Phase 3 runs as trial 3. Cryo's state starts empty for these trials, just as it would for any table newly registered in a trial.

### Amendment: phase 4 relaunch and analysis notes (2026-10-23)

- **Phase 4 attempt 1 (06:54 UTC)** completed the N = 100 fixtures, scans and accuracy check, then failed at the cron step: the preset rule used operator `eq`, and Cryo accepts `=` (`cryo/domain/predicates.py`). Attempt 2 refused to reuse an existing child job. Both logs are in `results/v2/raw/unscored/`.
- **Attempt 3 (`v2p4-scored-c`, 07:06 UTC)** reuses the never-enabled preset only if it has no runs and no rules. It also logs the full per-class distribution of Cryo's reported stats.
- **N = 100 discovery scan.** Attempt 3's "discovery" scan at N = 100 found tables already registered by attempt 1. Attempt 1's discovery time (156.6 s) is reported alongside it.
- **Small files.** Cryo counts small files only in partitions it flags as needing compaction, so `small_files_count` is not compared with the generator's raw file count. `total_files` is the accuracy measure.
- **Bytes rewritten** comes from Iceberg snapshot summaries (`removed-files-size`) for every arm alike (`v2/analysis/snapshot_audit.py`), because Cryo's Spark-on-K8s jobs report no file metrics (#155).
- **Expiry.** Phase 1 results are reported with and without snapshot expiry: under time compression every expiry is a no-op by construction (verified from the snapshot chains), and A2 has no expiry.

## Amendment 2026-10-26: phase 4 controller lost, resumed for N = 1,000 and 10,000

- `v2p4-scored-c` failed at 2026-10-23 08:16:43 UTC (BackoffLimitExceeded) during the N = 1,000 rescan. Its pod no longer exists although the Job's TTL is 7 days, and its cleanup (`finally`) never ran: the scheduler stayed at 1 replica and the scanner stayed disabled. This is consistent with node removal; Kubernetes events had expired, so the cause is NOT_ESTABLISHED. Controller stdout for that run is lost.
- No maintenance ran in the meantime: preset `v2p4-n1000` was never created, and the only phase 4 cron run is the N = 100 run. Scheduler was set back to 0 and the scanner re-enabled with its recorded prior config on 2026-10-26.
- The two N = 1,000 scans completed in Cryo and are recorded in its `runs` table (saved to `results/v2/raw/p4/p4_runs_from_db_20261026.jsonl`). The resumed controller `v2p4-scored-d` reports them from those run records (`source: cryo_run_record`, seconds = finished_at − created_at) instead of re-running them; fixture creation is skipped for N = 1,000 because the tables exist.
- Harness hardening, no change to what is measured: controller and measurement Job pods carry `karpenter.sh/do-not-disrupt`; SIGTERM raises SystemExit so cleanup runs; the API client re-authenticates on HTTP 401.
- The pod accountant `v2-podacct` reached its 3-day `activeDeadlineSeconds` at 2026-10-24 19:21 UTC and its pod was deleted; records after the last saved copy (2026-10-23 06:57 UTC) are lost. Phase 4 allocated compute for that gap is reconstructed from Job start/completion times × requests and from the always-on control-plane requests, and is labelled as such. `v2-podacct-b` (7-day deadline, do-not-disrupt) started 2026-10-26 07:37 UTC.
