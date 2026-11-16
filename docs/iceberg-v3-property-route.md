# Property changes on Iceberg v3 tables (#557, #560)

PyIceberg can't commit to a format-version 3 table yet
([apache/iceberg-python#1551](https://github.com/apache/iceberg-python/issues/1551)); even a
metadata-only `set_properties` raises `Writing V3 is not yet supported`. Cryo therefore routes
v3 property writes through an engine destination.

| Table version | Route | Statement |
|---|---|---|
| v1 / v2 | PyIceberg transaction (unchanged) | `set_properties` / `remove_properties` |
| v3, Spark destination | engine job | `ALTER TABLE … SET TBLPROPERTIES (…)`, `UNSET TBLPROPERTIES IF EXISTS (…)` |
| v3, Trino only | engine job, supported keys only | `ALTER TABLE … SET PROPERTIES max_commit_retry = …` |
| v3, no capable destination | none | API 409; UI hides Apply; Path to A projects nothing |

Destination order: effective preset destination, its SQL fallback, any enabled Spark, then
Trino bound to the source. Format version comes from the live catalog read on the Config tab
and from `TableStats.raw.format_version` elsewhere; the table header and Overview show it.

**It runs as a job (#560).** A v1/v2 apply returns 200 once PyIceberg commits. A v3 apply
returns **202** with `status: "queued"`, a `run_id` and a `job_id`. The change is a
one-table run with one `set_table_properties` job on the chosen destination. The governor
admits it like other engine work: engine capacity, the per-table lock, pause, ABAC and PII
all apply. It skips the maintenance scheduling gates (cooldown, off-peak window, deadline,
benefit and ROI), because it is a person's change and reclaims nothing. The UI shows
"Applying…", follows the job and reports the result. While one change for a table is
queued or running, a second apply returns 409.

`set_table_properties` is its own operation type. It is in no mode's planned operations and
not in `CORRECT_ORDER`. Spark runs it on the small fixed profile and does not retry it when
Spark or Iceberg rejects the change as invalid. A finished property job does not start the
table's nightly cooldown.

Audit: the request writes a `QUEUED` row (`apply_table_properties` or `table_config_change`)
with `engine=<engine>`, the job id, and `result_metrics = {route, engine, destination,
run_id, job_id}`. The worker then writes one `set_table_properties` row per statement with
the outcome. Both rows show in the table's activity. The run is under Runs like any other.

**Switching back.** After pinning a PyIceberg release that writes v3, set
`CRYO_PYICEBERG_V3_WRITES=true`; v3 tables then use PyIceberg like v1/v2.

**Known limits.** A Spark-on-K8s apply still takes a driver pod, typically a minute or two,
but the request no longer waits for it. The property autopilot skips v3 tables.
