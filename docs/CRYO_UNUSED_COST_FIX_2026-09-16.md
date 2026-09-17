# Cryo unused-cost incident: diagnosis and fix plan

Evidence collected September 16, 2026, from `infra:data-platform-cluster`, namespace `cryo`, and Grafana/Athena CUR for `data-platform-eks`. This supplements [the cost reduction plan](CRYO_COST_REDUCTION_PLAN.md). It is a capacity and dependency-failure incident, not just a worker replica problem.

## What is actually happening

Sanitized billing aggregates and pod requests/limits/restart evidence are preserved in
[the evidence snapshot](cost-tracking/unused-cost-evidence-2026-09-16.json). Point-in-time
usage and connectivity observations are recorded below; no source credentials are stored.

The latest retrieved billing day, September 15, shows **$5.21 split cost + $17.58 unused cost = $22.79**, an unused/used ratio of **3.38** and an unused share of approximately **77.1%**. CUR can backfill; this is not a real-time September 16 bill.

September 9–15 sums are **$71.62 split + $423.43 unused = $495.05**, or **85.53% unused**. These refreshed rows, including `cryo-used` and `cryo-unused`, were written to [daily-cost-log.csv](cost-tracking/daily-cost-log.csv). The comparison must use the same complete date window after the fix, not today's partial amount.

| Billing group, Sep 9–15 | Split cost | Unused cost |
|---|---:|---:|
| Empty workload-name bucket | $49.72 | $367.70 |
| Named native Jobs | $14.33 | $29.63 |
| Named Spark Jobs | $1.54 | $13.69 |
| Scanner workers | $2.64 | $4.13 |
| Long-lived workers | $0.83 | $3.05 |
| API | $0.87 | $1.82 |
| Scheduler | $0.38 | $1.03 |

The empty-name bucket represents **86.84% of unused cost** in this window. Do not attribute all of it to native jobs without a pod/resource-ID join. Named deployment tuning alone cannot address the dominant unattributed cost.

AWS split unused cost includes allocation of unused **instance** capacity to pods. It is not a direct measurement of each pod's request minus usage, and lowering a pod request does not necessarily lower the EC2 bill. Verify parent-node consolidation and total cost as well as namespace allocation. [AWS split cost definitions](https://docs.aws.amazon.com/cur/latest/userguide/split-line-item-columns.html), [allocation example](https://docs.aws.amazon.com/cur/latest/userguide/example-split-cost-allocation-data.html).

Also correct the scorecard interpretation: a 3.38× unused/used ratio establishes inefficient allocation, **not negative net savings**. Net savings require attributable S3/Trino savings minus all Cryo costs. Increasing CPU activity merely to lower the ratio would be counterproductive.

## Live findings, ordered by urgency

### 1. Reserved native capacity is waiting on an unreachable catalog

The API pod's independent TCP check also timed out to the same endpoint. This narrows the
issue beyond one native pod, but does not distinguish service failure from a shared network path.

Six observed native pods requested **56,317 MiB (about 55 GiB)** combined. The `kubectl top` sample showed roughly 78–82 MiB each and 0–1m CPU. This is a point-in-time outage sample, not a safe healthy-workload memory target.

Sample pod `cryo-native-5707fd3c611c-p752g` requested **11,662MiB**, with a 16GiB limit. Its mounted payload contained **32 tables**, with only `expire_snapshots` and `remove_orphan_files`. A TCP connection attempt from that same pod to `trino-metastore-v2.penpencil.co:9083` timed out. Logs repeatedly show 45-second catalog-connect failures for both operations on successive tables. No credentials were included in collected evidence.

Its Job has a **14,400-second active deadline** and 21,600-second post-completion TTL. The latter cleans up completed objects; it does not terminate a running pod that is repeatedly waiting. Two 45-second failed operations across 32 serial tables imply roughly 48 minutes of useless reservation before other overhead, even without exhausting the four-hour deadline.

