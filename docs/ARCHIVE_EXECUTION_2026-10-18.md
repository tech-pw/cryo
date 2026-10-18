# Archive execution: moving cold partitions between S3 classes

**Date:** 2026-10-18
**Status:** Implemented on branch `feat/table-detail-polish`. Builds on the advisory work in
[`ARCHIVE_TIERING_RESEARCH_2026-10-01.md`](./ARCHIVE_TIERING_RESEARCH_2026-10-01.md).

## What it does

An operator can move a partition's live data files to a cheaper S3 storage class, and back to
Standard, from the table page (**Cost & Savings → Archive cold partitions**). The move is in place:
each object is copied onto its own key with a new storage class. Paths and Iceberg metadata don't
change, so every engine keeps reading the table without a restore step.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Classes offered | `INTELLIGENT_TIERING`, `STANDARD_IA`, `GLACIER_IR`; restore to `STANDARD` | Instant retrieval only. `GLACIER` and `DEEP_ARCHIVE` break reads on a live Iceberg table (research doc §4.5). The API returns 422 for them. |
| Trigger | Manual only: select partitions, preflight, confirm | No scheduled archiving. Operator role, every job audited. |
| Credentials | Reuse the Source's S3 credentials | No new IAM role. When a permission is missing, preflight returns the exact policy to add (`cryo.tiering.executor.iam_policy`). |
| Runner | `ThreadPoolExecutor(max_workers=2)` in the API process (`cryo/tiering/archive_jobs.py`) | Copies are S3-side; no worker or engine slot is needed. A job not updated for 10 minutes is shown as stalled and doesn't block a new one. |
| Concurrency | Same Redis per-table lock maintenance uses (`archive:{job_id}` token, 15 min TTL, renewed) | Archiving never overlaps a compaction, expiry or orphan removal on that table. |
| Compaction | Planner skips partitions recorded in `STANDARD_IA` or `GLACIER_IR`, with a note | Rewriting them would put the data back in Standard and bill the minimum duration. Restore first. Intelligent-Tiering isn't skipped: it has no minimum and no retrieval fee. |

## Flow

1. `GET /tables/{id}/tiering/partitions?state=standard|archived` lists partitions for each view.
   `current_class` and `moved_at` come from `partition_storage_classes`, which only archive and
   restore jobs write. A missing row means Cryo never moved the partition; it doesn't prove the
   files are in Standard.
2. `POST /tables/{id}/tiering/preflight` (operator) returns, without moving anything:
   - data size, object estimate, one-time request cost, monthly storage change, retrieval and
     early-delete charges;
   - permission checks (`HeadObject` on a sample file, and a self-copy that changes nothing for
     `PutObject`), plus bucket reads;
   - bucket guardrails: versioning without a noncurrent-version expiry rule blocks (every copy
     would leave an old Standard version billing), Object Lock blocks, replication warns;
   - `missing_permissions` and `iam_policy` when the credentials can't do the copy;
   - `blockers`, `warnings`, `requires_confirmation`, `can_apply`.
   Missing permissions are reported only in `missing_permissions`, not repeated in `blockers`, so
   the UI shows one alert with the policy. `can_apply` is false when either is non-empty.
3. `POST /tables/{id}/tiering/apply` (operator, 202) reruns preflight and refuses with 403 on a
   scan-only install, 409 when a job is already active, 422 when it can't apply (the 422 detail
   names missing permissions), and 409 when confirmation is required but not given. Glacier IR
   or Standard-IA without trusted read evidence requires `confirm: true`. A reason is required.
4. The job loads the Iceberg table, lists live data files for the selected partitions, and copies
   each one. Per object: already in the target class → skipped; under 128 KiB (non-Standard
   target) → skipped, because S3 bills small objects as 128 KiB in IA classes; offline class,
   SSE-C or over 5 GB → failed with a reason. Every copy is verified with a second `HeadObject`.
   Jobs are idempotent, so an interrupted one is safe to run again.
5. `GET /tables/{id}/tiering/jobs` (viewer) lists recent jobs. The UI polls it every 3 s while a
   job is queued or running.

## Data model

Migration `20261018_1200_archive_execution` adds:

- `archive_jobs`: action, target class, partition keys, status, object counters, GB moved,
  capped error list, actor, reason, timestamps.
- `partition_storage_classes`: `(table_id, partition_key)` → class, objects, bytes, job id.

`job_store._latest_partitions` carries `storage_class` into the planner input, which is how the
planner sees archived partitions.

## Not done

- Realized savings (`realized_savings_usd_month` stays null).
- Bytes by class on the Source page.
- A durable per-partition last-read rollup; read evidence still depends on S3 access-log ingest.

## Tests

- `tests/test_archive_execution.py`: executor against a fake S3, permission probe, guardrails,
  IAM policy, planner skip, job runner, preflight, apply, confirmation, blocked partitions,
  offline-class rejection, missing-permission 422, the `state` filter.
- `web/src/components/table-detail/cost/ArchivePanel.test.tsx`: the panel and dialog.
