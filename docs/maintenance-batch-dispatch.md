# Maintenance batch dispatch

Ad-hoc and nightly paths are different. A batch review must name the path it will actually use.

## Ad-hoc (V1 submit)

`POST /maintenance/run` calls `cryo.maintenance.run_service.start_maintenance_run`. That creates one job per table and enqueues each job. A Spark job becomes its own driver (`cryo/engines/spark/k8s.py`). Five tables are five drivers. The review line is "N separate Spark jobs", not lanes.

The governor `reconcile_queued_jobs` re-enqueues a job that committed in Postgres and never reached Redis. That is the outbox for this path. No second outbox.

## Nightly lanes

`BatchMaintenance.run` calls `plan_lanes`. `max_lanes` is a hard cap on the returned plan. Occupied size tiers stay separate only when they fit. When they do not, lower tiers pack into the largest lane shape required. `lane_count()` is a budget hint, not the pod count. Preview and dispatch must both use `plan_lanes()`.

Explicit table IDs on the nightly coordinator currently skip eligibility (`require_eligible=not table_ids`). A batch must not use that path until eligibility is enforced again. Until then, batch submit stays on the per-table run service.

## What we do not claim

Fewer pods is not a saving. A saving needs a measured comparison of the same eligible tables and operations, once as separate jobs and once on the shared lane path, from equivalent starting states. Until that exists, the product says "Not estimated".
