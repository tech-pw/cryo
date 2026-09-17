# Top 25 Cost-Impact Tables — Trino/S3/Iceberg Optimization Checklist

Ranked by combined monthly cost (S3 storage + S3 request cost), as scored by Cryo's live fleet data on 2026-09-14.

Each table is scored against the 12-item Trino/S3/Iceberg checklist (see [cryo/scanner/checklist.py](../cryo/scanner/checklist.py)):

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

| # | Table | Monthly Cost | Weighted Score | Red Items |
|---|---|---:|---:|---|
| 1 | `unified_bq.gold_dbt_pw_bq` | $681.62 | 75 | cadence_match, snapshot_retention |
| 2 | `cdp_revenue.silver_infinite_practice_result_questions_temp` | $132.29 | 96 | — |
| 3 | `pw_experimentation.gold_unleash_experiments_v1` | $58.29 | 77 | snapshot_retention |
| 4 | `cdp_revenue.gold_users` | $36.26 | 93 | snapshot_retention |
| 5 | `pw_bq.silver_dbt_pw_my_batch_navigation` | $28.54 | 96 | — |
| 6 | `cdp_revenue_txn.gold_orders` | $26.58 | 64 | file_size |
| 7 | `cdp_revenue.gold_student_fee_mappings` | $22.39 | 93 | snapshot_retention |
| 8 | `lsq_btl.silver_prospect_activity_base` | $20.67 | 84 | cadence_match |
| 9 | `offline.gold_dbt_tgs_drip` | $19.67 | 60 | file_size |
| 10 | `cdp_revenue.gold_batch_subject_schedules_pw` | $18.76 | 62 | delete_files, snapshot_retention |
| 11 | `pw_bq.silver_dbt_dpp_question_navigation` | $18.12 | 81 | — |
| 12 | `cdp_revenue.gold_orders` | $17.13 | 93 | snapshot_retention |
| 13 | `mview.gold_dbt_drip_marketing_flow` | $16.94 | 60 | file_size |
| 14 | `user_experience.silver_test_student_mappings_backfill_19082026` | $13.42 | 57 | file_size, orphan_cleanup |
| 15 | `lsq_pw.gold_prospect_activity_extension_base` | $11.61 | 49 | file_size, cadence_match, snapshot_retention |
| 16 | `user_experience.silver_results` | $7.93 | 65 | file_size |
| 17 | `mview.load_test_basefact` | $6.98 | 96 | — |
| 18 | `cdp_revenue.gold_doubts_ops_new` | $6.16 | 62 | file_size |
| 19 | `mview.gold_dbt_video_data_temp` | $5.97 | 67 | delete_files |
| 20 | `offline.gold_dbt_ett_device_punch_logs` | $5.91 | 60 | file_size |
| 21 | `offline.gold_dbt_lead_source_ay26_ay26` | $4.44 | 62 | file_size |
| 22 | `mview.gold_dbt_online_calllogs` | $3.87 | 62 | file_size |
| 23 | `lsq_pw.gold_custom_object_prospect_activity_base` | $3.84 | 94 | — |
| 24 | `cdp_revenue.gold_pw_subjectwise_results_pw` | $3.63 | 62 | file_size |
| 25 | `offline.gold_dbt_drip_walkin_conversion_ay26` | $3.18 | 96 | — |

---

## Detail

### 1. `unified_bq.gold_dbt_pw_bq`

