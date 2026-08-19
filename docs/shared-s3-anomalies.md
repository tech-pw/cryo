# Shared S3 warehouse-path anomalies

Catalog names are pointers. The Iceberg table is the **S3 warehouse prefix**. Two names
(or a name nested under another table's prefix) share one physical table. `DROP TABLE`,
snapshot expiry, and orphan-file removal on either name delete the other's files.

This is the detector added after
[`docs/incidents/rca-cdp-catalog-evolving-shared-s3-drop.md`](./incidents/rca-cdp-catalog-evolving-shared-s3-drop.md).
It is **not** the ML fleet anomaly detector (`GET /api/v1/advanced/anomalies`).

## What counts as a collision

After normalizing `s3` / `s3a` / `s3n`, stripping trailing slashes, and lowercasing the
bucket:

- **Exact** — two or more catalog tables have the same warehouse path.
- **Nested** — one table's path is a path-boundary prefix of another (`…/orders` vs
  `…/orders/archive`). `…/orders` vs `…/orders_v2` is **not** a match.

Groups are computed across **all enabled sources** (Hive + Glue + REST). Maintenance
schema allowlists are ignored — aliases outside scan scope are the ones that get dropped
by mistake.

## Harvest (do not use `load_table` for Hive/Glue)

| Catalog | Location source |
|---------|-----------------|
| Hive / HMS | Direct Thrift (`thrift.transport` + `hive_metastore.ThriftHiveMetastore`) `StorageDescriptor.location`. Does not use PyIceberg. |
| Glue | `GetTables` → `StorageDescriptor.Location` |
| REST / Polaris / Nessie | `load_table().location()`; per-table error if metadata.json is missing |

If a source cannot be contacted, Cryo falls back to persisted `cryo.tables.location` for
that source and marks it `db_fallback` on the report.

Snapshots live in `cryo.shared_s3_anomaly_reports`. The UI reads the latest row so a page
load does not list production HMS.

## API

- `GET /api/v1/anomalies/shared-s3` — latest snapshot (viewer)
- `POST /api/v1/anomalies/shared-s3/refresh` — harvest now (operator); rate-limited
- `POST /api/v1/anomalies/shared-s3/notify` — Slack **only if** overlapping groups exist

## Slack

`ReportScheduler` (already on `cryo serve` / the scheduler process) ticks
`0 10 * * *` in **Asia/Kolkata**. No message is sent when `group_count == 0`.

Enabled `SlackConfig` rows with a webhook are the targets. This is independent of
`daily_report_cron` (the fleet-health digest).

## Local

```bash
make seed-config
make seed                     # demo tables in REST, if needed
python scripts/seed_shared_s3_anomaly_fixture.py
make shared-s3-anomalies      # harvest + print JSON
make shared-s3-anomalies-notify
```

Open `/anomalies` in the UI.
