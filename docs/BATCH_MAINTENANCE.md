# Batch maintenance

One CronJob does Iceberg maintenance the way the scanner does scanning:

**schedule → pick tables → start Spark pods sized to the work → run every table → delete the
pods → rescan what changed with the Go scanner → post one Slack message.**

It replaced the maintenance presets on 2026-09-24. All 33 presets were deleted that day. A
backup of the presets, their rules and assignments, plus the 404 jobs cancelled in the
process, is kept outside the repo (see the 2026-09-24 session notes).

## Why it changed

The preset path queued one job per table. Workers then ran **one spark-submit wrapper Job,
one driver and its executors for every SQL statement**. Between 2026-09-15 and 2026-09-22 that
started about 35k driver pods. The pod churn exhausted aws-cni IPs on the nodes, and Spark
was 89% of the `cryo` namespace's spend, 81–87% of it idle (reserved but unused). Resizing
pods could not fix that. The dispatch model itself was the waste.

## How a run works

`python -m cryo.maintenance.run` (module `cryo/maintenance/`):

| Step | Code | What happens |
|------|------|--------------|
| Orphan gate | `coordinator._shared_location_ids` | Refreshes the shared-S3-path snapshot. If the refresh fails *and* the last snapshot is older than `maintenance_shared_snapshot_max_age_hours` (72), `remove_orphan_files` is dropped for **every** table in the run. |
| Select | `selection.py` | Only stored scan stats are used. Tables are excluded if inactive, covered by an operator exclusion rule (`maintenance_exclusions`, see below), on a disabled source, unreadable at last scan, in a maintenance back-off window, not scanned within 3 days, or maintained successfully within `maintenance_cooldown_hours` (72 h in prod since 2026-09-26, so nights rotate through the fleet). The source's schema allow-list applies. Eligible tables either need compaction or have ≥1% delete files. They are ranked by 0.45 × heat (Trino queries over 30 days) + 0.35 × need (small-file and delete ratios) + 0.20 × ROI (small files per rewrite GB, plus projected savings). Heat and ROI are rank-normalized. The top `maintenance_top_n` are kept (prod: 1,500 on 12 lanes since 2026-09-26, up from 500 on 4; the run is bound by its GB·hr budget, not the count, so lanes are the real scope lever). |
| Plan | `manifest.py` | `build_execution_plan` in deep mode, then the Spark renderer. That covers partition-aware chunking, protected snapshots, the shared-location gate and `CORRECT_ORDER`. The planner decides the operations; nothing in the batch path adds or removes any. The run pins Spark's `min-file-size-bytes` to the scanner's small-file threshold (`scan_small_file_mb`, 89.6 MB), so optimize rewrites only the files the table was picked for, and sets `max-file-size-bytes` to 10 × target (`max_file_size_ratio`), so large files aren't rewritten just to be split. `DEFAULT_PARAMS` keep `stats_compute_ndv: False`, so no `compute_table_stats` (a full-table NDV scan) is planned, and turn on `remove_dangling_deletes`, so convert_deletes also drops the delete files its rewrite leaves dangling. |
| Lock | `coordinator._execute` | Redis per-table lock. A table locked by anything else is recorded as `skipped` and never sent to Spark. The coordinator also holds an owner lease on each of its jobs, so neither orphan recovery nor the stale-running reaper touches them. |
| Lanes | `lanes.py` | Lane count is ceil(total rewrite GB, plus 0.5 GB overhead per table, ÷ 200), capped at 4 and at the table count. Tables are dealt in rank order to the least-loaded lane. Each lane is sized by its largest table: <5 GB → 1 vCPU / 4 GiB, <200 GB → 2 / 8 GiB, otherwise 4 / 16 GiB, never above the configured ceiling. |
| Run | `k8s_lanes.py`, `cryo_spark_runner.py --manifest` | One Secret and one Job per lane. The Secret holds the gzip manifest and S3 keys (keys reach the pod only through `secretKeyRef`). The Job runs `spark-submit --master local[N,3]` (three attempts per task) in the Spark image with `do-not-disrupt` set, a capped status store (the lane is one long-lived SparkContext), S3FileIO bulk deletes of 1,000 keys and 32 S3 retries (Iceberg's recommendation for throttled workloads). Lanes schedule on `maintenance_lane_capacity_types` (default `spot,on-demand`: spot preferred, on-demand fallback) in place of the on-demand pin inherited from `spark.k8s.nodeSelector`. Each table's statements run in order. A failed statement skips the rest of *that* table, and the lane moves on to the next table. No table starts in the last 20 minutes of the run budget. |
| Record | `coordinator._apply` | The pod log is read every 30 s. Each `[cryo-result]` line becomes an `audit_log` row (action, OK/FAILED/SKIPPED, engine `spark_emr`, duration, metrics, actor `cryo-batch`). A job is finalized once all its statements have reported. |
| Tear down | `LaneRunner.teardown` | Jobs, Secrets and any stray pods carrying the run label are deleted, whatever state they are in. |
| Rescan | `run._rescan` | `run_scan(table_ids=..., num_chunks=1, full_scan=True)` runs on every table where at least one statement succeeded. `num_chunks=1` matters: `run_scan` otherwise defaults to chunk 0 of 4. |
| Report | `report.py` | One message to the default Slack connection: tables by outcome, data files and size before → after (from the rescan), compaction and cleanup counts, pods used, the top 3 failures, and an orphan-gate warning when the gate was closed. |

### Job statuses

| Status | Meaning |
|--------|---------|
| `succeeded` | Every statement succeeded. |
| `partial` | At least one statement succeeded and one failed, or the lane died after some of the table's statements committed. |
| `failed` | Nothing succeeded, including the table that was in flight when its lane pod died (the error names the pod's termination reason, e.g. `OOMKilled (exit 137)`). |
| `skipped` | Never started: the table was locked, the deadline passed, or its lane ended before reaching it. |

Runs are `kind=scheduled, trigger=batch-cron` for the nightly run and
`kind=adhoc, trigger=batch-adhoc` when `--table-ids` is given. `runs.summary.pipeline` is
`"batch"`. A run is `succeeded` only when no job is failed, partial or skipped. It is
`partial` when at least one table was maintained, and `failed` otherwise.

## Operating it

### Resource placement and worker scaling

Batch jobs are persisted as `running` while their coordinator owns execution. They must not
increase the worker autoscaling metric; the worker count excludes `runs.summary.pipeline =
batch` as well as watcher-owned dispatches. The September 29 fix is implemented locally;
the production worker cap is temporarily one until that metric correction is deployed.

`maintenance.laneMaxNodeCpu` / `CRYO_MAINTENANCE_LANE_MAX_NODE_CPU` optionally sets an inclusive
node vCPU ceiling for new lanes. The default is `0` (disabled), preserving installations
without Karpenter. A value such as `16` requires the Karpenter `instance-cpu` label and adds
an ANDed required node-affinity constraint; it preserves existing selector constraints,
capacity-type preferences, pod requests/limits, and disruption protection. The ceiling must
leave CPU headroom beyond the lane's own request. Unsuitable node pools can leave pods pending.

Canary this setting before enabling it in production. Avoiding very large protected nodes
may improve consolidation, but moving away from spare capacity on a necessary Trino node can
also add cost. Compare total node-hours, backlog, and completed work. Current production
leaves the ceiling disabled. See [the September 29 investigation](CRYO_UNUSED_COST_DEBUG_2026-09-29.md).

```bash
# What would tonight's run do? No DB writes, no pods, no Slack.
kubectl exec -n cryo deploy/cryo-api -- python -m cryo.maintenance.run --dry-run

# Canary: the top 5 only.
kubectl exec -n cryo deploy/cryo-api -- python -m cryo.maintenance.run --limit 5

# Ad-hoc: exact tables. Cooldown and eligibility are skipped; the planner still decides ops.
kubectl exec -n cryo deploy/cryo-api -- python -m cryo.maintenance.run --table-ids <uuid>,<uuid>

# Lane pods of a live run
kubectl get pods -n cryo -l app.kubernetes.io/component=maintenance-lane

# Pause the nightly schedule
kubectl patch cronjob -n cryo cryo-maintenance -p '{"spec":{"suspend":true}}'
```

### Holds and exclusions

A maintenance hold (Runs → Holds, or `POST /api/v1/maintenance/control/pause`) is checked
when the coordinator starts, after it resolves the Spark destination. If the global scope or that destination is
paused, the run is recorded as `skipped` with `summary.paused = true`, and no lane is
created. Because the check happens only at start, a run already in flight finishes the lanes it started. Use
the cronjob suspend above when the CronJob itself should not start.

Exclusion rules (`/api/v1/maintenance/exclusions`) remove a schema, or one table, from
selection. That includes `--table-ids` runs: an excluded table is never planned. Scans
are unaffected.

A full run outside the schedule: `kubectl create job -n cryo --from=cronjob/cryo-maintenance
cryo-maintenance-manual-$(date +%s)`.

The api pod works for manual runs because it runs as the same `cryo-native` ServiceAccount and
carries the same Spark env. Selection reads only numeric stats columns; the top-500 dry run
peaks around 220 MiB. An earlier version loaded whole `TableStats` rows, including the raw
JSONB, and OOMKilled the api pod on 2026-09-24. The Helm values live under `maintenance:`; each maps to a
`CRYO_MAINTENANCE_*` setting in `cryo/config.py`.

## Failure modes worth knowing

- **Coordinator is stopped (SIGTERM/SIGINT).** SIGTERM is turned into `SystemExit`, so the
  coordinator's `finally` runs within the 120 s grace period. It deletes the lane Jobs, pods
  and Secrets, marks unfinished tables `skipped` ("run interrupted"), releases leases and
  locks, closes the run and still sends the Slack message. A process started in the
  background from a non-interactive shell ignores SIGINT, so send SIGTERM.
- **Coordinator pod is killed (SIGKILL, node loss).** The lanes keep going until their own
  `activeDeadlineSeconds`, then Kubernetes stops them. Jobs stay `running` until their owner
  leases expire (5 min). After that the governor's stale-running reaper fails them 6 h after
  start. Table locks expire at deadline + 30 min. Lane Jobs have `ttlSecondsAfterFinished:
  3600`.
- **A lane pod dies mid-run (OOM, node loss).** The table it died on is recorded `failed`
  ("lane pod ended mid-table: OOMKilled (exit 137)") and not retried, since it would hit the
  same limit on the same pod size. The tables that lane never started go to a replacement pod
  of the same size (the Slack "Spark pods" line marks it `(replacement)`), up to
  `CRYO_MAINTENANCE_MAX_LANE_RESPAWNS` (default 8) per run and only until 20 min before the
  deadline. A lane that dies before starting any table isn't replaced: that's a config or
  image problem, and a replacement would die the same way. The exception is a lane the node
  took away (evicted pod, `DisruptionTarget` condition, or a pod deleted by a drain or spot
  reclaim, which leaves the Job `Failed` with no pod): that says nothing about the next pod, so
  it is replaced even before its first table. Until 2026-09-25 a deleted lane pod wasn't
  noticed at all and its tables waited for the run's hard stop. Before 2026-09-24 a dead lane
  stranded all of its remaining tables; the first full run lost two of four lanes (~250
  tables) this way.
- **Spot lanes.** An interruption costs the table in flight (recorded `failed`; its finished
  partial-progress commits stay, and failed batch jobs start no back-off, so it runs again the
  next night) plus a replacement pod for the rest. Set `maintenance.laneCapacityTypes:
  "on-demand"` to go back to on-demand-only lanes.
- **Lane JVM memory.** The heap is 60% of the pod limit. The rest covers off-heap memory and
  the Python runner; at 70% the kernel OOM-killed a 16 GiB lane. `MALLOC_ARENA_MAX=4` caps
  glibc's per-thread native arenas (Hadoop's YARN default). The JVM runs with
  `-XX:GCLockerRetryAllocationCount=100`: on Java 21 G1, a large Parquet vectored-read buffer
  that meets a heap pinned by a JNI codec fails after 2 retries with `OutOfMemoryError: Java
  heap space` and the log line "Retried waiting for GCLocker too often", even with the heap
  mostly free.
- **Kubelet log rotation on a long lane.** Results are deduplicated by (table, statement
  seq), not by line position, so a rotation can't misattribute results. Lines rotated away
  before a poll are lost; the affected table then finalizes from whatever did arrive.
- **Multi-catalog fleets.** A lane registers one Iceberg catalog, and the run uses the Spark
  destination's `catalog`. Tables rendered against a different catalog are reported as
  planning failures, not run.
- **Spark image.** The lane runs `/opt/cryo/cryo_spark_runner.py` from `spark.k8s.image`,
  which is pinned by digest separately from the app image. A runner change needs a Spark image
  rebuild and a new pin. The SQL and the `spark-submit` flags come from the app image, so
  changes like [`SPARK_ENGINE_OPTIMIZATION_2026-09-25.md`](./SPARK_ENGINE_OPTIMIZATION_2026-09-25.md)
  deploy without one.

- **Tables whose every data file carries deletes are slow on small lanes.** In the first prod
  canary, a single 1-vCPU lane took about 21 s per file group on a 3.4 GB table like that
  (Spark's row-based reader applies deletes). Nightly lanes are sized up to 4 vCPU. The
  deadline bounds the rest: tables not reached are `skipped`, not lost.
- **Partition spec comes from `stats.raw.partition_fields`.** The job store's stats carry
  `partition_col` / `partition_col_type` at the top level but the real spec only under `raw`.
  Until 2026-09-24 `partition_fields_from_stats` ignored `raw` and guessed the transform from
  the column name, which got 2,515 of 6,068 partitioned prod tables wrong (mostly `month` /
  `day` read as `identity`). A month partition `671` then rendered as
  `joining_date = DATE '1971-11-03'`, which rewrites nothing. The spec now wins, and a
  partition row written under an older spec (no value for the current field) is skipped
  with a note instead of binding its display string (`"<nil>"` from the Go scanner).
  `convert_deletes` skew predicates also drop what the engine can't render (bucket filters),
  as optimize already did.

## Canary log

- 2026-09-24: `--table-ids` on two small `mview` tables. Both succeeded: 10/10 statements OK
  in canonical order, 85 files compacted into 15, 197 orphan files removed, 39 expired files
  deleted, the Go rescan ran, one Slack message was sent and no lane objects were left behind.
  Total runtime was about 4 min, most of it spent on the shared-S3 snapshot refresh (~1.5 min)
  and pod start.
- 2026-09-24: first full manual run (`ea135ba6`, 499 tables, 4 lanes × 4 vCPU / 16 GiB).
  Lanes used 3.8–4 CPU and 7–12.7 GiB; about one table per minute. One planning failure,
  a temp table, from the `"<nil>"` partition bug above. About 30 min in,
  lane 0 was OOM-killed on a 27 GB table (578M
  rows) and lane 1 hit the GCLocker heap OOM on a 30 GB table.
  Both lanes' remaining tables were stranded, which led to the replacement-lane and JVM
  memory changes above. Final tally after 2h 18m: 117 succeeded, 3 partial, 3 failed, 376
  skipped (mostly the stranded lanes); 11,183 files rewritten into 791 (27,723 → 18,374
  files), 903 orphans removed, 6,816 expired files deleted, 3,112 → 3,092 GB. The Go rescan
  covered all 120 touched tables and one Slack message went out. Its log also printed the
  full Slack webhook URL: `cryo.maintenance.run` used `logging.basicConfig` without the
  `httpx` level cap in `configure_logging`. Every `basicConfig` entrypoint now calls
  `quiet_third_party_loggers()`, and `tests/test_logging_secrets.py` fails if one doesn't.

## Still to do

- Route ad-hoc runs from the UI and API through this pipeline (they still create preset-less
  jobs on the old queue).
- Remove the old path: scheduler preset dispatch, governor maintenance admission, the worker
  maintenance loop, the maintenance job queue, the presets API/UI and the repin daemon. Keep
  the governor's scan admission and Spark driver-pod cleanup, and the scheduler's reports and
  benefit scores.