- **Monthly cost:** $681.62  (storage $678.11 + requests $3.51)
- **Projected savings if fixed:** $97.24/mo
- **Size:** 25,254.9 GB across 156,480 files (2,444 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 66,273 · **Grade:** F
- **Weighted checklist score:** 75.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | 2% small files |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🔴 red | partition_write_idle_hours=0h is too tight for a table committing every ~0.2h -- risk of racing the writer |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟡 yellow | 102 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 779 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟡 yellow | remove_orphan_files last ran 28d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 2. `cdp_revenue.silver_infinite_practice_result_questions_temp`

- **Monthly cost:** $132.29  (storage $130.71 + requests $1.58)
- **Projected savings if fixed:** $130.71/mo
- **Size:** 4,981.7 GB across 22,419 files (0 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 389 · **Grade:** C
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 43 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 4 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 1d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 3. `pw_experimentation.gold_unleash_experiments_v1`

- **Monthly cost:** $58.29  (storage $54.48 + requests $3.81)
- **Projected savings if fixed:** $13.74/mo
- **Size:** 1,771.2 GB across 98,096 files (20,717 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 74,353 · **Grade:** F
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
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 4. `cdp_revenue.gold_users`

- **Monthly cost:** $36.26  (storage $14.63 + requests $21.63)
- **Projected savings if fixed:** $13.97/mo
- **Size:** 28.7 GB across 278 files (15 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 103 · **Grade:** C
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

### 5. `pw_bq.silver_dbt_pw_my_batch_navigation`

- **Monthly cost:** $28.54  (storage $28.39 + requests $0.15)
- **Projected savings if fixed:** $28.38/mo
- **Size:** 947.4 GB across 4,347 files (342 small)
- **Delete files:** 1 (0.02% of files)
- **Partitions:** 842 · **Grade:** A
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | 8% small files |
| Delete-file count/ratio | 20.0% | 🟢 green | 0% delete ratio, 1 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 5 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 20 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 0d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 6. `cdp_revenue_txn.gold_orders`

- **Monthly cost:** $26.58  (storage $2.53 + requests $24.05)
- **Projected savings if fixed:** $2.50/mo
- **Size:** 105.2 GB across 33,151 files (33,033 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 2,843 · **Grade:** F
- **Weighted checklist score:** 63.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 100% of 33,151 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 26 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 76 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 7. `cdp_revenue.gold_student_fee_mappings`

- **Monthly cost:** $22.39  (storage $15.32 + requests $7.07)
- **Projected savings if fixed:** $14.85/mo
- **Size:** 20.5 GB across 2,708 files (17 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 2,680 · **Grade:** D
- **Weighted checklist score:** 92.6/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 17 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 61 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 221 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 8. `lsq_btl.silver_prospect_activity_base`

- **Monthly cost:** $20.67  (storage $20.06 + requests $0.62)
- **Projected savings if fixed:** $20.05/mo
- **Size:** 955.8 GB across 4,452 files (54 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 320 · **Grade:** A
- **Weighted checklist score:** 84.0/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 54 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🔴 red | partition_write_idle_hours=0h is too tight for a table committing every ~6.0h -- risk of racing the writer |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 19 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 29 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 9. `offline.gold_dbt_tgs_drip`

- **Monthly cost:** $19.67  (storage $17.50 + requests $2.17)
- **Projected savings if fixed:** $16.06/mo
- **Size:** 62.5 GB across 4,202 files (4,118 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 100 · **Grade:** D
- **Weighted checklist score:** 60.5/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 98% of 4,202 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 100-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 97 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 10. `cdp_revenue.gold_batch_subject_schedules_pw`

- **Monthly cost:** $18.76  (storage $6.56 + requests $12.20)
- **Projected savings if fixed:** $6.46/mo
- **Size:** 4.4 GB across 78 files (0 small)
- **Delete files:** 316 (100.0% of files)
- **Partitions:** 77 · **Grade:** F
- **Weighted checklist score:** 61.7/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🔴 red | 100% delete ratio, 316 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟡 yellow | Streaming table with no maintenance preset configured yet -- verify idle-hours before scheduling one |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 12 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 439 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 11. `pw_bq.silver_dbt_dpp_question_navigation`

- **Monthly cost:** $18.12  (storage $18.01 + requests $0.11)
- **Projected savings if fixed:** $17.94/mo
- **Size:** 726.9 GB across 4,871 files (1,417 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 561 · **Grade:** B
- **Weighted checklist score:** 80.9/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟡 yellow | 29% of 4,871 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 4 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 26 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 12. `cdp_revenue.gold_orders`

- **Monthly cost:** $17.13  (storage $10.17 + requests $6.96)
- **Projected savings if fixed:** $9.38/mo
- **Size:** 34.6 GB across 255 files (2 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 96 · **Grade:** C
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
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 13. `mview.gold_dbt_drip_marketing_flow`

- **Monthly cost:** $16.94  (storage $12.52 + requests $4.42)
- **Projected savings if fixed:** $10.92/mo
- **Size:** 69.5 GB across 2,824 files (2,823 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 16 · **Grade:** B
- **Weighted checklist score:** 60.5/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 100% of 2,824 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 16-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 104 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 14. `user_experience.silver_test_student_mappings_backfill_19082026`

- **Monthly cost:** $13.42  (storage $13.41 + requests $0.00)
- **Projected savings if fixed:** $13.41/mo
- **Size:** 428.8 GB across 1,674 files (954 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 379 · **Grade:** D
- **Weighted checklist score:** 56.8/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 57% of 1,674 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 379-partition table |
| Manifest count/size | 5.0% | 🟢 green | 76 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 72 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🔴 red | remove_orphan_files has never run successfully on this table |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 15. `lsq_pw.gold_prospect_activity_extension_base`

- **Monthly cost:** $11.61  (storage $7.21 + requests $4.40)
- **Projected savings if fixed:** $4.79/mo
- **Size:** 105.0 GB across 1,933 files (1,464 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 605 · **Grade:** C
- **Weighted checklist score:** 49.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 76% of 1,933 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🔴 red | partition_write_idle_hours=0h is too tight for a table committing every ~0.5h -- risk of racing the writer |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 88 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🔴 red | 357 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 1d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 16. `user_experience.silver_results`

- **Monthly cost:** $7.93  (storage $7.93 + requests $0.00)
- **Projected savings if fixed:** $7.93/mo
- **Size:** 344.6 GB across 10,314 files (9,966 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 137 · **Grade:** F
- **Weighted checklist score:** 65.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 97% of 10,314 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='range' |
| Manifest count/size | 5.0% | 🟢 green | 97 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 36 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 17. `mview.load_test_basefact`

- **Monthly cost:** $6.98  (storage $6.81 + requests $0.17)
- **Projected savings if fixed:** $6.81/mo
- **Size:** 287.8 GB across 2,454 files (0 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 791 · **Grade:** C
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 2 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 4 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 18. `cdp_revenue.gold_doubts_ops_new`

- **Monthly cost:** $6.16  (storage $4.76 + requests $1.40)
- **Projected savings if fixed:** $4.09/mo
- **Size:** 29.0 GB across 514 files (394 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 30 · **Grade:** A
- **Weighted checklist score:** 62.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 77% of 514 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 30-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 5 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 19. `mview.gold_dbt_video_data_temp`

- **Monthly cost:** $5.97  (storage $5.54 + requests $0.43)
- **Projected savings if fixed:** $5.48/mo
- **Size:** 240.1 GB across 1,013 files (0 small)
- **Delete files:** 31,510 (100.0% of files)
- **Partitions:** 619 · **Grade:** C
- **Weighted checklist score:** 66.7/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🔴 red | 100% delete ratio, 31,510 delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 619-partition table |
| Manifest count/size | 5.0% | 🟢 green | 78 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 8 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟡 yellow | remove_orphan_files last ran 39d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 20. `offline.gold_dbt_ett_device_punch_logs`

- **Monthly cost:** $5.91  (storage $5.43 + requests $0.49)
- **Projected savings if fixed:** $5.32/mo
- **Size:** 4.6 GB across 304 files (288 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 56 · **Grade:** B
- **Weighted checklist score:** 60.5/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 95% of 304 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 56-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 63 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 21. `offline.gold_dbt_lead_source_ay26_ay26`

- **Monthly cost:** $4.44  (storage $4.44 + requests $0.00)
- **Projected savings if fixed:** $4.08/mo
- **Size:** 15.7 GB across 463 files (427 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 18 · **Grade:** C
- **Weighted checklist score:** 62.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 92% of 463 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 18-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 4 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 3d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 22. `mview.gold_dbt_online_calllogs`

- **Monthly cost:** $3.87  (storage $3.54 + requests $0.33)
- **Projected savings if fixed:** $3.33/mo
- **Size:** 9.1 GB across 354 files (325 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 46 · **Grade:** A
- **Weighted checklist score:** 62.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 92% of 354 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | No query pattern data available yet |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 46-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 48 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 4d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 23. `lsq_pw.gold_custom_object_prospect_activity_base`

- **Monthly cost:** $3.84  (storage $3.44 + requests $0.40)
- **Projected savings if fixed:** $3.34/mo
- **Size:** 77.1 GB across 602 files (0 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 596 · **Grade:** B
- **Weighted checklist score:** 94.4/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 53 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟡 yellow | 50 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 24. `cdp_revenue.gold_pw_subjectwise_results_pw`

- **Monthly cost:** $3.63  (storage $3.09 + requests $0.54)
- **Projected savings if fixed:** $2.48/mo
- **Size:** 26.4 GB across 929 files (843 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 28 · **Grade:** B
- **Weighted checklist score:** 62.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🔴 red | 91% of 929 files are small |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟡 yellow | write.distribution-mode is 'none' on a 28-partition table |
| Manifest count/size | 5.0% | 🟢 green | 1 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 36 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 4d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |

### 25. `offline.gold_dbt_drip_walkin_conversion_ay26`

- **Monthly cost:** $3.18  (storage $2.86 + requests $0.31)
- **Projected savings if fixed:** $2.67/mo
- **Size:** 13.5 GB across 68 files (0 small)
- **Delete files:** 0 (0.0% of files)
- **Partitions:** 14 · **Grade:** A
- **Weighted checklist score:** 96.3/100

| Item | Weight | Status | Reason |
|---|---:|:---:|---|
| File size vs target | 25.0% | 🟢 green | Only 0 small files (below compaction floor) |
| Delete-file count/ratio | 20.0% | 🟢 green | No delete files |
| Partition scheme matches query predicates | 15.0% | ⚪ unknown | Not enough query volume to judge filter pattern |
| Compaction cadence matches write velocity | 10.0% | 🟢 green | Low write velocity, no cadence risk |
| Sort order matches filter columns | 6.0% | 🟡 yellow | No sort order set |
| write.distribution-mode set | 5.0% | 🟢 green | write.distribution-mode='hash' |
| Manifest count/size | 5.0% | 🟢 green | 33 manifests |
| Column/NDV statistics current | 4.0% | 🟢 green | Metadata-to-data ratio 0.0% |
| Snapshot retention tuned | 3.0% | 🟢 green | 17 snapshots retained |
| Orphan file cleanup runs | 3.0% | 🟢 green | remove_orphan_files last ran 2d ago |
| Schema width / unused columns | 2.0% | ⚪ unknown | Column-level stats not available |
| S3 storage class reachable | 2.0% | ⚪ unknown | No file sample available to check storage class |
