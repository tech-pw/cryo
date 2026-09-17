# Top 25 Trino-Cost Tables — Trino/S3/Iceberg Optimization Checklist

Ranked purely by Trino compute cost (CPU-hours), independent of S3 storage cost, over the trailing 7 days as of 2026-09-14.

**Source:** `cdp.trino_logs.trino_queries` (the durable, Iceberg-backed query log with full history — not the same-day `trino_logs.trino.trino_queries` MySQL sink), aggregated via [`docs/top25_trino_cost_query.sql`](top25_trino_cost_query.sql). Scoped to `SELECT` / `FINISHED` queries against Iceberg data tables (Iceberg metadata-table reads like `$files`/`$snapshots` excluded so a table's own maintenance jobs don't inflate its ranking).

**Note:** this is a genuinely different set of tables from the S3-storage-cost ranking (see [`TOP_25_COST_TABLES_CHECKLIST.md`](TOP_25_COST_TABLES_CHECKLIST.md)) — only 4 tables appear on both lists (`gold_batches_pw`, `gold_unleash_experiments_v1`, `gold_orders`, `gold_student_fee_mappings`). A table can be cheap to store but extremely expensive to query if it's hot, poorly pruned, or scanned in full on every query.

Each table is scored against the 12-item Trino/S3/Iceberg checklist (see [`cryo/scanner/checklist.py`](../cryo/scanner/checklist.py)):

| Tier | Item | Weight |
|---|---|---|
| 1 | file_size | 25% |
| 1 | delete_files | 20% |
| 1 | partition_match | 15% |
| 1 | cadence_match | 10% |
| 2 | sort_order | 6% |
| 2 | distribution_mode | 5% |
| 2 | manifest_bloat | 5% |
| 2 | stats_current | 4% |
| 3 | snapshot_retention | 3% |
| 3 | orphan_cleanup | 3% |
| 3 | schema_width | 2% |
| 3 | storage_class | 2% |

---

## Summary

| # | Table | CPU-hrs (7d) | Queries (7d) | Avg CPU-sec/query | GB Scanned (7d) | Checklist Score | Red Items |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | `cdp_revenue.gold_users` | 6,063 | 18,992 | 769 | 898,167 | 93 | snapshot_retention |
| 2 | `cdp_revenue.gold_batches_pw` | 5,684 | 11,472 | 955 | 893,632 | 59 | delete_files, cadence_match |
| 3 | `cdp_revenue.gold_cohorts` | 4,246 | 5,801 | 1,193 | 471,493 | 96 | — |
| 4 | `mview.gold_dbt_orders_base_fact` | 3,886 | 12,545 | 723 | 519,785 | 30 | file_size, delete_files |
| 5 | `pw_experimentation.gold_unleash_experiments_v1` | 3,622 | 4,227 | 947 | 524,955 | 77 | snapshot_retention |
| 6 | `lsq_pw.silver_prospect_activity_extension_base` | 3,551 | 1,863 | 2,000 | 905,855 | 94 | — |
| 7 | `cdp_revenue.gold_transactions` | 2,963 | 5,962 | 973 | 348,010 | 94 | — |
| 8 | `cdp_revenue.gold_orders` | 2,328 | 6,716 | 949 | 308,273 | 93 | snapshot_retention |
| 9 | `cdp_revenue.gold_batches` | 2,202 | 12,119 | 450 | 294,958 | 94 | — |
| 10 | `offline.gold_dbt_offline_students_details` | 1,894 | 17,732 | 168 | 257,585 | 96 | — |
| 11 | `cdp_revenue.gold_student_fee_mappings` | 1,731 | 4,516 | 1,104 | 224,869 | 91 | — |
| 12 | `cdp_revenue.gold_order_details` | 1,708 | 4,527 | 1,090 | 218,480 | 93 | orphan_cleanup |
| 13 | `pw_bq.silver_dbt_pw_batch_listing_page` | 1,644 | 497 | 5,658 | 118,006 | 96 | — |
| 14 | `cdp_revenue.gold_coupons` | 1,424 | 2,408 | 1,319 | 188,024 | 96 | — |
| 15 | `cdp_revenue.gold_pass` | 1,369 | 1,293 | 2,472 | 158,018 | 94 | — |
| 16 | `lsq_pw.silver_prospect_base` | 1,310 | 1,976 | 1,943 | 275,017 | 82 | cadence_match |
| 17 | `cdp_revenue.gold_batch_student_mappings` | 1,285 | 6,041 | 578 | 179,975 | 94 | — |
| 18 | `store.gold_product_variants` | 1,283 | 6,483 | 279 | 58,504 | 94 | — |
| 19 | `ott.gold_subscription_plan_user_mappings` | 1,204 | 3,954 | 511 | 136,331 | 94 | — |
| 20 | `pw_bq.silver_dbt_buy_now_clicked` | 1,154 | 1,319 | 2,551 | 129,150 | 96 | — |
| 21 | `mview.gold_dbt_video_stats_v3` | 1,112 | 3,808 | 830 | 247,819 | 96 | — |
| 22 | `pw_bq.silver_dbt_batch_explore_click` | 1,097 | 961 | 3,184 | 102,066 | 96 | — |
| 23 | `cdp_revenue.gold_test_categories` | 1,067 | 3,238 | 887 | 126,935 | 96 | — |
| 24 | `cdp_revenue.gold_batch_plans` | 983 | 2,457 | 871 | 126,894 | 94 | — |
| 25 | `cdp_revenue.gold_cohort_test_mappings` | 938 | 2,133 | 1,335 | 94,968 | 91 | — |

