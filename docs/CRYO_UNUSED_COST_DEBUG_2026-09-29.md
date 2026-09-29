# Cryo unused cost: 29 September investigation, mitigation and next steps

**Scope:** kube context `infra:data-platform-cluster`, namespace `cryo`; billing cluster tag
`data-platform-eks`.
**Evidence:** CUR through Grafana, Kubernetes, Prometheus and read-only Postgres queries,
preserved in [sanitized evidence](cost-tracking/unused-cost-evidence-2026-09-29.json) and the
[daily log](cost-tracking/daily-cost-log.csv).
**Status:** worker mitigation live (temporary cap); code fix written, **not deployed**; lane
node ceiling implemented, **off by default**; scheduler OOM **open**.

---

## TL;DR

1. **Read the metric correctly first.** AWS allocates otherwise unallocated node capacity to
   pods, separately for CPU and memory (§1). Cryo's unused share is an **allocation signal**,
   not a direct measurement of Cryo's idle resources. Keep tracking it alongside total allocated
   cost, request efficiency, useful work and node consolidation evidence (§6).
2. **Confirmed bug, mitigated:** the worker autoscaler counted nightly-batch jobs as worker work
   and held 10 idle workers. It is capped live at 1. The code fix is written and tested but not
   deployed (§2).
3. **Confirmed blocker:** small `do-not-disrupt` lane pods sat on five 64-vCPU shared nodes and
   blocked Karpenter consolidation. The coordinator pod carries the same annotation. An opt-in
   node-size ceiling exists; it is not enabled (§3).
4. **Don't cut lane memory yet.** Observed peaks reach 70–80% of the 4 and 8 GiB lanes (§4).
5. **Separate incident:** the scheduler was OOM-killed at its 10 GiB limit, cause not attributed
   (§5).

---

## 1. How to read these numbers

