# Spark engine: efficiency and reliability pass (2026-09-25)

Scope: the Spark path that runs Iceberg maintenance, which since 2026-09-24 is the nightly batch
pipeline (`cryo/maintenance/`, `cryo_spark_runner.py --manifest`, local-mode Spark lanes; see
[`BATCH_MAINTENANCE.md`](./BATCH_MAINTENANCE.md)) plus the older per-statement Spark-on-K8s
path (`cryo/engines/spark/k8s.py`) that ad-hoc runs still use.

Every change below is grounded in the first full nightly run's audit rows, Iceberg 1.11.0 source,
or published practice. Nothing here needs a Spark image rebuild: the SQL is rendered by the app
image and the new Spark settings travel on the `spark-submit` command line the coordinator builds.

## 1. What the 2026-09-24 nightly run spent its time on

Run `25e8a39c` (19:00 → 01:41 UTC, 4 lanes: 3 × 4 vCPU/16 GiB + 1 × 2 vCPU/8 GiB): 494 tables
selected, 461 succeeded, 32 skipped because the deadline arrived first, 1 killed mid-table by the
deadline, 0 statement failures. 476.6 GB rewritten at 60 GB per rewrite-hour.

| Action | Statements | Lane-hours | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| optimize | 196 | 6.44 | 16.5 s | 340 s | 3,414 s |
| **compute_statistics** | 192 | **5.47** | 7.2 s | 566 s | 3,841 s |
| rewrite_position_deletes | 17 | 2.76 | 142 s | 2,110 s | 2,445 s |
| convert_deletes | 59 | 1.50 | 2.9 s | 487 s | 1,810 s |
| remove_orphan_files | 334 | 0.61 | 1.2 s | 14.6 s | 438 s |
| expire_snapshots | 461 | 0.55 | 1.9 s | 17 s | 146 s |
| optimize_manifests | 461 | 0.07 | 0.5 s | 1.2 s | 3.4 s |

Findings, each confirmed against the audit rows and table stats:

1. **31% of statement time was NDV statistics nobody asked for.** The batch params set
   `compute_statistics: True, stats_compute_ndv: False`, but neither the planner nor the Spark
   renderer read `stats_compute_ndv`, so every optimized table got
   `CALL system.compute_table_stats(table)`. In Iceberg 1.11 that is
   `loadTable(snapshot).select(thetaSketch(col) for every column).first()`
   (`NDVSketchUtil.computeNDVSketches`): a full scan of the whole table. It took 64 min on
   one 267 GB table and 28 min on
   a 241 GB table, whose optimize had rewritten only 21 GB. A
   bin-pack rewrite doesn't change the data, and Trino, which runs this fleet's queries, walks
   back through parent snapshots to the latest stats file (`TableStatisticsReader`) and also
   collects NDV on its own writes. Spark's reader only uses a stats file for the exact snapshot
   it scans, so for Spark the recomputation bought nothing that outlived the next commit.
2. **Optimize rewrote medium files the planner never counted.** The planner picks tables and
   partitions by files under the scanner's small-file threshold (89.6 MB); Spark's
   `rewrite_data_files` treats every file under 75% of the target (192 MB at 256 MB) as a
   candidate, and the planner sends any table up to `chunk_size_gb` (50 GB) as one full-table
   rewrite. One table had **11** small files and got
   **206 files / 23 GB** rewritten in 40 minutes (206 → 115 files); several other tables show
   the same pattern (≈1.5 lane-hours). Readers split Iceberg files at the table's
   `read.split.target-size` (128 MB default), so turning 120 MB files into 220 MB files barely
   changes the splits or GETs a query issues.
3. **`convert_deletes` never removed a delete file.** All 59 statements reported
   `removed_delete_files_count = 0` while rewriting 216 GB. Rewriting a data file applies its
   deletes, but v2 delete files stay tracked as *dangling* until something removes them, so the
   scanner keeps counting them and the table keeps coming back for delete maintenance.
4. **`rewrite_position_deletes` ran with none of the planner's settings.** The planner asks for
   partial progress; the renderer emitted a bare `CALL`. A 58 MB table with 80,107 tiny delete
   files took 34 min: Iceberg sizes each group's tasks by bytes (`FILE_OPEN_COST=0`), so 99 MB of
   delete files became two tasks reading ~40k files each, all-or-nothing.
