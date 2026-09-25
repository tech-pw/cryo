# Patched fork of github.com/apache/iceberg-go v0.6.0

This is a full copy of the pinned `apache/iceberg-go` v0.6.0 release (Apache-2.0,
LICENSE/NOTICE retained), trimmed of `cmd/`, `website/`, `dev/`, `.github/`, and
most `*_test.go`/`testdata` (not needed to build against it as a library), with
targeted patches applied on top.

## Why this exists

`v0.6.0` is the latest release on the module proxy as of this patch (verified via
`go list -m -versions github.com/apache/iceberg-go`) — there is no newer upstream
version to bump to that already fixes this.

## Patches

### 1. Day/month/year/hour partition rewrite Avro encoding

`manifest.go`, `avroPartitionData()`: a `DataFile` decoded from a manifest written
by another Iceberg implementation (PyIceberg tags day/month/year/hour-transform
partition fields with an Avro `date` logicalType) can carry a `time.Time` value for
that partition field, but the manifest reader never records that logical-type hint
onto the resulting `DataFile`'s `fieldIDToLogicalType` map. This table's own
`PartitionSpec.PartitionType()` declares such fields as a plain `int` (no logical
decoration), so re-encoding that value into a *new* manifest — e.g. as a "deleted"
entry while committing a compaction rewrite — fails:

```
avro: field data_file.partition.event_date_day: cannot use time.Time with Avro type int
```

Reproduced via `cryo/engine-go` native compaction on any day/month/year/hour-transform
partitioned table (previously tracked as an expected failure:
`tests/integration/native/test_native_compaction_e2e.py::test_native_compaction_execute_day_partition_scoped`).

The fix normalizes a bare `time.Time` value back to the same days-since-epoch `int32`
`DayTransform` itself would have produced (`time.Unix(0,0).UTC()` epoch, matching
`transforms.go`'s `epochTM`), regardless of whether the logical-type hint survived
the round trip.

### 2. Nil-safe Avro partition value conversion

`manifest.go`, `convertAvroValueToIcebergType()`: every logical-type branch
(`Date`, `TimeMillis`, `TimeMicros`, `TimestampMillis`, `TimestampMicros`,
`TimestampNanos`) went straight to an unchecked type assertion
(`v.(int32)`, `v.(int64)`, ...) once the richer `time.Time`/`time.Duration`
case didn't match. A NULL-valued Avro partition field — legal per the
Iceberg spec, e.g. a row whose partition-source column was itself null —
decodes to a bare `nil` interface value, which panicked instead of
converting:

```
panic: interface conversion: interface {} is nil, not int32

goroutine 44 [running]:
github.com/apache/iceberg-go.(*dataFile).convertAvroValueToIcebergType(...)
	manifest.go:1880 +0xa95
github.com/apache/iceberg-go.(*dataFile).initializeMapData.func1()
github.com/apache/iceberg-go.(*dataFile).Partition(...)
github.com/apache/iceberg-go/table.GetPartitionRecord(...)
```

Reproduced in production during a fleet-wide cold-table cleanup — crashed the whole native
maintenance subprocess mid-batch, taking 29 unrelated tables' jobs down
with it (they were reported `native_job_no_result` despite having nothing
to do with the failing table).

The fix returns `nil` for a genuinely-nil Avro value (checked once, up
front) instead of asserting straight into a type. `nil` is the correct
decoded value for a null partition field: `table/scanner.go`'s
`newPartitionRecord` already treats an *absent* map entry as `nil` via a
plain Go map lookup, and Iceberg's own expression evaluator is built to
handle a nil/absent partition value as part of normal null semantics — this
patch makes the explicit-null case behave the same as the
already-correctly-handled absent-key case, rather than a third, panicking
path. A non-nil value of an unexpected type (previously also an unchecked
panic) now degrades the same way, returning `nil` instead of crashing.

Regression tests: `manifest_partition_null_test.go`.

Note: this is a narrower, independent fix from `cryo/engine-go`'s own
`internal/ops/executor.go` `recoverOperation`, which isolates a panic
*per-operation* within one table's subprocess so one crash doesn't take
down unrelated operations in the *same* job. This patch fixes the actual
root-cause panic; `recoverOperation` is defense-in-depth for whatever
still-unknown panic shows up next. Whether cross-*table* batch-pod
isolation (the 29-collateral-jobs scenario) needs its own fix at the
Python dispatch/packing layer is tracked separately as an open follow-up.

### 3. UTF-8–safe string metrics truncate (Spark parity)

Spark/Java Iceberg writes file metrics under `write.metadata.metrics.default=truncate(16)`
using `UnicodeUtil.truncateStringMin` / `truncateStringMax`, which truncate by **Unicode
code points** and keep valid UTF-8. iceberg-go v0.6.0 truncated string
`TruncateTransform` (and therefore lower bounds in `statsAggregator.MinAsBytes`) by
**bytes**, so Hindi/Devanagari (and any multi-byte text) produced invalid UTF-8
`lower_bounds`. Spark later failed reading `$files` with:

```
Failed to decode value as UTF-8: java.nio.HeapByteBuffer[pos=0 lim=16 cap=16]
```

Fixes (aligned with Iceberg Java `UnicodeUtil` and upstream iceberg-go #1198):

- `transforms.go`: string truncate by code points; binary remains byte-based
- `table/internal/utils.go`: `TruncateLowerBoundText` + `TruncateUpperBoundText` with
  Java's surrogate-skipping `incrementCodePoint` for upper bounds; `MinAsBytes` uses
  the string path explicitly

Regression tests: `transforms_utf8_test.go`, `table/internal/metrics_truncate_utf8_test.go`.

### 4. Unwrapped branch-changed commit conflicts

Hive/Glue `UpdateAndStageTable` requirement validation returns
`requirement failed: branch "main" has changed: expected id …` without wrapping
`table.ErrCommitFailed`. `Table.doCommit` then treated the rewrite as terminal,
so `rewrite_data_files` lost races on moderate-write tables.

- `table/commit_conflict.go`: classify and wrap those strings as `ErrCommitFailed`
- `catalog/internal/utils.go`: wrap requirement-validation failures
- `table/table.go` `doCommit`: retry when the catalog returns the unwrapped form

Regression tests: `table/commit_conflict_test.go`.

## Removing this fork

Once a released `apache/iceberg-go` version fixes these upstream (Avro day-transform
issue + UTF-8 string truncate — latter landed on main as #1198), swap the `replace`
directive in `engine-go/go.mod` back to the real module and delete this directory.