EKS split cost allocation, from the [AWS CUR column definitions](https://docs.aws.amazon.com/cur/latest/userguide/split-line-item-columns.html):

- **`SplitUsage`** is the larger of the pod's reserved and actual vCPU or memory.
- **`SplitCost`** is that usage's share of the parent EC2 instance's cost.
- **`UnusedCost`** is the cost of capacity left unallocated under that accounting, distributed
  proportionally to pods. Reserved but idle capacity already counts in `SplitUsage`.

For **one resource dimension, one node and one billing interval**, using the same cost basis,
with cost *C*, allocated split total *S > 0* and unused *U = C − S*:

```text
pod_unused = U × pod_split / S
pod_unused / (pod_split + pod_unused) = U / C
```

**What this means:**
- **Do not extend this identity to a namespace's physical idle percentage.** CPU and memory
  have different cost weights and pod shares; namespace totals combine both across nodes and
  hours. The [AWS worked example](https://docs.aws.amazon.com/cur/latest/userguide/example-split-cost-allocation-data.html)
  calculates the resource dimensions separately.
- **Right-sizing can improve packing, but its allocation effect is conditional.** A request
  reduction below actual usage does not reduce `SplitUsage`. A sole pod can still receive the
  entire node's cost. Reducing reservations can also enlarge the unallocated pool and change
  the unused ratio. Measure the outcome rather than promising a proportional dollar saving.
- **"Used" in the dashboard means `max(reserved, actual)`,** not measured consumption. A pod
  that reserves 4 GiB and uses 1 GiB is "used" at 4 GiB.
- **A lower namespace allocation is not proof of a lower cloud bill.** Compute savings need
  fewer or cheaper instance-hours (or a rate change). Commitments can further separate reduced
  usage, amortized cost and the invoiced amount.

Keep the standing unused-ratio KPI in `AGENTS.md`, but interpret it with §6. Shared Trino nodes
affect allocation; the current evidence does not assign their idle capacity to any one workload.

### Daily cost (CUR via Grafana)

| Billing day (UTC) | Split / allocated | Unused | Total | Unused share |
|---|---:|---:|---:|---:|
| 23 Sep | $8.29 | $26.00 | $34.28 | 75.8% |
| 24 Sep | $2.72 | $7.19 | $9.91 | 72.6% |
| 25 Sep | $1.17 | $1.57 | $2.74 | 57.2% |
| 26 Sep | $1.58 | $1.85 | $3.43 | 53.9% |
| 27 Sep | $1.24 | $2.50 | $3.74 | 66.9% |

- **Completeness:** 23–27 September each have 24 hourly buckets. CUR can still backfill.
  28 September has only 22 buckets ($0.43 so far) and is excluded.
- **Totals:**
  - 21–27 Sep: $96.15, of which $68.38 unused (71.1%).
  - 25–27 Sep: $9.91, of which $5.92 unused (59.7%).
- **Rounding:** rounded components may not add up to the rounded totals.
- **Attribution:** the drop since 23 September is not attributable to this investigation.
  Workload volume changed and earlier fixes landed in between.

---

## 2. Worker autoscaler counted batch jobs: confirmed, mitigated live

**What happened:**
- At the first check, all **433 admitted or running jobs** belonged to nightly-batch runs
  (`summary.pipeline = batch`), and none were worker-owned.
- Batch jobs have a null `dispatch_id`, so the deployed `_worker_scale_metric` counted them.
- KEDA therefore held the worker Deployment at its maximum of **10**, while Spark lanes did all
  the maintenance.
- The idle workers were not free: polling and reconciliation used about **0.2–0.3 vCPU each**
  in one-hour samples, above their 100m request. Adding workers could not speed up the batch.

**Code fix (written, tested, not deployed):** `_worker_scale_metric` joins each job to its run
and excludes `summary.pipeline = batch`.
- It keeps admitted and running worker demand, and the existing `dispatch_id` exclusion.
- Jobs with no pipeline metadata still count as worker work.
- Tests cover ordinary, batch-only, mixed and watcher-owned jobs.
- Run read-only against production, it returned **0**, which confirms the diagnosis.

**Live mitigation:** after a server-side dry run, only the worker ScaledObject's
`maxReplicaCount` was changed, 10 → 1, with a temporary-reason annotation. KEDA reconciled to one
healthy worker. `values-prod.yaml` mirrors it (`native.keda.maxReplicas: 1`, with a dated
comment), so a routine upgrade can't undo it before the fix ships.

- **Released requests:** 900m CPU and 2,304 MiB memory (nine replicas × 100m / 256Mi). This is
  a capacity reduction, not a dollar saving.
- **Trade-off:** the batch continued normally, since it does not use workers. Ad-hoc work now
  has one worker; watch admitted backlog and oldest-job age, and lift the cap if they grow.

**Why the fix isn't deployed:** `deploy/deploy.sh` rejects a dirty working tree, and the tree
holds unrelated concurrent work. The script was not bypassed, and nothing else was published.

### Restore normal scaling after the fix ships

1. Deploy the corrected metric through the normal release path.
2. Verify: batch-only work gives 0; real worker-owned admitted or running jobs still count.
3. Set `native.keda.maxReplicas` and the live `maxReplicaCount` back to 10, and remove the
   `cryo/temporary-worker-cap` annotation.
4. Watch KEDA desired replicas, worker backlog and completions for one nightly cycle.

### Two design limits found while reviewing the template

- **Scale-to-zero is impossible with this design.** The metric is served by the worker pods'
  own `/scale-metric` endpoint (`keda-worker-scaledobject.yaml`); at zero replicas nothing
  answers. To allow zero, serve the metric from `cryo-api`, or use KEDA's PostgreSQL scaler with
  the same query and an activation threshold.
- **`minReplicas | default 1` can't express 0 either:** Helm's `default` treats 0 as unset. Use
  `hasKey` or `kindIs` in the template before offering 0.

Moving the endpoint alone is insufficient: governor admission also considers worker presence.
At zero workers, a metric that sees only admitted work can stay at zero while queued work waits
for a worker. Design a bounded wake-up signal for eligible queued demand and test the complete
`0 workers → new job → wake → admission → completion` path. Keep active work visible, distinguish
an unavailable metric from zero, and measure cold-start latency. KEDA's cooldown governs the
return to zero; HPA behaviour governs scaling above zero. Verify the installed KEDA version
before choosing fields from the reference documentation.

---

## 3. Protected lanes pin very large shared nodes: confirmed blocker

**Evidence:** nine running lane pods were spread over **five `r8g.16xlarge` Spot nodes** (64
vCPU, about 512 GiB each). The sampled lanes requested 1 vCPU / 4 GiB or 2 vCPU / 8 GiB.
Kubernetes events on all five nodes named a Cryo lane's `karpenter.sh/do-not-disrupt`
annotation as the consolidation blocker. That is the annotation's intended behaviour
([Karpenter disruption](https://karpenter.sh/docs/concepts/disruption/)).

**Not established:** all five nodes also ran Trino. Their whole cost is not Cryo's, and they
can't all be removed: Trino's memory needs, other workloads, prices and rescheduling constraints
decide that. The evidence shows a blocker, not the counterfactual saving.

**A second pinned pod:** the maintenance coordinator itself is `do-not-disrupt`
(`maintenance-cronjob.yaml`: "Evicting the coordinator mid-run strands its lanes"). It pins its
node for the whole run, up to `maxRuntimeMinutes` + 1 h.

**Recovery exists, but is not a safe drain protocol.** Lanes execute tables serially and the
coordinator has a default budget of eight replacement lanes **per run**, subject to the remaining
deadline and failure classification. Started work can have partial commits or uncertain receipts;
respawning does not provide exactly-once replay. The Spark lane runner has no between-table
SIGTERM drain handshake. The coordinator's termination handler is separate, and its cleanup can
tear down lanes. `do-not-disrupt` blocks voluntary consolidation, not Spot interruption.

Before B3, require durable operation receipts, commit reconciliation, a stop-taking-work state,
bounded cancellation/grace periods and lock handover only after execution has stopped. Simply
removing the annotation does not tell Karpenter when a table boundary occurs. Bounded lane batches
with checkpointed replacement are another design to evaluate. Coordinator recovery needs its own
validation before removing its protection.

### Implemented, opt-in: a lane node-size ceiling

- **Setting:** `maintenance.laneMaxNodeCpu` / `CRYO_MAINTENANCE_LANE_MAX_NODE_CPU` adds an
  inclusive Karpenter node-vCPU ceiling (`karpenter.k8s.aws/instance-cpu`) for future lanes.
- **Interaction:** it keeps the other placement rules, and combines the ceiling with the
  capacity-type restriction in the same required affinity term.
- **Default:** 0 (off), so clusters without the label behave as before. A ceiling with no CPU
  headroom for system pods is rejected.

**16 is a candidate for a canary, not a production setting.** Moving lanes off nodes that Trino
needs anyway can add nodes and raise the bill. Enable it only after comparing total node-hours,
consolidation events, pod pending time and cost per completed effect (§7). Never kill running
lanes to force a placement test.

---

## 4. Blind memory cuts would hurt maintenance

A three-hour Prometheus sample showed these working-set peaks:

| Lane size | Peak |
|---|---|
| 4 GiB | about 3.2 GiB |
| 8 GiB | about 5.7 GiB |
| One larger lane | about 10.3 GiB |

These are working-set peaks, not a bound on JVM, native and page-cache demand. Lane requests
equal limits, so lowering the request alone means a sizing redesign.

**Keep the current memory and JVM headroom.** The request-versus-instant-usage gap is not
enough evidence to halve lane memory. Record cgroup peak memory and working set alongside JVM
heap/non-heap, table size and operation type. Persistent JVM memory means per-table RSS deltas
are correlations, not isolated table allocations. A VPA recommender in `Off` mode is an optional
source of advice only after validating support for these short-lived Jobs; installing it adds
its own overhead. Keep margin for native allocations and caches when evaluating smaller tiers.

---

## 5. Scheduler OOM: separate, open

The scheduler restarted **five** times. Its last termination was **`OOMKilled`** at 05:25:08
UTC, at a **10 GiB** limit (Prometheus showed about 10,143 MiB). The last logs contain catalog
and table errors but don't identify which runner was allocating. **Don't infer that those errors
caused the OOM.**

**Open work:**
- Sample process RSS around runner ticks and use bounded `tracemalloc` snapshots above a
  threshold. Overlapping runners make RSS deltas correlational; `tracemalloc` does not capture
  all native allocations. Correlate cgroup memory, active runners and input sizes before attribution.
- Consider moving the heaviest runners into bounded child processes, as the scanner already
  does.
- Bound and resume long-running work.

This investigation did not fix it.

---

## 6. KPIs that Cryo's own changes can move

Keep logging `split` and `unused` daily (the existing `daily-cost-log.csv` workflow), and add:

| KPI | Definition | Why |
|---|---|---|
| **Cryo total allocated $ / day** | `SplitCost + UnusedCost` for namespace `cryo`, on a consistent gross/net basis | Measures allocation; verify cluster savings separately |
| **Reservation efficiency** | Actual ÷ reserved vCPU-hours and GiB-hours, per workload | Cryo's own sizing, independent of node idleness |
| **Protected-node exposure hours** | Integrate unique nodes hosting protected Cryo pods; deduplicate multiple pods on one node | Measures exposure, not recoverable savings; events alone do not prove duration or that Cryo is the only blocker |
| **Verified consolidation** | Nodes removed or replaced more cheaply, after accounting for placement, PDBs, disruption budgets and other workloads | Tests whether removing a Cryo blocker actually releases cost |
| **Cost per unit of work** | Cryo $ ÷ tables with verified maintenance effects; separately $ / GB rewritten for rewrite work | Keep metadata-only operations and no-ops separate so work mix cannot manufacture an improvement |
| **Idle worker-hours** | Worker replica-hours while worker-owned admitted+running = 0 | Catches a repeat of §2 |

**Data rules:**
- Treat 24 hourly buckets plus a 48-hour hold as a provisional completeness heuristic, not an
  AWS guarantee. Use export timestamps and cluster-wide hour coverage too: a namespace with
  genuine zero usage need not have a row every hour. Reconcile backfills without duplicate dates.
- `compare_mom.py` should drop partial days automatically. Today it includes 28 September's
  partial row, so its "lower mean, not significant" result is not evidence of savings.

---

## 7. Options, ranked

Effort and risk are rough. **No production change without owner approval.**

| # | Option | Expected effect | Risk | Evidence before and after |
|---|---|---|---|---|
| A1 | **Ship the worker-metric fix, restore max 10** | Stops a repeat of 10 idle workers without capping ad-hoc capacity | Low; tested, read-only prod check = 0 | Idle worker-hours ≈ 0 over a week; ad-hoc backlog unchanged |
| A2 | Vitals check: replicas above configured idle floor while fresh worker-owned demand stays zero beyond scale-down and drain windows | Early warning if classification drifts again | Low; unavailable metrics need a separate alert | Replays the incident; respects intentional minimum replicas and active-job draining |
| A3 | Event-driven idle workers (block on the queue, or back off polling when idle) | Cuts the 0.2–0.3 vCPU per idle worker | Medium (worker loop) | Idle worker CPU before and after |
| A4 | Scale workers to zero (external metric, eligible-queue wake-up, Helm `hasKey`) | Removes the last idle worker | Medium: admission deadlock and cold-start delay unless tested (§2) | First-job latency from zero; active-job protection; metric-outage behaviour |
| B1 | **Canary `laneMaxNodeCpu=16` for one nightly run** | Lanes stop pinning 64-vCPU nodes | Medium: may add small nodes | Node-hours, consolidation events, pending time, cost per table: canary night vs matched baseline night |
| B2 | Dedicated maintenance NodePool (taint + toleration, ≤16 vCPU Spot, short `consolidateAfter`) | Isolates Cryo's pinning to nodes that die with the run; clean attribution | Medium: loses bin-packing with Trino; platform-team change | Same as B1, plus Trino node count unchanged |
| B3 | Build and verify checkpointed lane draining before selectively allowing disruption (§3) | May permit consolidation during long runs | High: partial commits, duplicate execution, lost receipts and exhausted respawn budget | Fault injection at commit/receipt boundaries; no overlapping execution; restart recovery, failed work and node-hours |
| B4 | Put the coordinator on a small, stable node (node selector or affinity) | Stops the coordinator pinning a big node for hours | Low | Coordinator node type and blocked-hours |
| C1 | Lane cgroup/JVM memory evidence, correlated with table and operation | Supports measured right-sizing later | Low for bounded instrumentation | Peak memory and OOM/retry rates by size and operation |
| C2 | Scheduler memory attribution (§5) | Find and fix the OOM | Low to medium | Per-runner RSS; restarts = 0 over a week |
| D1 | Verify existing cost labels reach CUR and dashboard queries | Makes ephemeral work attributable | Low; payer-side activation may be needed | Cost coverage under user-defined label columns, with explicit unknown bucket |

**Suggested order:** A1 → A2 → B4 → C2 → B1 (canary) → B2 or B3 (only if B1 shows blocked
exposure corresponds to feasible consolidation) → A3/A4 → C1. Verify D1 before interpreting
workload-level cost comparisons.

**D1 is an ingestion/query check, not a missing-label implementation.** Lane objects already
call `cost_attribution_labels`, including `eks-workload-name`, `cryo-engine` and `cryo-operation`;
the coordinator also has a workload label. These map to user-defined columns such as
`resource_tags_user_eks_workload_name`, not the AWS-generated
`resource_tags_aws_eks_workload_name`. Check payer activation/export availability and fall back
from the user label to the AWS workload tag, then pod/resource identity. Keep unidentified cost
visible; do not assume every blank AWS workload name is a Spark lane.

### Canary decision record

Change one placement or sizing variable at a time. Record eligible work, completed effects,
rewritten bytes, lane-hours, retries/OOMs, pending-time percentiles, protected-node exposure,
Trino capacity and whole-cluster instance-hours for a matched baseline and canary. Set runtime,
pending-time and failure rollback thresholds from that baseline **before** enabling the canary.
Apply placement changes only to future lanes. Revert for missed maintenance deadlines, scheduling
failure or higher normalized cluster cost without a compensating benefit. Three nights can check
feasibility; they do not establish statistically significant savings. The existing month-over-month
workflow requires at least seven current-month days and still needs comparable, complete inputs.

---

## 8. Validation recorded by the original investigation

These results describe the investigation's checkout and release state, not a new full-suite run
or a deployment attestation. Re-check the current release before restoring scaling.

| Check | Result |
|---|---|
| Focused suite (worker scaling, lane planning/manifest, coordinator) | **83 passed** |
| Ruff lint and format on changed Python files | Pass |
| Whitespace check on the diff | Pass |
| `helm lint` | Pass |
| Render with `maintenance.laneMaxNodeCpu=16` | Forwards the correct env var |
| Corrected worker metric, read-only on production Postgres | Returned 0 |
| After the mitigation | Live HPA and pod inventory show one worker; batch progress visible |
| Repository-wide ruff | 192 errors; 90 files need formatting |
| Repository-wide mypy | 48 errors in 17 files; **none in the changed files** |
| Full non-integration suite | 2 failures, both `test_every_registered_tool_stays_in_tenant[True/False]` in concurrent assistant work. Unrelated; no assistant code was changed to hide them |

The repository-wide ruff and mypy failures must be fixed or attributed before a clean release.

**Re-checked while polishing this doc (config and templates read directly):**
- `values-prod.yaml` sets `native.keda.maxReplicas: 1` with a dated reason.
- `maintenance_lane_max_node_cpu` defaults to 0 with the documented behaviour, and the CronJob
  forwards `CRYO_MAINTENANCE_LANE_MAX_NODE_CPU`.
- The coordinator pod is `do-not-disrupt`.
- The KEDA trigger reads the workers' own `/scale-metric`.

The polishing pass also read the worker-metric implementation/tests, lane recovery code and
cost-label helper. The options above remain proposals; this document edit deploys none of them.

---

## 9. Next actions

- [ ] Land the worker-metric fix in a clean commit and deploy through `deploy/deploy.sh` (owner
      approval), then restore scaling (§2).
- [ ] Add the idle-worker vitals check (A2).
- [ ] Record the §6 KPIs daily from the next complete CUR day. Add a partial-day filter to
      `compare_mom.py`.
- [ ] Decide on the B1 canary night (owner and platform team). Record baseline-night metrics
      first.
- [ ] Open the scheduler OOM attribution task (C2).
- [ ] Extend `AGENTS.md`'s cost self-check with §6's complementary KPIs and §1's interpretation;
      retain the unused-ratio time series.

---

## Sources

- AWS CUR split line items (definitions of `SplitUsage`, `SplitCost`, `UnusedCost`):
  https://docs.aws.amazon.com/cur/latest/userguide/split-line-item-columns.html
- Karpenter disruption and `do-not-disrupt`: https://karpenter.sh/docs/concepts/disruption/
- KEDA ScaledObject spec (min/idle replica counts, activation):
  https://keda.sh/docs/2.21/reference/scaledobject-spec/
- AWS Prescriptive Guidance, workload-tiered node pools:
  https://docs.aws.amazon.com/prescriptive-guidance/latest/eks-cost-optimization/6-1-separate-node-pools-by-application-usage.html
- Repository:
  - `deploy/helm/cryo/values-prod.yaml`
  - `deploy/helm/cryo/templates/keda-worker-scaledobject.yaml`
  - `deploy/helm/cryo/templates/maintenance-cronjob.yaml`
  - `cryo/config.py` (`maintenance_*` lane settings)
