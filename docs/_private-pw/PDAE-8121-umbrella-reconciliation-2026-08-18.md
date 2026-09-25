# PDAE-8121 — umbrella status reconciliation (2026-08-18)

**Jira:** [PDAE-8121](https://physicswallah001.atlassian.net/browse/PDAE-8121) (Task, workflow **Dev Complete**). Parent epic: PDAE-2572.

This is a documentation pass, not a code change. It maps the ticket's original three scope bullets to what is actually live in `data-platform-cluster` / namespace `cryo` after today's fleet commit `f967caa` and deploy pin `f922d0c`.

**Do not close this ticket from this note.** Dev Complete already means the *platform* is deployed and usable. Child work under PDAE-2572 is still open.

## Original scope vs live

| Scope bullet | Live? | Evidence |
| --- | --- | --- |
| Deploy/schedule Cryo maintenance for CDP Iceberg tables | **Yes** | Prod Helm in `cryo`; Scheduler + Governor + workers; presets (`default-native-deep`, `claude-cold-table-fleet-optimization`, dedicated hot-table presets). Scanner CronJobs. |
| Compaction, orphan cleanup, snapshot expiry policies | **Yes as capabilities**; **not fleet-complete** | Planner op order `expire_snapshots → remove_orphan_files → optimize → optimize_manifests`. Wave 1 (PDAE-8274) still In Progress (~32% succeed; `native_job_failed` under investigation). Dedicated daily cron on `unified_bq.gold_dbt_pw_bq` (PDAE-8542) is live API config. |
| Cost tracking per table (AWS cost attribution) | **Partial** | Per-table S3 heuristics in `cryo.table_stats` / `cryo.table_cost`. PDAE-8135 Done. **EKS Job tagging** shipped in `f967caa` (PDAE-8544: `eks-workload-name`, `cryo-engine`, `cryo-operation`) but Athena still shows Jobs as untagged until payer **user-tag activation** and Grafana `de05f8e7cjpj4ea` is pointed at `resource_tags_user_*`. |

## What shipped today (children, not this umbrella)

Committed + digest-pinned: PDAE-8346/8347/8348/8352/8353/8541/8542/8544 (and related). PDAE-8354 is investigation-only (data-owner decision). PDAE-8545 filed for Spark expire/orphan OOM on `gold_dbt_pw_bq` — out of 8121's original bullets.

## Workflow recommendation

- **Leave PDAE-8121 at Dev Complete.** Closing it would imply every Iceberg table is healthy and every cost dollar is attributed; that is false.
- **Do not reopen** unless product wants this Task to track "fleet-wide maintenance finished" — that work lives on PDAE-8274 / PDAE-8276 / PDAE-2572.
- Standup comments from 2026-07 still mention PowerBI duplicate queries; that is historical noise relative to Cryo maintenance ops.

## Still outside this umbrella

- Wave 1 drain + `native_job_failed` (PDAE-8274) — other session.
- Wave 2 live-traffic tables (PDAE-8276) — plan only; see `docs/PDAE-8276-wave2-rollout-plan.md`.
- CUR user-tag activation (ops/AWS payer) after PDAE-8544 deploy.
- PDAE-2855 (compaction adoption in CDP schemas) still To do under PDAE-2572.