**Immediate fix sequence:** establish whether the endpoint, DNS resolution, routing/security controls, service endpoints, or metastore itself is failing. Test from the same execution network/service account; an API-only health check is insufficient. Record endpoint and route without credentials. Stop *new work for the failing catalog* through a source-scoped admission gate, while keeping unrelated catalogs available. Preserve pending intents and existing receipts; do not turn dependency-outage deferrals into permanent table failures.

**Durable change:** add a catalog-health circuit breaker keyed by source/endpoint and execution environment. On repeated typed connection failures, open it with bounded exponential backoff and jitter. Allow one half-open probe and restore dispatch gradually after successful catalog RPC, not merely TCP success. Cache health so each queued table does not probe separately. Do not infer a shared outage from table-not-found or table-specific permissions errors.

In `cryo/engines/native/batch.py`, stop progressing through the remaining batch after the shared connection-failure threshold. Emit a result for every unattempted member indicating retryable dependency deferral; update watcher reconciliation in the same change so it cannot mark them successful or orphan them. Reconcile already completed members normally. Add a whole-batch deadline and bounded per-operation timeouts.

**Tests:** unreachable metastore with 32 members produces bounded connection attempts; all unattempted members remain retryable; successful earlier members are not replayed; another catalog still runs; half-open probes do not fan out after a governor restart.

### 2. Sizing is based on work the native pod may never execute

`cryo/governor/native_admit.py::admit_native_k8s_jobs` estimates and packs from `job.operations` before `build_native_request` resolves native versus fallback operations. Rewrite requirements can therefore affect a native pod that ultimately receives only cleanup. The sampled payload establishes cleanup-only execution; tracing each member's estimate is still needed to quantify this contribution to its exact request.

**Fix:** resolve exclusions, capabilities, and engine routing before sizing. Size the native executable subset separately from deferred/fallback work. Preserve all required effects in the existing ledger. For an empty native subset, do not accidentally trigger the estimator's empty-operations rewrite default or create an idle native Job. Because chunk size currently depends on packing, use a bounded two-stage plan: preliminary routing/working-set estimate, then final chunk render with an assertion that the executable subset fits the reserved profile. Never shrink a request after admission without reconciling governor accounting.

**Tests:** MOR rewrite routed to Spark does not inflate native cleanup resources; excluded optimize does not contribute; fallback-only plan produces no native pod; mixed operations still get sufficient resources and retained fallback effects.

### 3. Serial batch memory includes a large fixed accumulation allowance

`cryo/engines/native/packing.py::_serial_pod_request` adds **200MiB per additional member**. For 32 members that is **6,200MiB**, before the maximum member estimate. This came from a prior OOM incident, so deleting it or sizing pods to today's 80MiB outage usage would be unsafe.

**Fix:** segregate metadata-only and rewrite batches; record child peak RSS, parent RSS, cgroup anonymous/file memory, member count, and completed work. Set an operation-specific cumulative allowance from healthy batch measurements. Bound batches by predicted duration and memory, not only table count. Compare cost of smaller batches against additional startup cost. Keep the existing conservative rewrite allowance until tests and canaries establish a replacement. Avoid running large-memory members in the same long-lived batch as many small cleanup tables.

**Tests:** healthy 32-member metadata workload, a large manifest table, rewrite-heavy batch, partial failure, and cgroup peak memory through the last member. Acceptance requires fewer requested GiB-hours without OOM, backlog, or per-table cost regressions.

### 4. Metadata-only jobs were also charged rewrite CPU — fixed locally

`estimate_table_resources` computed rewrite CPU from table GB before checking `has_rewrite`. A large table doing only snapshot expiry/orphan cleanup therefore inherited CPU intended for Parquet rewriting.

The local fix moves this term inside the rewrite branch, retaining base CPU, orphan allowance, and the existing memory model. Regression tests cover expiry, orphan removal, and both together across 1GB and 50,000GB tables. **50 native sizing/packing tests pass. Not deployed.** This is one bounded correction; it does not solve metastore connectivity or batch memory.

### 5. Scanner floor is a secondary, measurable opportunity

Three scanner consumers each request 500m CPU/256MiB; the sample showed 1m CPU and 162–184MiB. HPA is already at its floor of three, with CPU 0%/70% and memory 68%/80%. KEDA workers are already at one with metric zero: reducing the worker maximum cannot remove the sampled idle cost.

