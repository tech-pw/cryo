---
name: cryo-config-explain
description: Explain why Cryo skipped a table, picked an engine, or graded a table. Use when a plan or a grade looks wrong.
---

# Cryo config explain

Explain a decision Cryo already recorded. Do not invent a flag that would change it.

Read [docs/CAPABILITIES.md](../CAPABILITIES.md) for what an engine can run. Read [cryo/planner/guardrails.py](../../cryo/planner/guardrails.py) for a refused plan. Read [cryo/scanner/health.py](../../cryo/scanner/health.py) for a grade. Do not restate `CORRECT_ORDER`. Point at the tuple.

## Engine

- The planner emits no SQL. Trino `EXECUTE optimize` and Spark `rewrite_data_files` are the same `OperationType`, rendered per engine.
- Trino `OPTIMIZE` is rendered with `file_size_threshold` ([cryo/engines/trino/sql.py](../../cryo/engines/trino/sql.py)). That decides which files are eligible to rewrite. It is not the target size of the files written. No renderer sets or reads `iceberg.target-max-file-size`. That name appears in docs as a catalog property. Do not tell the user Cryo set it.
- The session statements Cryo does emit, from `session_setup`, are `task_min_writer_count = 8` and `writer_scaling_min_data_processed = '32MB'`.
- Z-order and Hilbert are Spark-only. Trino has no curve function. Linear sort is not Spark-only: [cryo/engines/trino/clustering.py](../../cryo/engines/trino/clustering.py) sets `sorted_by`, then runs `OPTIMIZE`. Do not tell the user Trino cannot sort.
- `EngineType` is `trino`, `spark_emr`, `native`, `flink`. Flink `execute` returns `ok=False`. It generates config and does not submit a job. Do not recommend Flink as the fix for a SQL failure.
- Native Go is beta, and it is not unconditionally the default. Routing treats the engine as Go only when the impl value is the string `go`. Native optimize is also gated (no delete files, no z-order, no v3 deletion vectors). Read [docs/CAPABILITIES.md](../CAPABILITIES.md) and the native routing module before saying why native was skipped. `engine-go/README.md` and the routing function disagree on the unset default. Say so. Do not pick one.

## Grade

Read `TableStats.raw["health_deductions"]`. Do not recompute the score.

`compute_health_score` starts at 100. In `_deduct`, a crit threshold subtracts 20 and a warn threshold subtracts 8. Bands in `_grade`: A at or above 90, B at or above 75, C at or above 60, D at or above 40, else F. Four deductions subtract 8 with no threshold key: v3 upgrade candidate, high-velocity streaming, suboptimal properties, legacy file formats.

`HEALTH_THRESHOLDS` keys are `small_file_pct`, `total_files`, `snapshot_count`, `snapshot_age_days`, `delete_file_pct`, `delete_file_count`, `manifest_count`, `partition_count`, `partition_skew_ratio`, `avg_files_per_partition`, `metadata_to_data_ratio`, each with `_warn` and `_crit`. Quote the row's deduction list, not these numbers, when explaining a table.

`partition_count` and `partition_skew_ratio` are structural: they never lower the score. `compute_design_findings` reports them in `TableStats.raw["health_design_findings"]` with a recommendation, because maintenance can't change them. The `total_files` limits rise to 1.5x (warn) and 3x (crit) the layout floor, `max(total_partitions, data size / target file size)`. The `snapshot_count` limits rise to 1.5x and 3x `commits_per_day` times the effective expire window (7 days by default, tightened for fast writers as in `_expire_params`). The fixed values stay as the minimum.

`MIN_SMALL_FILES_FOR_COMPACTION` is 128. A table under that floor can look fragmented by percentage and still never be scheduled. The grade and the plan answer different questions.

## Refuse

- Do not read live `$files` or manifests to explain a stored decision.
- Do not propose a full-table `OPTIMIZE` on a large table without a partition predicate.
- Do not invent a session property, a catalog property, or an engine capability the file you read does not name.
- Do not restate `CORRECT_ORDER`.
