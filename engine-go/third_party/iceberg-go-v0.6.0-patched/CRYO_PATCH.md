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

### 2. UTF-8–safe string metrics truncate (Spark parity)

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

## Removing this fork

Once a released `apache/iceberg-go` version fixes these upstream (Avro day-transform
issue + UTF-8 string truncate — latter landed on main as #1198), swap the `replace`
directive in `engine-go/go.mod` back to the real module and delete this directory.
