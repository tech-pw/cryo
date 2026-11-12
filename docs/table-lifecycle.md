# Table lifecycle: active and archived (#519)

## Problem

"Stale table commits" costs 20 points once a table has gone 30 days without a commit. Backups,
snapshot copies and retired tables are read-only on purpose, so they lost those points forever,
and Path to A could only say "write to the table, or retire it", with no way to act in Cryo.

## How other platforms model it

| Platform | Mechanism | What we took from it |
|---|---|---|
| Apache Iceberg | Maintenance is expire snapshots, remove orphan files, rewrite manifests, compact data. Compaction only helps tables that are still being written. | A read-only table still needs metadata and file housekeeping, but not compaction. |
| AWS S3 Tables | Managed maintenance is split into compaction, snapshot management and unreferenced-file removal, configured separately per table. | Keep the three housekeeping jobs on, turn compaction off. |
| BigQuery | Table data unmodified for 90 consecutive days moves to long-term storage pricing automatically; a write resets the clock. | 90 days without a commit is a recognised "cold" signal: used for the archive-candidate suggestion. |
| Delta Lake / Databricks | `delta.appendOnly` and similar table properties restrict what writes may do; OPTIMIZE and VACUUM are separate. | A table-level flag that changes which operations apply, not how they are scored. |
| DataHub | A `deprecation` aspect: deprecated flag, note, actor, optional decommission time. | Store who, when and why with the state. |
| OpenMetadata | A table `lifeCycle` property (created/updated/accessed timestamps) used for lifecycle and cost reports. | "Lifecycle" as the field name, and recent access as part of the decision. |

Sources:

- Iceberg maintenance: https://iceberg.apache.org/docs/latest/maintenance/
- S3 Tables maintenance: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-maintenance.html
- BigQuery storage pricing (long-term storage): https://cloud.google.com/bigquery/pricing#storage
- Delta table properties: https://docs.delta.io/table-properties/
- DataHub deprecation: https://docs.datahub.com/docs/api/tutorials/deprecation
- OpenMetadata table schema (`lifeCycle`): https://docs.open-metadata.org/main-concepts/metadata-standard/schemas/entity/data/table

## Decision

- `cryo.tables.lifecycle` is `active` (default) or `archived`, plus `lifecycle_changed_at`,
  `lifecycle_changed_by` and `lifecycle_reason`. Additive migration `20261112_1200`.
- Set by an admin from the Config tab (Lifecycle card) or the Path to A `mark_archived` step, both
  with a typed table name. API: `PUT /api/v1/tables/{id}/lifecycle`
  `{lifecycle, reason, confirm}`. Every change writes an `audit_log` row with
  `action = table_lifecycle`, the actor, and `result_metrics` = before, after, reason and the
  score before and after.
- Health: archived tables skip only the stale-commit check. Weights and thresholds are unchanged
  (AGENTS.md §5 parity). The stored grade is re-graded when the lifecycle changes, and every scan
  applies it again.
- Maintenance: archived tables drop the operations that rewrite or delete data
  (convert_equality_deletes, convert_deletes, rewrite_position_deletes, optimize,
  compute_statistics, expire_data, migrate_partition_spec) through the existing per-table
  exclusion path, so the planner is unchanged and engine-agnostic. expire_snapshots,
  remove_orphan_files and optimize_manifests still run; cost reporting is unchanged.
  expire_data is dropped because an archive is frozen; return the table to active to apply a TTL.
- Path to A: archived tables get no compaction, delete-rewrite, property, partition or sort steps.
  Active tables with the stale-commit deduction get **Mark as archived** (admin, typed
  confirmation) with the projected grade after the deduction is removed.
- Suggestion only: an active table with no commit in 90+ days and no recorded reads in 30 days is
  flagged `archive_candidate` (Table API, Config tab, Path to A step text). Cryo never archives
  a table on its own.
- UI: an Archived badge on the table header and the Tables list; a Lifecycle filter in the list's
  advanced filters (filter spec field `lifecycle`).

## Not done

- Setting lifecycle from a preset rule (namespace or name pattern). Preset rules choose a
  workflow; letting them also flip a health-affecting state would make an unaudited, implicit
  change per table. A follow-up can add a bulk "mark archived" over a filter spec instead.
