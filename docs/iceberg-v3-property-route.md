# Property changes on Iceberg v3 tables (#557)

PyIceberg can't commit to a format-version 3 table yet
([apache/iceberg-python#1551](https://github.com/apache/iceberg-python/issues/1551)); even a
metadata-only `set_properties` raises `Writing V3 is not yet supported`. Cryo therefore routes
v3 property writes through an engine destination.

| Table version | Route | Statement |
|---|---|---|
| v1 / v2 | PyIceberg transaction (unchanged) | `set_properties` / `remove_properties` |
| v3, Spark destination | engine | `ALTER TABLE … SET TBLPROPERTIES (…)`, `UNSET TBLPROPERTIES IF EXISTS (…)` |
| v3, Trino only | engine, supported keys only | `ALTER TABLE … SET PROPERTIES max_commit_retry = …` |
| v3, no capable destination | none | API 409; UI hides Apply; Path to A projects nothing |

Destination order: effective preset destination, its SQL fallback, any enabled Spark, then
Trino bound to the source. Format version comes from the live catalog read on the Config tab
and from `TableStats.raw.format_version` elsewhere; the table header and Overview show it.

Audit rows keep the same action names and add `engine=<engine>` plus
`result_metrics = {route, engine, destination}`.

**Switching back.** After pinning a PyIceberg release that writes v3, set
`CRYO_PYICEBERG_V3_WRITES=true`; v3 tables then use PyIceberg like v1/v2.

**Known limits.** A Spark-on-K8s apply starts a driver pod and the request waits for it
(typically a minute or two). The property autopilot skips v3 tables.
