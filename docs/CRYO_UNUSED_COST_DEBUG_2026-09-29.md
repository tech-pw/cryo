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

1. **Read the metric correctly first.** AWS spreads a node's idle capacity over its pods in
   proportion to what each pod reserves or uses (§1). So Cryo's "unused share" is roughly **the
   idle fraction of the nodes Cryo runs on**, not a measure of Cryo over-requesting. Trimming
   Cryo's requests lowers Cryo's total cost but barely moves its unused *share*. Track total
   allocated cost and blocked node-hours instead (§6).
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
- **`UnusedCost`** is the instance capacity nobody used. It is "proportionately applied to the …
  Kubernetes pod based on `splitLineItem/SplitUsage`."

It follows, for one node with cost *C*, allocated split total *S* and unused *U = C − S*:

```text
pod_unused = U × pod_split / S
pod_unused / (pod_split + pod_unused) = U / C       ← the node's idle fraction
```

**What this means:**
- **A namespace's unused share is the weighted idle fraction of the nodes it runs on.** Five
  lanes on half-empty 64-vCPU nodes show about 50% unused, however well sized the lanes are.
- **Right-sizing Cryo's requests reduces Cryo's total allocated cost** (split plus unused). It
  does not move its unused share much. That share moves only when Cryo runs on busier nodes, or
  helps nodes go away (consolidation).
- **"Used" in the dashboard means `max(reserved, actual)`,** not measured consumption. A pod
  that reserves 4 GiB and uses 1 GiB is "used" at 4 GiB.
- **A lower namespace allocation is not proof of a lower cloud bill.** The bill falls only when
  node-hours fall.

This affects the standing KPI in `AGENTS.md` ("judge by *unused_cost ratio* trending down"). The
ratio mostly measures node idleness caused by whatever shares Cryo's nodes (Trino here). §6
proposes KPIs that Cryo's own changes can move.

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

**A question worth asking:** lanes are built to survive losing a node. They run on Spot, commit
per table, and the coordinator starts up to `maintenance_max_lane_respawns` (8) replacement
lanes. The table in flight is recorded as failed and the replacement takes the rest
(`cryo/config.py`). So `do-not-disrupt` protects at most **one table's in-progress rewrite** per
lane, and it blocks consolidation for the whole run to do so. Whether that trade is worth it is
measurable (§7, option B3).

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
enough evidence to halve lane memory. To collect better evidence without risk, run a VPA
recommender in `Off` mode on lanes, or record per-table peak RSS in the lane's audit metrics,
then size by table size rather than by tier alone.

---

## 5. Scheduler OOM: separate, open

The scheduler restarted **five** times. Its last termination was **`OOMKilled`** at 05:25:08
UTC, at a **10 GiB** limit (Prometheus showed about 10,143 MiB). The last logs contain catalog
and table errors but don't identify which runner was allocating. **Don't infer that those errors
caused the OOM.**

**Open work:**
- Attribute memory per runner: log RSS per runner tick, or take `tracemalloc` snapshots above a
  threshold.
- Consider moving the heaviest runners into bounded child processes, as the scanner already
  does.
- Bound and resume long-running work.

This investigation did not fix it.

---

## 6. KPIs that Cryo's own changes can move

Keep logging `split` and `unused` daily (the existing `daily-cost-log.csv` workflow), and add:

| KPI | Definition | Why |
|---|---|---|
| **Cryo total allocated $ / day** | `SplitCost + UnusedCost` for namespace `cryo` | Falls when Cryo requests less or runs on cheaper nodes |
| **Reservation efficiency** | Actual ÷ reserved vCPU-hours and GiB-hours, per workload | Cryo's own sizing, independent of node idleness |
| **Blocked node-hours** | Hours in which a node's only consolidation blockers were Cryo `do-not-disrupt` pods (from Karpenter events) | The node cost Cryo can actually release |
| **Cost per unit of work** | Cryo $ ÷ tables maintained, or ÷ GB rewritten (from the batch summary) | Real work legitimately costs money; this separates waste from throughput |
| **Idle worker-hours** | Worker replica-hours while worker-owned admitted+running = 0 | Catches a repeat of §2 |

**Data rules:**
- Count a CUR day only once it has 24 hourly buckets and is at least 48 h old.
- `compare_mom.py` should drop partial days automatically. Today it includes 28 September's
  partial row, so its "lower mean, not significant" result is not evidence of savings.

---

## 7. Options, ranked

Effort and risk are rough. **No production change without owner approval.**

| # | Option | Expected effect | Risk | Evidence before and after |
|---|---|---|---|---|
| A1 | **Ship the worker-metric fix, restore max 10** | Stops a repeat of 10 idle workers without capping ad-hoc capacity | Low; tested, read-only prod check = 0 | Idle worker-hours ≈ 0 over a week; ad-hoc backlog unchanged |
| A2 | Vitals check: "workers > 1 while worker-owned demand = 0 for 30 min" | Early warning if classification drifts again | Low | Fires on a replay of the 29-Sep state; silent on normal days |
| A3 | Event-driven idle workers (block on the queue, or back off polling when idle) | Cuts the 0.2–0.3 vCPU per idle worker | Medium (worker loop) | Idle worker CPU before and after |
| A4 | Scale workers to zero (metric from API or the PostgreSQL scaler; Helm `hasKey`) | Removes the last idle worker | Medium: cold-start delay for ad-hoc runs | First-job latency from zero; idle worker-hours |
| B1 | **Canary `laneMaxNodeCpu=16` for one nightly run** | Lanes stop pinning 64-vCPU nodes | Medium: may add small nodes | Node-hours, consolidation events, pending time, cost per table: canary night vs matched baseline night |
| B2 | Dedicated maintenance NodePool (taint + toleration, ≤16 vCPU Spot, short `consolidateAfter`) | Isolates Cryo's pinning to nodes that die with the run; clean attribution | Medium: loses bin-packing with Trino; platform-team change | Same as B1, plus Trino node count unchanged |
| B3 | Let lanes be disrupted between tables: on SIGTERM, finish or abandon the current table and exit; drop `do-not-disrupt` on lanes | Consolidation can reclaim nodes mid-run | Medium: lost work per disruption, more respawns | Tables failed by disruption, respawns, lane-hours, node-hours across ≥ 3 nights |
| B4 | Put the coordinator on a small, stable node (node selector or affinity) | Stops the coordinator pinning a big node for hours | Low | Coordinator node type and blocked-hours |
| C1 | Per-table memory evidence (lane RSS in audit metrics, or VPA `Off`) | Safe lane right-sizing later | Low | Peak RSS vs table size distribution |
| C2 | Scheduler memory attribution (§5) | Find and fix the OOM | Low to medium | Per-runner RSS; restarts = 0 over a week |
| D1 | Label lane and ephemeral pods with `eks-workload-name` | Moves their cost out of the empty-name bucket (`AGENTS.md` gotcha) | Low | Empty `workload_name` share of Cryo cost falls |

**Suggested order:** A1 → A2 → B4 → C2 → B1 (canary) → B2 or B3 (only if B1 shows blocked
node-hours matter) → A3/A4 → C1. D1 whenever a Helm change goes out.

---

## 8. Validation done in this investigation

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

**Not re-checked:** the metric code and its tests, which weren't readable at the time.

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
- [ ] Update `AGENTS.md`'s cost self-check: replace "judge by unused_cost ratio" with the §6
      KPIs, citing §1.

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