---

## Detail

### 1. `cdp_revenue.gold_users`

- **Trino CPU-hours (7d):** 6,063.1  ·  **Wall-hours (7d):** 345.8
- **Query count (7d):** 18,992  ·  **Avg CPU-sec/query:** 769
- **Physical bytes scanned (7d):** 898,167 GB
- **Weighted checklist score:** 92.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 15 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 20 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 239 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 4d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 2. `cdp_revenue.gold_batches_pw`

- **Trino CPU-hours (7d):** 5,683.9  ·  **Wall-hours (7d):** 354.9
- **Query count (7d):** 11,472  ·  **Avg CPU-sec/query:** 955
- **Physical bytes scanned (7d):** 893,632 GB
- **Weighted checklist score:** 59.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 40 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🔴 red | 100% delete ratio, 692 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🔴 red | partition_write_idle_hours=0h is too tight for a table committing every ~6.0h -- risk of racing the writer |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 21 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 29 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 0d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 3. `cdp_revenue.gold_cohorts`

- **Trino CPU-hours (7d):** 4,245.7  ·  **Wall-hours (7d):** 187.4
- **Query count (7d):** 5,801  ·  **Avg CPU-sec/query:** 1,193
- **Physical bytes scanned (7d):** 471,493 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 8 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 6 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 12 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 4. `mview.gold_dbt_orders_base_fact`

- **Trino CPU-hours (7d):** 3,885.7  ·  **Wall-hours (7d):** 260.2
- **Query count (7d):** 12,545  ·  **Avg CPU-sec/query:** 723
- **Physical bytes scanned (7d):** 519,785 GB
- **Weighted checklist score:** 29.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 95% of 220 files are small |
| Delete-file count/ratio | 20.0% | 🔴 red | 85% delete ratio, 187 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟡 yellow | 6.4 commits/day table with no maintenance preset configured yet -- verify idle-hours before scheduling one |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 33-partition table |
| Manifest count/size | 5.0% | 🟢 green | 3 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 70 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 5. `pw_experimentation.gold_unleash_experiments_v1`

