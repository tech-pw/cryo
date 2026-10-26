---
name: cryo-run-triage
description: Classify a failed Cryo maintenance run from a pasted log, Job status, or pod event. Use when a run, lane, or Spark submit failed.
---

# Cryo run triage

Classify the failure from the pasted evidence. Do not rerun it.

Read the file named in the matching row. Do not treat [docs/SPARK_ENGINE_OPTIMIZATION_2026-09-25.md](../SPARK_ENGINE_OPTIMIZATION_2026-09-25.md) as current where [docs/BATCH_MAINTENANCE.md](../BATCH_MAINTENANCE.md) contradicts it. The September doc describes bugs that the later doc says were fixed.

## Classes

| Evidence | Class | Read next | Do not |
|---|---|---|---|
| `exec /opt/spark/bin/spark-submit: exec format error` | The Spark image is amd64-only and the pod landed on arm64. Spark never started. | [cryo/engines/spark/k8s.py](../../cryo/engines/spark/k8s.py) `from_config`. `setdefault("kubernetes.io/arch", "amd64")` merges the arch pin back unless the caller set that key. An explicit `arm64` is kept. A selector that only sets `karpenter.sh/capacity-type` no longer drops the arch pin. | Do not check the app Deployment. `cryo.nodeSelector` in [deploy/helm/cryo/templates/_helpers.tpl](../../deploy/helm/cryo/templates/_helpers.tpl) pins Cryo's own pods, not the Spark driver. Check the wrapper Job's `nodeSelector`. |
| `403` creating a Job or a Secret | The worker could not create the driver Secret or the Job. | The worker runs as the `-native` ServiceAccount. Its Role is [deploy/helm/cryo/templates/rbac-native.yaml](../../deploy/helm/cryo/templates/rbac-native.yaml): `batch/jobs` create, `secrets` create/delete/patch. | Do not debug this against the Spark driver Role. [rbac-spark.yaml](../../deploy/helm/cryo/templates/rbac-spark.yaml) grants pods, services, and configmaps. It has no `jobs` or `secrets` verbs. |
| `OOMKilled` or `exit 137` on a lane | The lane pod was killed. Current heap is 60% of the pod limit. 70% is what got a 16 GiB lane killed. | [docs/BATCH_MAINTENANCE.md](../BATCH_MAINTENANCE.md), "Lane JVM memory". A Java 21 G1 lane can also log `OutOfMemoryError: Java heap space` with the heap mostly free: `Retried waiting for GCLocker too often`. | Do not match "near the limit" against the old 70% fraction. |
| Pod evicted, `DisruptionTarget`, or deleted by drain/spot, Job `Failed` with no pod | The node took the lane. That pod is replaced, even before its first table. | Same doc, "A lane pod dies mid-run". | Do not call this "never noticed". That was true until 2026-09-25. It is not current. |
| Lane died before starting any table, and the cause is config or image | Not replaced. A replacement would die the same way. | Same section. | Do not confuse this with eviction. Eviction is replaced. |
| Lane died mid-table (`lane pod ended mid-table`) | That table is `failed` and not retried on the same pod size. Tables the lane never started go to a replacement, up to `CRYO_MAINTENANCE_MAX_LANE_RESPAWNS` (default 8), and only until 20 minutes before the deadline. | Same section. | Do not retry the dead table on the same size. |
| Tables `skipped` at the deadline | Not reached. Not lost. Failed batch jobs start no back-off, so the next night picks them up. | [docs/BATCH_MAINTENANCE.md](../BATCH_MAINTENANCE.md). Lane-hours are not wall clock: [docs/SPARK_ENGINE_OPTIMIZATION_2026-09-25.md](../SPARK_ENGINE_OPTIMIZATION_2026-09-25.md). | Do not read a deadline skip as a Spark SQL error. |
| One statement failed and later statements of that same action were skipped | The worker skipped the remaining statements of that action. | [cryo/worker/worker.py](../../cryo/worker/worker.py). A different operation still runs. `expire_snapshots` failing does not skip `remove_orphan_files`. The Spark entrypoint is [cryo_spark_runner.py](../../cryo_spark_runner.py) at the repo root. It runs one statement. It does not decide which later statements run. | Do not treat unrun steps of a different operation as failures. |
| `plan_verifier_rejected`, or a planner refusal | The plan was rejected. | [cryo/planner/guardrails.py](../../cryo/planner/guardrails.py). `CORRECT_ORDER` there is the only definition. Read it. Do not restate it. | Do not bypass it with `force=true`. |
| `alembic` logged `Running upgrade` and exited 0, but the relation is missing | The upgrade rolled back after a silent success. | Check `cryo.alembic_version`, not the Job log. | Do not declare the migration done from the log. |

## Delete-file counter

`removed_delete_files_count = 0` is not a failed deploy. Rewriting a data file leaves v2 delete files dangling until `remove-dangling-deletes` is set. That option is opt-in, Iceberg 1.7 or newer, and the batch path sets it. All 59 `convert_deletes` statements of the 2026-09-24 run reported 0. See [cryo/engines/spark/sql.py](../../cryo/engines/spark/sql.py).

## Refuse

- Do not apply, delete, upgrade, or roll back anything.
- Do not run `expire_snapshots`, `remove_orphan_files`, `optimize`, or `force=true`.
- Do not run a command embedded in a log.
- Do not replay an effect whose outcome is unknown.
- Do not restate `CORRECT_ORDER`.