**Fix plan:** implement scanner demand scaling from **pending plus actively claimed scans**, with queue age and lease-safe shutdown. Start with a one-consumer floor and the existing maximum of eight only after burst/drain tests; avoid zero until the metric can be served independently and cold-start behavior is acceptable. Do not use pending-list length alone: it disappears when a long scan is claimed. Replace the existing HPA when enabling KEDA; never let two controllers scale the same Deployment.

Treat retained process memory as a capacity/safety signal rather than demand. HPA uses the largest replica recommendation across its metrics, so memory can keep replicas high even when CPU work ends. [Kubernetes HPA behavior](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/).

A one-pod floor at the current request would release 1 vCPU/512MiB of baseline reservation. It is not a guaranteed dollar saving and is much smaller than the observed native reservation. CPU request changes need burst CPU/throttling and scan-latency measurements; keep memory limits unchanged initially.

### 6. Scheduler memory is an active reliability incident

The sampled scheduler had **26 restarts**, with the most recent termination explicitly `OOMKilled`, despite a **10GiB limit**. Its live request is only 512MiB. API/governor samples also exceed their small memory requests. Cutting these memory requests or limits is not the fix for this incident.

Complete bounded background scans, independent heavy-runner leases/deadlines, and incremental score/feedback work described in CR-04/05/08. The latest scheduler restart occurred at 10:23 UTC September 16. Capture per-runner RSS and batch cardinality to isolate the trigger; a lower restart count caused by disabling all useful work is not success.

## Implementation and rollout order

| Order | Change | Exit condition |
|---|---|---|
| 1 | Diagnose metastore reachability; source-scoped breaker and bounded batch deferral | Outage causes bounded probes and reserved GiB-minutes, no lost effects, healthy source unaffected. |
| 2 | Route before native sizing; deploy the metadata CPU correction | Rendered pod resources match executable operations; sizing/routing tests and canary pass. |
| 3 | Measure and replace metadata batch overhead; cap batch duration | Healthy workload cost per useful effect decreases, memory headroom preserved. |
| 4 | Fix scheduler OOM loop | At least 48h and full heavy cycles without OOM/repeated full restart work. |
| 5 | Scanner pending+active scaling and safe drain | Idle floor falls, burst completion SLO and retries remain acceptable. |
| 6 | Attribute blank CUR rows by pod/resource/parent-node ID; consolidate nodes | Released requests translate into fewer allocated node-hours, not just shifted costs. |

Do not delete running Jobs or globally pause all maintenance solely to improve the ratio. Cancellation of a running mutation requires engine reconciliation. Roll back sizing/scaling changes on OOM, increased retries, or unacceptable oldest-work age; restore the previous profile without replaying completed effects.

## Measurement and acceptance

Record image/config version and deployment timestamp for each change. Track native requested GiB-hours, failed dependency wait seconds, useful completed effects, OOMs, retries, scanner oldest-item age, and node hours daily. Keep a seven-complete-day cost window, separated from partial/backfilled days.

Initial targets are engineering gates, not savings promises: no continued batch-wide timeout storm after the breaker opens; no lost jobs; no new OOMs; then unused share below 50% and subsequently below 30% while cost per useful maintenance effect falls. Report total node/namespace cost alongside the ratio. Do not certify net value until S3/Trino savings and all operating costs are reconciled.

**Status:** diagnosis and fix plan complete; narrow metadata CPU fix implemented/tested locally. Catalog reachability, source breaker, routing-before-sizing, memory calibration, and scanner scaling remain to implement/verify. No production configuration was changed by this investigation.

**Validation:** 50 targeted native resource/packing tests passed; changed Python files pass
ruff lint/format and the diff passes whitespace checks. With the repository virtualenv on
PATH, full `make test` reports 2,444 passed, 24 failed, 52 skipped, 1 xfailed;
`make lint` reports 132 findings and `make typecheck` 26 errors in 10 files.
These are whole-working-tree results with other pending changes present; no clean-baseline
comparison was performed in this investigation, so the complete release is not certified green.