5. **Lanes can't retry a task.** `--master local[N]` runs every task once, so one S3 timeout
   fails the whole statement and, through the runner's per-table short-circuit, the rest of that
   table's plan.
6. **A lane whose pod is deleted is never noticed.** Eviction, node drain and spot reclaim delete
   the pod; with `backoffLimit: 0` the Job fails without a replacement, `LaneRunner.poll` found
   no pod and waited, and the lane's remaining tables sat until the run's hard stop.
7. **Protected snapshots break Spark expiry.** `_render_expire` emitted
   `CALL system.set_snapshot_ref(...)`, which Iceberg does not have (1.11's `SparkProcedures`
   registry), joined with `"; "`, which the runner never splits. No prod table has a protected
   snapshot today (0 rows), so this was latent.
8. Lanes are pinned to on-demand nodes (`spark.k8s.nodeSelector:
   karpenter.sh/capacity-type=on-demand`) although the `general` NodePool offers spot and the batch
   design already tolerates losing a lane.

## 2. What competitors and Iceberg itself do

Verified sources (the per-platform comparison is in the appendix):

- **Iceberg 1.11.0 source.** `partial-progress.max-commits` batches `ceil(groups / max-commits)`
  file groups per commit and still rewrites every planned group
  (`RewriteDataFilesSparkAction.doExecuteWithPartialProgress`); failed groups are logged and
  skipped, and the action only fails when failed commits exceed `max-failed-commits`.
  `remove-dangling-deletes` runs only after a rewrite that did something, is a no-op on
  unpartitioned single-spec tables, and commits only when it finds dangling files
  (`RemoveDanglingDeletesSparkAction`). Every rewrite option name is validated against
  `VALID_OPTIONS` (unknown keys fail the procedure), and `rewrite_position_delete_files`
  accepts exactly `max-concurrent-file-group-rewrites`, `partial-progress.enabled`,
  `partial-progress.max-commits`, `rewrite-job-order` plus the size-planner options.
