# Scanner-worker scale-to-zero (2026-09-22)

Completes §5 of [`CRYO_UNUSED_COST_FIX_2026-09-16.md`](./CRYO_UNUSED_COST_FIX_2026-09-16.md)
("scanner pending+active scaling and safe drain") by removing the last always-on replica of
the `cryo-scanner-worker` Deployment.

## What this Deployment is (and is not)

`cryo-scanner-worker` consumes the **ad-hoc** scan queue — "Trigger scan" from the web UI, or
a `POST` that reaches `create_scan_trigger`. It is **not** the fleet sweep. The 6-hourly
full-fleet scan is `scanner-cronjob.yaml` (`0 */6 * * *`), which brings up its own indexed
pods and exits when they finish. Nothing about the cron path depends on this Deployment.

That distinction is why the fix here is scale-to-zero rather than a cron schedule. Putting the
Deployment on `0 */6 * * *` would keep an idle pod resident on a timer *and* leave a
user-triggered scan queued for up to six hours. Scaling from zero on demand costs only pod
start latency and serves the request immediately.

## Why it was pinned at one replica

The KEDA `ScaledObject` polled `http://cryo-scanner-worker.<ns>.svc.cluster.local:8081/scale-metric`
— the Deployment's **own** Service. At zero replicas that Service has no endpoints, so the
trigger becomes unreachable at exactly the moment it needs to report "work arrived, scale up".
The floor of 1 was there to keep something alive to answer "should there be any pods?", which
is a circular dependency, not a workload requirement.

The metric itself never needed a scanner pod. `pending_scan_chunk_count()` is a plain Postgres
read over `MaintenanceRun.summary` for open scan runs; any process with a DB session can
answer it.

## The change

| | Before | After |
|---|---|---|
| Trigger URL | `cryo-scanner-worker:8081/scale-metric` | `cryo-api:8000/scale-metric` |
| `minReplicaCount` | 1 | 0 |
| Metric definition | `cli.py::_scanner_scale_metric` | `scanner.py::pending_scan_chunk_count`, called by both |

- `pending_scan_chunk_count()` moved to `cryo/db/repositories/scanner.py`. `_scanner_scale_metric`
  is now a `SessionLocal()` wrapper around it, so the CLI health server and the API cannot drift.
- `cryo/api/app.py` serves `GET /scale-metric` returning `{"value": N}` — the same shape the CLI
  health server serves, so `valueLocation: "value"` is unchanged. Unauthenticated alongside
  `/healthz` and `/readyz`: KEDA sends no credentials and the body is a single integer.
- The API is the right host for this: it is always up, and it is where ad-hoc scans are created
  in the first place. If the API is down, nobody can enqueue a scan, so there is nothing to
  scale up for.
- KEDA's `activationTargetValue` stays at its default of `0`, which is exactly the boundary
  wanted — held at zero while the metric is 0, activated by a single pending chunk.

`minReplicaCount` is rendered **without** `| default 3`. Go templates treat `0` as empty, so
`default` would silently rewrite the one value that enables scale-to-zero back into three
always-on pods, with no error anywhere. `test_a_zero_floor_is_not_swallowed_by_a_template_default`
pins this; reintroducing `| default 3` fails it (verified).

## Evidence it is safe

Measured on `data-platform-cluster` before changing anything:

- `ScaledObject` `ACTIVE=False`, HPA showing target `0/1` — KEDA already saw no demand.
- The single pod at **1m CPU / 117Mi** against requests of **500m / 256Mi**.
- No open scan runs (`status in ('accepted','queued','running')`).
- Last genuinely user-triggered scan in `cryo.runs`: **2026-08-25** (`web-table-detail`).
  `api` last fired 2026-08-06, `web-scanner` 2026-07-14. Everything since has been `cron`
  (which does not use this Deployment) or one-off agent verification runs.

So the pod had served no ad-hoc work in 28 days.

## Cost

From the EKS Split Cost Allocation CUR data (`grafana-athena-datasource`, namespace `cryo`,
aggregated across the workload's ReplicaSets):

| Period | `cryo-scanner-worker` $/day |
|---|---|
| 2026-09-12 → 09-17 (8 replicas) | ~0.60 – 0.72 |
| 2026-09-19 → 09-21 (1 replica) | ~0.29 avg |
| after this change, while idle | ~0 |

≈ **$8.60/month, ~$104/year**.

Keep this in proportion. Over the same window the untagged ephemeral bucket
(`resource_tags_aws_eks_workload_name = ''` — the Spark and native dispatch Jobs, which do not
inherit the workload tags a Deployment gets) ran at **$41.41/day unused, 87.3% unused**. That
is ~145x this change and remains the dominant waste in the namespace.

## What to watch after deploy

1. `kubectl get deploy cryo-scanner-worker -n cryo` settles to `0/0` within `cooldownPeriod`
   (120s) of the last scan finishing.
2. Trigger a scan from the UI and confirm it wakes: KEDA notices within `pollingInterval` (15s),
   then pod start. Confirm the chunk is actually consumed, not just that a pod appeared.
3. `kubectl get scaledobject cryo-scanner-worker -n cryo` does not report a trigger error —
   that would mean the API URL is wrong or `/scale-metric` is not reachable.
4. Scan-chunk oldest-age does not grow: cold start adds seconds, and should not compound.

Rollback is `scannerWorker.autoscaling.keda.minReplicas: 1` (the trigger change is independent
and should stay either way — polling the API is strictly more available than polling a
Deployment that can have zero pods).