- **Trino CPU-hours (7d):** 3,622.1  ·  **Wall-hours (7d):** 151.2
- **Query count (7d):** 4,227  ·  **Avg CPU-sec/query:** 947
- **Physical bytes scanned (7d):** 524,955 GB
- **Weighted checklist score:** 77.2/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟡 yellow | 21% of 98,096 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | idle_hours=72h safely exceeds ~0.7h commit gap |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 23 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 642 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 4d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 6. `lsq_pw.silver_prospect_activity_extension_base`

- **Trino CPU-hours (7d):** 3,551.4  ·  **Wall-hours (7d):** 119.4
- **Query count (7d):** 1,863  ·  **Avg CPU-sec/query:** 2,000
- **Physical bytes scanned (7d):** 905,855 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | 9% small files |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 75 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 73 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 7. `cdp_revenue.gold_transactions`

- **Trino CPU-hours (7d):** 2,963.1  ·  **Wall-hours (7d):** 136.7
- **Query count (7d):** 5,962  ·  **Avg CPU-sec/query:** 973
- **Physical bytes scanned (7d):** 348,010 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 8 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 77 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 72 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 8. `cdp_revenue.gold_orders`

- **Trino CPU-hours (7d):** 2,328.5  ·  **Wall-hours (7d):** 112.8
- **Query count (7d):** 6,716  ·  **Avg CPU-sec/query:** 949
- **Physical bytes scanned (7d):** 308,273 GB
- **Weighted checklist score:** 92.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 2 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 76 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 261 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 0d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 9. `cdp_revenue.gold_batches`

- **Trino CPU-hours (7d):** 2,201.6  ·  **Wall-hours (7d):** 179.2
- **Query count (7d):** 12,119  ·  **Avg CPU-sec/query:** 450
- **Physical bytes scanned (7d):** 294,958 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 18 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 5 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 171 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 10. `offline.gold_dbt_offline_students_details`

- **Trino CPU-hours (7d):** 1,894.0  ·  **Wall-hours (7d):** 252.1
- **Query count (7d):** 17,732  ·  **Avg CPU-sec/query:** 168
- **Physical bytes scanned (7d):** 257,585 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 13 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 8 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 11. `cdp_revenue.gold_student_fee_mappings`

- **Trino CPU-hours (7d):** 1,730.9  ·  **Wall-hours (7d):** 106.2
- **Query count (7d):** 4,516  ·  **Avg CPU-sec/query:** 1,104
- **Physical bytes scanned (7d):** 224,869 GB
- **Weighted checklist score:** 91.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 5 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 11-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 2 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟡 yellow | remove_orphan_files last ran 38d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 12. `cdp_revenue.gold_order_details`

- **Trino CPU-hours (7d):** 1,708.5  ·  **Wall-hours (7d):** 89.6
- **Query count (7d):** 4,527  ·  **Avg CPU-sec/query:** 1,090
- **Physical bytes scanned (7d):** 218,480 GB
- **Weighted checklist score:** 92.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 1 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🔴 red | remove_orphan_files has never run successfully on this table |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 13. `pw_bq.silver_dbt_pw_batch_listing_page`

- **Trino CPU-hours (7d):** 1,644.1  ·  **Wall-hours (7d):** 12.3
- **Query count (7d):** 497  ·  **Avg CPU-sec/query:** 5,658
- **Physical bytes scanned (7d):** 118,006 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | 13% small files |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 4 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 13 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 14. `cdp_revenue.gold_coupons`

- **Trino CPU-hours (7d):** 1,424.5  ·  **Wall-hours (7d):** 52.2
- **Query count (7d):** 2,408  ·  **Avg CPU-sec/query:** 1,319
- **Physical bytes scanned (7d):** 188,024 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 2 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 5 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 15. `cdp_revenue.gold_pass`

- **Trino CPU-hours (7d):** 1,369.5  ·  **Wall-hours (7d):** 37.9
- **Query count (7d):** 1,293  ·  **Avg CPU-sec/query:** 2,472
- **Physical bytes scanned (7d):** 158,018 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 1 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟡 yellow | remove_orphan_files last ran 33d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 16. `lsq_pw.silver_prospect_base`