- **Iceberg docs** ([Spark procedures](https://iceberg.apache.org/docs/latest/spark-procedures/)):
  `stream_results` on `expire_snapshots` is "recommended to set to true to prevent Spark driver
  OOM"; `rewrite-job-order=files-desc` rewrites the groups with the most files first;
  `rewrite_position_delete_files` filters dangling deletes while it rewrites.
- **Apache Amoro** ([self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)):
  optimizers are *resident* processes fed by the AMS scheduler (no JVM per task, which Cryo's
  lanes now match), and optimizing is generational: frequent *minor* passes compact only
  fragment files (< target/8, 16 MB at 128 MB), *major* passes touch the larger segment files
  rarely. Rewriting medium files every night is exactly what that design avoids.
- **Amazon S3 Tables** ([maintenance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-maintenance.html)):
  managed compaction (512 MB default target, 64–512 MB configurable), snapshot expiry (120 h
  default) and unreferenced-file removal run as separate managed jobs; no NDV/statistics step.
- **Where the small-file bar sits elsewhere.** Iceberg's default is 75% of target. Trino's
  `OPTIMIZE` defaults `file_size_threshold` to 100 MB, Dremio's `OPTIMIZE` rewrites files under
  0.24 × target, Amoro's frequent pass takes fragments under target/8, and AWS Glue's optimizer
  only starts when a partition has more than 100 files under 75% of target
  ([Trino Iceberg connector](https://trino.io/docs/current/connector/iceberg.html),
  [Dremio OPTIMIZE](https://docs.dremio.com/current/reference/sql/commands/apache-iceberg-tables/optimize-table/),
  [Glue compaction](https://docs.aws.amazon.com/glue/latest/dg/compaction-management.html)).
The batch path's 89.6 MB is 35% of a 256 MB target: 10% under Trino's 100 MB
default and 46% above Dremio's 0.24 × target (61 MB). It sits between those two bars,
not on either of them.
- **AWS on Spark + Spot** ([EMR on EKS fault-tolerant Spark on Spot](https://aws.amazon.com/blogs/big-data/run-fault-tolerant-and-cost-optimized-spark-clusters-using-amazon-emr-on-eks-and-amazon-ec2-spot-instances/),
  [EMR containers cost guide](https://aws.github.io/aws-emr-containers-best-practices/cost-optimization/docs/cost-optimization/)):
  keep the component whose loss fails the job on on-demand and put re-assignable work on Spot
  with diversified instance types. In the batch design the coordinator (on-demand,
  `do-not-disrupt`) is that component; a lane is re-assignable, since its unstarted tables move
  to a replacement lane and its rewrites commit in partial-progress steps.

## 3. Changes

| # | Change | Where | Evidence | Revert |
|---|---|---|---|---|
| 1 | `compute_statistics` planned only when `stats_compute_ndv` is true or `bloom_filter_columns` is set; the Spark renderer skips `compute_table_stats` when `compute_ndv` is false and renders nothing if nothing is left | `cryo/planner/planner.py`, `cryo/engines/spark/sql.py` | Finding 1 | `stats_compute_ndv: true` in the params |
| 2 | Batch optimize passes `min-file-size-bytes` = scanner small-file threshold (`CRYO_SCAN_SMALL_FILE_MB`, 89.6 MB), bin-pack only, dropped if ≥ target | `coordinator.run`, `_render_optimize` | Finding 2 | remove `min_file_size_mb` from the params |
| 3 | `rewrite-job-order=files-desc` on optimize, convert_deletes and position-delete rewrites | `sql.py` | biggest reductions commit first when a rewrite is cut short | `rewrite_job_order` param |
| 4 | `remove-dangling-deletes=true` on convert_deletes, opted into by the batch `DEFAULT_PARAMS` (the option exists from Iceberg 1.7 and unknown options fail the procedure, so the shared renderer only adds it when asked; lanes run Cryo's image with Iceberg 1.11) | `_render_convert_deletes`, planner `convert_params`, `manifest.DEFAULT_PARAMS` | Finding 3 | `remove_dangling_deletes: false` |
| 5 | `rewrite_position_delete_files` honors partial progress (10 commits) and sets group concurrency (5) and job order | `_render_rewrite_position_deletes` | Finding 4 | `partial_progress_enabled: false` |
| 6 | `stream_results => true` on expire_snapshots | `_render_expire` | Iceberg docs | `stream_results: false` |
| 7 | Protected snapshots use `ALTER TABLE … CREATE OR REPLACE TAG \`cryo-protected-latest\` AS OF VERSION n` as its own `;\n` statement | `_render_expire` | Finding 7 | — |
| 8 | Lanes: `local[N,3]` (three attempts per task), status store capped (50 jobs, 100 stages, 5,000 tasks, 20 SQL executions), `s3.delete.batch-size=1000` and `s3.retry.num-retries=32` on S3 warehouses (32 is what Iceberg's AWS docs recommend for throttled workloads; S3FileIO only retries throttling, 5xx and I/O errors) | `k8s_lanes.spark_submit_command`, `k8s.py::catalog_io_confs` | Finding 5; long-lived driver heap | — |
| 9 | Per-statement Spark-on-K8s submits get the same S3FileIO confs | `k8s.py::catalog_io_confs` | fewer DeleteObjects calls, fewer throttling failures | — |
| 10 | Lanes may run on spot with on-demand fallback: the inherited `karpenter.sh/capacity-type` pin becomes a required `In [spot, on-demand]` node affinity preferring spot | `CRYO_MAINTENANCE_LANE_CAPACITY_TYPES` / `maintenance.laneCapacityTypes` | Finding 8 | set it to `on-demand` (or `""` to keep the pin) |
| 11 | A lane whose pod vanished is finished from its Job status (`Failed` condition → infra loss; `DeadlineExceeded` → run deadline); evicted pods and pods with a `DisruptionTarget` condition count as infra loss; an infra-lost lane is replaced even before its first table | `LaneRunner.poll/_job_outcome`, `coordinator._respawn` | Finding 6 | — |
| 12 | Batch optimize sets `max-file-size-bytes` to 10 × target, so files up to 2.5 GB are no longer rewritten alone just to be split (Iceberg's default bound is 1.8 × target). LinkedIn OpenHouse uses the same 10×. Read splits stay target-sized, so outputs still land near 256 MB | `manifest.DEFAULT_PARAMS` (`max_file_size_ratio`), `_render_optimize` | 2 optimize statements grew file counts on 2026-09-24 (52 GB rewritten) | drop `max_file_size_ratio` |

Deliberately **not** changed:

- `partial-progress.max-commits` stays 50 for optimize and convert_deletes. The old comments
  claimed Iceberg stops after 10 commits; the source says otherwise, and the comments now say
  what the value actually trades (commit granularity and conflict blast radius vs snapshots).
  On one table, 25 commits spread over a 32-minute rewrite, so commit
  overhead is not the bottleneck.
- `remove_orphan_files` keeps Hadoop directory listing (one LIST per directory, spread over
  tasks) instead of `prefix_listing` (flat `ListObjectsV2` on the driver; 1.11's
  `FileSystemWalker.listDirRecursivelyWithFileIO` appends the trailing `/`, so sibling prefixes
  like `…/tbl_v2` are not picked up). The whole operation took 0.61 lane-hours across 334
  tables, so switching the listing path of a destructive operation isn't worth it without a
  canary; it's on the follow-up list.
- The S3 Analytics Accelerator (`s3.analytics-accelerator.enabled`) ships in the 1.11 AWS bundle
  and would cut GETs per small file, but it is off by default upstream and switches reads to a
  CRT async client. Worth a canary, not a blind default.
- Lane memory. Lanes used 7–12.7 GiB of 16 GiB, but the first full run OOM-killed a lane at a
  70% heap fraction; memory headroom is what keeps the big tables alive.
- The per-table short-circuit in the runner (a failed statement skips the rest of that table).

## 4. Expected effect on the next nightly run

- Statement time ≈ 17.4 → ≈ 10.5 lane-hours if changes 1, 2 and 12 are live: −5.5 h NDV
  scans, ≈ −1.5 h medium-file rewrites, plus part of the 0.5 h that split-only rewrites took.
  That is lane-hours, not wall clock. Spread across 4 lanes it shortens the longest lane by
  about 1.4 h only if the saved work was spread evenly; it reduces the chance of deadline
  skips. It does not by itself put the run well before the 6 h cap.
- `removed_delete_files_count` should rise only when `convert_deletes` rewrote something,
  the table is not an unpartitioned single-spec table, and Iceberg actually finds dangling
  delete files. A zero sum on that query is not by itself a failed deploy.
- With spot capacity the lane nodes cost the spot price (AWS advertises up to 90% off on-demand;
  the Karpenter default allocation is price-capacity-optimized). An interrupted lane costs one
  table (recorded `failed`, retried the next night since failed batch jobs start no back-off) and
  a replacement pod for the rest.

## 5. How to verify after deploy

```sql
-- per-action lane-hours for a run (compute_statistics should be gone)
select a.action, count(*), round(sum(a.duration_ms)/3.6e6, 2) hours
from cryo.audit_log a join cryo.jobs j on j.id = a.job_id
where j.run_id = '<run>' and a.actor = 'cryo-batch' group by 1 order by 3 desc;

-- delete files actually removed by convert_deletes
select sum((regexp_match(detail, 'removed_delete_files_count'': (\d+)'))[1]::int)
from cryo.audit_log a join cryo.jobs j on j.id = a.job_id
where j.run_id = '<run>' and a.action = 'convert_deletes' and a.status = 'OK';
```

```bash
# lane pods and the capacity type of the nodes they landed on
kubectl get pods -n cryo -l app.kubernetes.io/component=maintenance-lane -o wide
kubectl get nodes -L karpenter.sh/capacity-type,node.kubernetes.io/instance-type
```

Watch for any statement failing with `Cannot use options` (an option Iceberg rejected) or
`ParseException` (tag DDL); neither is expected, since every option was checked against the
1.11.0 `VALID_OPTIONS` sets.

## 6. Follow-ups, ranked by expected payoff

1. **Stop compacting tables their writers rebuild every day.** Several tables compacted on
   2026-09-24 were back to their old file counts within hours: `store.gold_users` 100 → 9 → 100,
   one table 500 → 10 → 500, another
   894 → 103 → 892 (a Trino write at 03:32 UTC). Nightly compaction only helps those tables
   during the night. Detecting it needs the scanner to record the last data commit's
   added/deleted/total file counts (the Go scanner reads snapshot summaries already); selection
   would then skip compaction but keep expire/orphan cleanup, which is where rebuilt tables
   actually waste storage.
2. **Align AutoComp's estimates with the rewrite scope.** Selection still scores file-count
   reduction and rewrite bytes from files below the 256 MB target, while Spark now rewrites only
   files below 89.6 MB. That overstates cost (budget fits fewer tables than could run) and
   benefit for medium-file tables. The scanner would need to store small-file bytes.
3. **Work stealing between lanes.** Lanes get a fixed table list; a lane that finishes early
   can't take work from a busy one. The manifest Secret is mounted without `subPath`, so a
   coordinator-written skip list would reach a running lane.
4. **Right-size Spark for small-file latency.** Per-task S3 latency, not CPU, bounds rewrites of
   thousands of tiny files. Candidates: an S3 Analytics Accelerator canary, and a smaller
   `max-file-group-size-bytes` for position-delete rewrites so tiny-file partitions split into
   parallel groups.
5. **Run cleanup after a failed rewrite.** expire_snapshots and remove_orphan_files don't depend
   on the rewrite having succeeded; today a failed optimize skips them.
6. **Canary `prefix_listing => true` for remove_orphan_files.** Fewer LIST requests on tables
   with many partition directories and no Hadoop S3A dependency; small time payoff today.
7. **Carry NDV statistics across compaction instead of recomputing them.** Trino's `OPTIMIZE`
   rewrites the existing sketches onto its new snapshot; a bin-pack that applied no deletes
   leaves NDV unchanged, so re-registering the parent's Puffin sketches is enough. Expiring a
   snapshot drops its stats file, so a table no engine writes stats for loses NDV once the
   analyzed snapshot ages out; recompute only after real data change, and only on join/filter
   columns (Trino's own guidance, Databricks' predictive optimization rule).
8. **Back off tables that fail every night.** A table that OOM-kills its lane pays the lost lane
   time again each night. AWS Glue suspends an optimizer after 4 consecutive failures; OpenHouse
   retries 1 h after a failure versus 16 h after a success. `tables.maintenance_retry_after`
   exists, but the batch path doesn't set it.
9. **Guard orphan deletion further.** Soft delete before hard delete (S3 Tables: noncurrent after
   3 days, gone 10 days later; OpenHouse moves files to `.backup`), and abort when the orphan
   ratio looks abnormal (IOMETE).

## 7. Implementation plan

The 12 changes in §3 exist only in this working tree as uncommitted diffs across
`cryo/engines/spark/k8s.py`, `cryo/engines/spark/sql.py`, `cryo/maintenance/coordinator.py`,
`cryo/maintenance/k8s_lanes.py`, `cryo/maintenance/manifest.py`, `cryo/planner/guardrails.py`,
`cryo/planner/planner.py`, `deploy/helm/cryo/templates/maintenance-cronjob.yaml`, and their four
test files (470 lines of new/changed tests, 112 passing). None of it is in the image the
2026-09-25 19:00 UTC nightly run will use unless committed and deployed before then — this is
Helm-templated command-line/SQL-rendering logic, not new binary contents, so **no Spark image
rebuild is required**, only an app-image deploy.

Three gaps identified in review (not in §3/§4 as first written) block a same-day ship without
more evidence, and one is a real behavior-changing interaction with already-deployed work:

- §4's "well before the 6 h cap" claim treats aggregate lane-hours saved as if it were
  wall-clock time saved. With 4 lanes, removing 5.47 lane-hours of NDV work only shortens the
  *longest* lane by ~1.4 h unless that time was concentrated on whichever lane already ran
  longest — which the doc doesn't show. The claim needs either that per-lane breakdown or
  needs to be walked back to "reduces the deadline-skip risk," not a specific margin.
- `removed_delete_files_count > 0` (§4) is not unconditional — §3 change 4 itself documents
  the no-op case (unpartitioned single-spec tables, commits only when something is dangling).
  §4 should state the same condition, not the unconditional version.
- "Sits with Trino and Dremio" (§2) undersells the appendix's own numbers: 89.6 MB is 46% above
  Dremio's 61.44 MB bar (0.24 × 256 MB) and 10% below Trino's 100 MB — between both, not
  matching either. Cosmetic; fix in passing.
- **`ac0545a`** (already committed on this branch, landed 2026-09-25 13:45 IST, *after* the
  19:00 UTC run this doc's memory analysis is based on) moved the 16 GiB lane tier's threshold
  from 50 GB to 200 GB (`cryo/maintenance/lanes.py::_SIZE_TIERS`). Any table ≥50 GB and
  <200 GB that used to land on a 2 vCPU/16 GiB lane now gets 2 vCPU/8 GiB. §3's "leave lane
  memory alone, lanes used 7–12.7 GiB of 16 GiB" conclusion doesn't name the table that hit
  12.7 GiB or its size, so it's unknown whether that observation survives the new tiering.
  This is the one gap that can turn a deploy into an outage, not just an inaccurate doc line,
  so it gates the rollout below rather than just getting a doc correction.

### Step 0 result — 2026-09-25, gate failed

Lane size uses `rewrite_gb` (small-file share of table size), not total table size.
Pre-run `table_stats_history` joined to `cryo.jobs.engine_ref` for run `25e8a39c`:

| Lane | Was | Largest rewrite | Table | Tonight under `ac0545a` |
|---|---|---:|---|---|
| 0 | 4 vCPU / 16 GiB | 109.4 GB | `user_experience.gold_test_student_mappings` (140 GB) | 2 vCPU / 8 GiB |
| 1 | 4 vCPU / 16 GiB | 69.9 GB | a 160 GB table | 2 vCPU / 8 GiB |
| 2 | 2 vCPU / 8 GiB | 30.5 GB | already under the old 50 GB bar | unchanged |
| 3 | 4 vCPU / 16 GiB | 62.7 GB | a 234 GB table | 2 vCPU / 8 GiB |

§1's 241 GB / 21 GB rewrite for that table is the optimize statement's audit (what Spark rewrote). The 234 GB / 62.7 GB here is the pre-run scanner estimate that sized the lane (`total_size_gb` × small-file ratio), not the same number.

No per-lane peak RSS is stored in audit rows, and the lane pods are gone. The only
memory figure in this doc is 7–12.7 GiB on the 16 GiB lanes. 7 GiB is already over
70% of 8 GiB. All three lanes that ran at 16 GiB have a largest rewrite in
`[50, 200)` GB, so tonight they would be 8 GiB. Gate failed. Changes 1–5 and 8–12
stay uncommitted. Change 6 and 7 (commit D) ship: 0 protected snapshots, and
`stream_results` does not increase the bytes a lane reads.

### Step 0 — Resolve the tiering gap before anything else ships

1. Query the 2026-09-24 run's audit rows (or Spark event logs if retained) for peak driver
   memory per lane, joined to the largest table each lane processed that night.
2. For any lane whose largest table was in `[50, 200)` GB: that lane would have gotten 8 GiB
   under the tiering in effect *tonight*, not the 16 GiB it actually had on 2026-09-24. If its
   observed peak was above ~70% of 8 GiB (≈5.6 GiB), treat the memory-headroom conclusion in
   §3 as not yet verified for tonight's shape, and either (a) widen the 16 GiB tier back toward
   its old 50 GB boundary for this batch destination specifically (leaving `ac0545a`'s change
   for the destinations it was meant for), or (b) hold changes 1–2 (the NDV/min-file-size fixes,
   which reduce data actually read into memory) until after this check, since they may lower
   peak usage enough on their own to make the question moot.
3. Record the answer in this doc (or a dated follow-up note) before Step 2 below — this is a
   go/no-go gate, not a parallel task.

### Step 1 — Land the doc corrections (no code risk, do first)

Fix the three claims above in §2 and §4 directly, in the same commit that lands the code below.
A reviewer should not have to cross-reference a chat transcript to know which of this doc's
own numbers to trust.

### Step 2 — Commit the 12 changes as reviewable units, not one squash

The changes already group cleanly by risk and by what breaks if reverted:

| Commit | Changes | Why grouped | Revert blast radius if wrong |
|---|---|---|---|
| A | 1 (NDV gating) | Pure gate; nothing downstream reads `compute_statistics` output today. Lowest risk, biggest win (5.47 lane-hours). | Set `stats_compute_ndv: true` in batch params — no code revert needed. |
| B | 2, 12 (min/max-file-size-bytes) | Both touch `_render_optimize`'s options list; testing them together covers the interaction (a file just above min and just below max). | Drop `min_file_size_mb`/`max_file_size_ratio` from params — no code revert needed. |
| C | 3, 4, 5 (rewrite-job-order, remove-dangling-deletes, position-delete partial progress) | All three are "pass more of Iceberg's own tuning surface through," same shape of change, same risk profile (Iceberg validates the option names itself). | Per-param revert, listed in §3's own Revert column. |
| D | 6, 7 (stream_results, protected-snapshot tag) | 7 is the one correctness fix in this batch (Spark expiry with a protected snapshot fails outright today) — ship it even if everything else in this plan is deferred. 0 prod tables have a protected snapshot today, so D is currently a no-op in prod either way; low urgency but zero reason to hold it back once C ships. | 7 has no param revert (the old code path was simply broken); 6 does. |
| E | 8, 9 (local[N,3], status-store caps, S3FileIO confs) | Reliability, not behavior — no param controls these, they're submit-command constants. Needs a real lane run to confirm `local[N,3]`'s retry doesn't mask a failure class that should have short-circuited the table (e.g. a bad predicate vs. a transient S3 timeout look identical to a bare retry count). | Code revert only; no param escape hatch — call this out explicitly to whoever's on call. |
| F | 10, 11 (spot/on-demand affinity, infra-loss detection) | Changes what capacity type lanes land on and how a lost lane is detected — the two are coupled (spot makes lane loss more likely, so the detection needs to exist first). Ship F only after E has run clean at least once, since E's retry behavior changes what "a statement failed" means, which F's infra-loss path partially depends on distinguishing from. | `CRYO_MAINTENANCE_LANE_CAPACITY_TYPES=on-demand` reverts the affinity half without a code revert; the detection half (11) has no flag — revert the commit if it's flagging false positives. |

Order: A → B → C → D, each independently deployable and individually verifiable via the SQL/
`kubectl` checks in §5. E and F should land together but *after* A–D have run clean for at
least one full nightly cycle, since E changes failure semantics that F's detection logic reads.

### Step 3 — Deploy sequencing for tonight's 19:00 UTC run

Given Step 0 is a real go/no-go gate:

- **If Step 0 clears** (no lane's peak usage is at risk under the new tiering, or A/B alone
  demonstrably reduce peak usage enough): land commits A–D today, deploy the app image with
  enough margin before 19:00 UTC to watch the deploy succeed and confirm pods are on the new
  digest (same verification `OSS_0_BASELINE`-style docs in this repo already use: check the
  rendered image digest on running pods, don't trust `helm upgrade`'s exit code alone).
- **If Step 0 does not clear in time**: ship D alone (the correctness fix, zero prod tables
  affected today, effectively risk-free) and defer A–C to the following night once the memory
  question is answered. Do not ship E/F without a clean A–D cycle regardless of the Step 0
  outcome — they're independent of the memory question but depend on A–D's failure-semantics
  changes.
- In either case, run the §5 verification queries against the actual run once it completes, and
  update §4's "expected effect" with what actually happened rather than leaving the projection
  unreconciled — the gap between predicted and observed lane-hours will itself confirm or
  correct the wall-clock-vs-lane-hours question raised above.

### Step 4 — Follow-ups (§6) stay follow-ups

Nothing in §6 blocks this deploy; they're independently scoped, ranked work for after tonight's
run confirms A–D (and later E–F) behave as expected. Follow-up 1 (skip compaction on
writer-rebuilt tables) is the highest-value next step once this plan's rollout is stable, since
it's the only item in §6 that changes *which* tables get selected rather than how an already-
selected table is processed — worth scoping as its own doc once there's a clean baseline to
measure it against.

## Appendix: competitor survey

| Platform | How it runs maintenance | What Cryo takes from it |
|---|---|---|
| Apache Amoro ([self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/)) | Resident optimizer processes pull tasks from the AMS scheduler; generational passes: frequent minor (fragments < target/8), rarer major/full for segments; per-table resource quotas. | Long-lived sessions (lanes already); nightly rewrites limited to small files (change 2). |
| LinkedIn OpenHouse ([repo](https://github.com/linkedin/openhouse), [AutoComp paper](https://arxiv.org/abs/2504.04186)) | Spark jobs per table from a scheduler (40 concurrent, tables younger than 72 h skipped); bin-pack with partial progress, only files under 0.75 × a 502 MB target, **max file size 10 × target ("don't split large files")**, ≤ 30 commits, a fixed 0.5 GB-h startup cost in the score; orphan deletion batched into one Spark app per ≤ 50 tables / ≤ 1M files because "file count is the OFD cost driver", with `stream-results` and files moved to `.backup`. AutoComp ranks gain ÷ cost under a daily budget. | Many tables per Spark session (lanes), AutoComp selection (`selection.py`), the 10 × max-file-size (change 12). |
| Netflix AutoOptimize ([blog](https://netflixtechblog.com/optimizing-data-warehouse-storage-7b94a48fdcbe)) | Commit-triggered, "essential work only", replace the minimum number of files; a partition is skipped when its file-size entropy (MSE against target) is under a threshold; workers are a pool of long-running Spark jobs. Reported: 70% less compute, 72% fewer file replacements, 80% fewer files. | Partition-scoped, small-file-only rewrites; recency/change awareness is follow-up 1. |
| Amazon S3 Tables ([maintenance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-maintenance.html)) | Managed compaction (512 MB default target), snapshot expiry (120 h) and unreferenced-file removal as separate jobs; no statistics step. | Statistics are not part of routine maintenance (change 1). |
| Starburst Galaxy ([data maintenance](https://docs.starburst.io/starburst-galaxy/data-engineering/optimization-performance-and-quality/observability/data-maintenance.html)) | Compaction, statistics, snapshot expiry and orphan removal are separately enabled tasks with their own hourly/daily/weekly/cron schedule per catalog, schema or table. | Statistics on their own cadence, opt-in (`stats_compute_ndv`). |
| AWS EMR on EKS ([Spot blog](https://aws.amazon.com/blogs/big-data/run-fault-tolerant-and-cost-optimized-spark-clusters-using-amazon-emr-on-eks-and-amazon-ec2-spot-instances/), [cost guide](https://aws.github.io/aws-emr-containers-best-practices/cost-optimization/docs/cost-optimization/)) | Job-critical component on on-demand, re-assignable work on Spot, diversified instance types, Karpenter price-capacity-optimized allocation. | Coordinator on-demand, lanes spot-preferred with fallback (change 10) and loss detection (change 11). |
| Apache Iceberg ([procedures](https://iceberg.apache.org/docs/latest/spark-procedures/), 1.11.0 source) | Partial-progress commits, `rewrite-job-order`, dangling-delete removal, streamed expiry results; strict option validation. | Changes 3–7, all checked against the 1.11.0 `VALID_OPTIONS`. |
| AWS Glue optimizers ([compaction](https://docs.aws.amazon.com/glue/latest/dg/compaction-management.html), [notes](https://docs.aws.amazon.com/glue/latest/dg/optimizer-notes.html)) | Compaction only when a partition has > 100 files under 75% of a 512 MB target, `deleteFileThreshold` 1; auto-suspends after 4 consecutive failures; retention/orphan runs cap at 1M files per run. | Failure back-off (follow-up 8). |
| Databricks predictive optimization ([docs](https://docs.databricks.com/aws/en/optimizations/predictive-optimization)) | OPTIMIZE / VACUUM / ANALYZE on managed Delta and Iceberg tables; statistics collected on write, ANALYZE only when UPDATE/DELETE degrade them. | Statistics only on real data change (follow-up 7). |
| Dremio, Trino, Tabular, IOMETE | Dremio `OPTIMIZE` rewrites files under 0.24 × target; Trino `OPTIMIZE` under 100 MB, 7-day minimum retention, stats on write; Tabular rewrote one file group at a time; IOMETE runs compaction/manifests on Spark but expiry/orphan cleanup outside Spark, orphan cleanup weekly with an abnormal-ratio abort. | Small-file bar (change 2), orphan guards (follow-up 9). |
