# Fragmented partitions: what the check measures (#518)

## Problem

The `Fragmented partitions` deduction compared the raw average file count per partition with a
size-scaled limit (50 warn / 200 crit). A large table that is already compacted still has many
files: 150 GB in 448 files of ~345 MiB is about the right number of files for that much data, yet
it lost 20 points forever, and the UI offered a Compact button that could not change anything.

## How other tools judge "too many files"

| Tool | Signal | Source |
|------|--------|--------|
| Iceberg write path | `write.target-file-size-bytes`, default 512 MiB | [Iceberg configuration](https://iceberg.apache.org/docs/latest/configuration/#write-properties) |
| Iceberg `rewrite_data_files` | rewrites files below `min-file-size-bytes` (75% of target) or above `max-file-size-bytes` (180%); a group needs `min-input-files` (5) | [Spark procedures](https://iceberg.apache.org/docs/latest/spark-procedures/#rewrite_data_files) |
| Amazon S3 Tables | compaction by target file size (default 512 MB, 64–512 MB) | [S3 Tables maintenance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-maintenance.html) |
| Delta Lake OPTIMIZE | bin-packs only files below `optimize.minFileSize` toward `optimize.maxFileSize` | [Delta optimizations](https://docs.delta.io/latest/optimizations-oss.html) |

All of them judge fragmentation by file **size** against a target, never by a raw count.

## Chosen metric

```
needed  = max(1, ceil(avg_partition_bytes / (0.75 × target_file_size)))
excess  = max(0, avg_files_per_partition − needed)
```

- `target_file_size` is the table's `write.target-file-size-bytes`, or Cryo's 256 MiB default
  (the planner's default; smaller than Iceberg's 512 MiB, so it is more lenient here).
- 0.75 is Iceberg's `min-file-size-bytes` default. Files at or above it are not rewrite
  candidates, so a table made of such files gets no deduction: the finding only fires when
  compaction can remove files.
- `excess` is graded against the existing `avg_files_per_partition_warn/crit` thresholds (50/200,
  sqrt-size-scaled). Weights (−8/−20) and A–F bands are unchanged.
- Inputs are stored scan stats (`total_files`, `total_partitions`, `avg_file_size_mb`,
  `avg_files_per_partition`, `target_file_size_bytes`). No live `$files` read.

Using the per-table average partition size is an approximation: a table with very uneven
partitions can hide excess in a few hot partitions. Those partitions are still caught by the
small-file check and by the planner's per-partition candidate ranking.

## Examples

| Table | Before | After |
|-------|--------|-------|
| Unpartitioned, 448 × 345 MiB (151 GB) | −20 critical (448 > 245) | no deduction (needs ~600) |
| One partition, 448 × 2 MiB | −20 critical | −20 critical (443 extra files) |
| One partition, 448 × 200 MiB, 256 MiB target | −20 critical | no deduction (200 MiB > 192 MiB) |

## UI

The Overview deduction row shows no Compact files button for this check when the table has
zero compaction candidates (`small_files_count == 0`); it shows a note instead.