- **Trino CPU-hours (7d):** 1,309.8  ·  **Wall-hours (7d):** 47.8
- **Query count (7d):** 1,976  ·  **Avg CPU-sec/query:** 1,943
- **Physical bytes scanned (7d):** 275,017 GB
- **Weighted checklist score:** 82.1/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 89 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🔴 red | partition_write_idle_hours=0h is too tight for a table committing every ~1.0h -- risk of racing the writer |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 10 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 79 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 17. `cdp_revenue.gold_batch_student_mappings`

- **Trino CPU-hours (7d):** 1,285.3  ·  **Wall-hours (7d):** 88.8
- **Query count (7d):** 6,041  ·  **Avg CPU-sec/query:** 578
- **Physical bytes scanned (7d):** 179,975 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 2 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 60 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 75 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 18. `store.gold_product_variants`

- **Trino CPU-hours (7d):** 1,283.3  ·  **Wall-hours (7d):** 213.8
- **Query count (7d):** 6,483  ·  **Avg CPU-sec/query:** 279
- **Physical bytes scanned (7d):** 58,504 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 26 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 2 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 75 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 19. `ott.gold_subscription_plan_user_mappings`

- **Trino CPU-hours (7d):** 1,204.5  ·  **Wall-hours (7d):** 61.8
- **Query count (7d):** 3,954  ·  **Avg CPU-sec/query:** 511
- **Physical bytes scanned (7d):** 136,331 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 113 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 7 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 74 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 20. `pw_bq.silver_dbt_buy_now_clicked`

- **Trino CPU-hours (7d):** 1,153.7  ·  **Wall-hours (7d):** 24.8
- **Query count (7d):** 1,319  ·  **Avg CPU-sec/query:** 2,551
- **Physical bytes scanned (7d):** 129,150 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 6 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 13 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 9 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 21. `mview.gold_dbt_video_stats_v3`

- **Trino CPU-hours (7d):** 1,112.0  ·  **Wall-hours (7d):** 81.5
- **Query count (7d):** 3,808  ·  **Avg CPU-sec/query:** 830
- **Physical bytes scanned (7d):** 247,819 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 51 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 3 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 11 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 0d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 22. `pw_bq.silver_dbt_batch_explore_click`

- **Trino CPU-hours (7d):** 1,096.7  ·  **Wall-hours (7d):** 15.5
- **Query count (7d):** 961  ·  **Avg CPU-sec/query:** 3,184
- **Physical bytes scanned (7d):** 102,066 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | 19% small files |
| Delete-file count/ratio | 20.0% | 🟢 green | 0% delete ratio, 1 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 3 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 9 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 1d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 23. `cdp_revenue.gold_test_categories`

- **Trino CPU-hours (7d):** 1,067.3  ·  **Wall-hours (7d):** 82.0
- **Query count (7d):** 3,238  ·  **Avg CPU-sec/query:** 887
- **Physical bytes scanned (7d):** 126,935 GB
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 25 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 2 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 36 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 4d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 24. `cdp_revenue.gold_batch_plans`

- **Trino CPU-hours (7d):** 982.6  ·  **Wall-hours (7d):** 88.3
- **Query count (7d):** 2,457  ·  **Avg CPU-sec/query:** 871
- **Physical bytes scanned (7d):** 126,894 GB
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 38 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | Unpartitioned or single-partition table, distribution mode not relevant |
| Manifest count/size | 5.0% | 🟢 green | 9 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 168 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 25. `cdp_revenue.gold_cohort_test_mappings`

- **Trino CPU-hours (7d):** 937.7  ·  **Wall-hours (7d):** 47.5
- **Query count (7d):** 2,133  ·  **Avg CPU-sec/query:** 1,335
- **Physical bytes scanned (7d):** 94,968 GB
- **Weighted checklist score:** 91.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 25 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 31-partition table |
| Manifest count/size | 5.0% | 🟢 green | 42 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 1 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟡 yellow | remove_orphan_files last ran 34d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |
