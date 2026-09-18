# Cryo capacity check — September 18, 2026

## Implementation update

Implemented locally after the check:

- Production uses scanner KEDA with minimum 1 and maximum 8; per-pod resources are unchanged.
- Added the missing `cryo-scanner-worker` Service used by the KEDA metric URL.
- Deployment `spec.replicas` is omitted whenever either scaler is enabled. KEDA takes
  precedence over the plain HPA, including when both flags are true.
- KEDA's generated HPA uses a 300-second downscale stabilization window and removes at most
  one replica per 60 seconds. This controls scale-down among positive replica counts;
  `cooldownPeriod` alone is not used as that protection.
- Chart regression tests cover all four scaler-flag combinations, Service selection/port,
  metric URL, production bounds, unchanged limits, and scale-down behavior. Existing scanner
  demand/chunk accounting tests are included in validation.

Validation: **20 focused tests pass**, including stale-chunk recovery; production Helm lint
passes, as do lint/format for the new test file and diff whitespace checks. These are local
tests, not a live burst/drain experiment. Full-repository lint/typecheck still report findings;
the chart implementation does not make the entire repository release-ready by itself.
The full test run recorded 2,508 passed and six failures: four initial chart-test fixture
failures (missing production image digest) were corrected and pass in the final focused rerun;
two fleet-trend aggregation tests remain failing. The whole suite was not rerun after that
fixture correction. Full lint reported 146 findings and typecheck 28 errors in 12 files.

Production rollout is still pending. The diagnostic observations below describe the earlier
live state. This source change does not certify active-scan drain behavior in production or
actual billing savings. Roll out while scan demand is zero, verify KEDA health and service
reachability, then observe idle floor and a subsequent scan burst/recovery cycle. To revert,
set `autoscaling.keda.enabled=false` and `autoscaling.enabled=true`, restoring the HPA floor 3.

Read-only production inspection around 07:45 UTC, `infra:data-platform-cluster`, namespace
`cryo`. Current repo HEAD at inspection: `b62aa8b1`. This updates, rather than repeats, the
[September 16 diagnosis](CRYO_UNUSED_COST_FIX_2026-09-16.md).

## Requests versus current utilization

Usage is a point-in-time sample, not a p95/peak sizing recommendation. Requests are per pod.

| Workload | Replicas | CPU / memory request | Observed CPU / memory per pod | Decision |
|---|---:|---|---|---|
| scanner-worker | 3 | 500m / 256Mi | 1m / 117–118Mi | Idle floor is a real tuning opportunity. |
| worker | 10 | 100m / 256Mi | 1–23m / 160–208Mi | Investigate work ownership/blocked execution before reducing maximum. |
| API | 2 | 250m / 256Mi | 2m / 149–173Mi | CPU request candidate after historical peaks; retain two replicas for availability. |
| governor | 1 | 250m / 384Mi | 864m / 1165Mi | Using more than requests; do not reduce. |
| scheduler | 1 | 100m / 512Mi | 1159m / 3893Mi | Using more than requests; address memory behavior rather than cut limits. |
| web | 2 | 100m / 64Mi | 1m / 7–23Mi | Small CPU request opportunity; retain availability. |
| Redis | 1 | 100m / 128Mi | 3m / 11Mi | Needs workload peaks before resizing; small absolute opportunity. |

Memory limits are 3Gi scanner, 6Gi worker/API, 3Gi governor, and 10Gi scheduler. These are
limits, not reserved memory. Do not describe ten worker pods as reserving 60Gi: their combined
memory request is 2.5Gi. Low CPU may also mean waiting for an external engine rather than no work.

The previous fixed-eight-scanner diagnosis is historical: production now has an HPA with a
**three-pod minimum and eight-pod maximum**. The `replicas: 8` value is omitted from the rendered
Deployment while the existing HPA is enabled. Editing that value alone will not reduce live replicas.

## Autoscaler evidence and exact levers

Scanner HPA reports CPU 0% against 70%, memory 46% against 80%, at its three-pod floor.
The deployed `_scanner_scale_metric()` returned **0 pending plus running scan chunks**.
The KEDA scanner template and metric are implemented, but
`scannerWorker.autoscaling.keda.enabled` remains false. Its default minimum is also three.

Preferred canary values after confirming scan drain/recovery:

```yaml
scannerWorker:
  autoscaling:
    enabled: true
    keda:
      enabled: true
      minReplicas: 1
      maxReplicas: 8
      targetValue: "1"
```

Keep `autoscaling.enabled: true` with the present chart: the plain HPA already excludes itself
when KEDA is enabled, while the Deployment omits `spec.replicas` only when this flag is true.
Setting it false, as the stale values comment suggests, would render the fixed eight replicas
and let Helm upgrades reset KEDA's desired count. Fix that ownership condition/comment when
formalizing the configuration; the current template's actual conditions take precedence.

Render-check that exactly one scaling controller is produced and no Deployment replica count
is set. Verify the scanner metric endpoint, pending/running summary reconciliation, scan lease
recovery, shutdown during an active scan, and a real burst before lowering the floor. Transition
away from the existing HPA in a controlled rollout. Do not scale to zero: the metric currently
depends on a scanner pod serving it.

One idle scanner instead of three would release **1 vCPU and 512Mi of requests** at current
per-pod sizing. Keep memory and CPU limits unchanged in the initial canary; measure completion
latency/oldest chunk age and throttling before also lowering CPU requests. Restore the floor to
three if the canary worsens those measures. Node-hour savings require capacity consolidation.

Worker KEDA is currently at its maximum of ten, unlike September 16 when it was at one.
A nearby database snapshot returned:

| State | Dispatch ID present? | Engine | Jobs |
|---|---|---|---:|
| admitted | no | native | 542 |
| admitted | no | spark_emr | 12 |
| running | no | native | 19 |
| admitted | yes | native | 173 |
| running | yes | native | 36 |

These counts explain a high `_worker_scale_metric`, which counts admitted/running jobs with
no dispatch ID. The autoscaler is responding to its input; low worker CPU does not demonstrate
that this input is wrong. Reconcile those jobs with worker ownership, external engine refs,
queue membership, heartbeat age, destination dispatch mode, and dependency waits. Do not
exclude all native jobs: the legacy worker-executed native route can legitimately need workers.
Do not reduce maximum replicas to one merely to hide an admitted-job backlog.

## Billing refreshed and logged

The September 18 CUR pull was written to [daily-cost-log.csv](cost-tracking/daily-cost-log.csv)
before reporting. Latest returned date is September 17; values may still backfill.

| Billing date | Split cost | Unused cost | Total | Unused share |
|---|---:|---:|---:|---:|
| Sep 15 | $7.57 | $25.59 | $33.16 | 77.2% |
| Sep 16 | $6.79 | $13.72 | $20.52 | 66.9% |
| Sep 17 | $5.06 | $13.24 | $18.30 | 72.4% |

September 15 has backfilled from the $22.79 total observed on September 16. Do not interpret
that revision as a new runtime regression. The latest day's unused/split ratio is about **2.62**.
These namespace totals do not identify which current workload produced yesterday's cost.
Retain the separate empty-workload-name and parent-node attribution investigation; this pull
does not establish a new per-workload financial breakdown or causal savings.

## Recommended order

1. Validate and canary scanner demand scaling to one idle replica, preserving scan leases and
   the eight-replica burst ceiling. This is the clearest observed fixed-floor opportunity.
2. Reconcile the worker admission/ownership backlog; fix false or blocked demand at its source.
3. Use seven-day CPU/memory peaks and useful-work throughput to tune API/web requests and
   scanner CPU. Do not cut scheduler/governor memory based on namespace unused cost.
4. Compare requested resource-hours, oldest-work age, OOMs, throughput, namespace cost, and
   actual parent-node hours over complete comparable billing windows.

**Status:** live checks complete and billing log refreshed. Subsequent local chart implementation
is described above; no live replica counts, requests, or production configuration were changed here.
