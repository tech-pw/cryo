package scanstats

import (
	"strings"
	"testing"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestTemporalBoundsRenderLikePythonAndSamplesKeepOnlyRequestedColumns(t *testing.T) {
	if got := jsonable(iceberg.Date(20605)); got != "2026-06-01" {
		t.Fatalf("date = %v", got)
	}
	if got := jsonable(iceberg.Timestamp(1780272000000000)); got != "2026-06-01T00:00:00Z" {
		t.Fatalf("timestamp = %v", got)
	}
	kept := keepColumns(map[string]any{"event_date": "2026-06-01", "email": "a@x.com"}, []string{"event_date"})
	if len(kept) != 1 || kept["event_date"] != "2026-06-01" {
		t.Fatalf("kept = %v", kept)
	}

	acc := newAccumulator(nil, Options{SampleColumns: []string{"email", "event_date", "missing"}})
	acc.columnDefs = map[int]protocol.AggregateFixtureColumn{
		1: {FieldID: 1, ColumnName: "email"}, 2: {FieldID: 2, ColumnName: "event_date"},
	}
	acc.columnTypes = map[int]iceberg.Type{1: iceberg.PrimitiveTypes.String, 2: iceberg.PrimitiveTypes.Date}
	if got := acc.boundSafeSampleColumns(); len(got) != 1 || got[0] != "event_date" {
		t.Fatalf("string column must never be sampled: %v", got)
	}
}

func TestCommitLogIsOrderedCappedAndParsesSummaryCounters(t *testing.T) {
	snaps := make([]table.Snapshot, 0, maxCommitLog+5)
	for i := maxCommitLog + 4; i >= 0; i-- {
		snaps = append(snaps, table.Snapshot{
			TimestampMs: int64(1000 + i),
			Summary: &table.Summary{
				Operation: table.OpAppend,
				Properties: iceberg.Properties{
					"added-records": "10", "total-records": "500", "deleted-records": "x",
				},
			},
		})
	}
	snaps = append(snaps, table.Snapshot{TimestampMs: 5000})

	log := commitLog(snaps)

	if len(log) != maxCommitLog {
		t.Fatalf("len = %d, want %d", len(log), maxCommitLog)
	}
	last := log[len(log)-1]
	if last.CommittedAtMs != 5000 || last.Operation != "" || last.AddedRecords != nil {
		t.Fatalf("summary-less snapshot should be last with no counters: %+v", last)
	}
	prev := log[len(log)-2]
	if prev.Operation != "append" || *prev.AddedRecords != 10 || *prev.TotalRecords != 500 {
		t.Fatalf("counters not parsed: %+v", prev)
	}
	if prev.DeletedRecords != nil || prev.TotalPositionDeletes != nil {
		t.Fatalf("unparseable or absent counters must be nil: %+v", prev)
	}
	for i := 1; i < len(log); i++ {
		if log[i].CommittedAtMs < log[i-1].CommittedAtMs {
			t.Fatalf("not ordered at %d", i)
		}
	}
}

func TestDecodeBoundsUsesSchemaTypesAndNames(t *testing.T) {
	encoded, err := iceberg.NewLiteral[int64](42).MarshalBinary()
	if err != nil {
		t.Fatalf("marshal literal: %v", err)
	}

	result := decodeBounds(
		map[int][]byte{7: encoded},
		map[int]protocol.AggregateFixtureColumn{
			7: {FieldID: 7, ColumnName: "account_id", ColumnType: "long"},
		},
		map[int]iceberg.Type{7: iceberg.PrimitiveTypes.Int64},
	)

	if got := result["account_id"]; got != int64(42) {
		t.Fatalf("decoded bound = %#v, want int64(42)", got)
	}
}

func TestAggregateFromRecordsDayPartitioned(t *testing.T) {
	const mb = 1024 * 1024
	fields := []protocol.ScanStatsPartitionField{
		{
			BaseColumn:     "event_date",
			PartitionField: "event_date_day",
			Transform:      "day",
			ColType:        "date",
		},
	}
	records := []FileRecord{
		{SizeBytes: 10 * mb, RecordCount: 10, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2026-06-29"}},
		{SizeBytes: 20 * mb, RecordCount: 20, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2026-06-29"}},
		{SizeBytes: 30 * mb, RecordCount: 30, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2026-06-29"}},
		{SizeBytes: 256 * mb, RecordCount: 100, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2026-06-30"}},
	}

	resp := AggregateFromRecords(records, fields, Options{})
	if !resp.OK {
		t.Fatalf("expected ok, got error=%v", resp.Error)
	}
	if resp.TotalFiles != 4 {
		t.Fatalf("total_files=%d", resp.TotalFiles)
	}
	if resp.TotalRecords != 160 {
		t.Fatalf("total_records=%d", resp.TotalRecords)
	}
	if len(resp.Partitions) != 2 {
		t.Fatalf("partitions=%d", len(resp.Partitions))
	}
	var smallSum int
	for _, p := range resp.Partitions {
		if p.Small > 1 && p.Files > 1 {
			smallSum += p.Small
		}
	}
	if smallSum != 3 {
		t.Fatalf("small_files_count via F partitions=%d", smallSum)
	}
}

func TestAggregateFromRecordsDeletesAndUnpartitioned(t *testing.T) {
	const mb = 1024 * 1024
	records := []FileRecord{
		{SizeBytes: 50 * mb, RecordCount: 10, Content: "DATA", FileFormat: "PARQUET"},
		{SizeBytes: 1 * mb, RecordCount: 2, Content: "POSITION_DELETES", FileFormat: "PARQUET"},
		{SizeBytes: 2 * mb, RecordCount: 3, Content: "EQUALITY_DELETES", FileFormat: "PARQUET"},
		{SizeBytes: 3 * mb, RecordCount: 1, Content: "2", FileFormat: "ORC"},
	}
	resp := AggregateFromRecords(records, nil, Options{})
	if resp.DeleteFiles != 3 {
		t.Fatalf("delete_files=%d", resp.DeleteFiles)
	}
	if resp.EqualityDeleteFiles != 2 {
		t.Fatalf("equality=%d", resp.EqualityDeleteFiles)
	}
	if resp.PositionDeleteFiles != 1 {
		t.Fatalf("position=%d", resp.PositionDeleteFiles)
	}
	if len(resp.Partitions) != 1 || resp.Partitions[0].PartitionKey != "_unpartitioned" {
		t.Fatalf("partitions=%v", resp.Partitions)
	}
	if len(resp.FileFormats) != 2 {
		t.Fatalf("formats=%v", resp.FileFormats)
	}
}

// Regression, 2026-07-28: a table with 34,176 tiny position-delete files had every one of
// them double-counted as a small DATA file in total_files/small_files_count and each
// partition's files/small tallies (delete files are almost always under the small-file
// threshold). This made the planner select partitions for OPTIMIZE based on delete-file
// counts rather than actual small-data-file counts -- rewrite_data_files (which doesn't
// touch delete files directly) then predictably rewrote nothing, and the table's health
// score never moved despite maintenance jobs reporting success. Delete files must land only
// in DeleteFiles/PositionDeleteFiles/EqualityDeleteFiles, never in TotalFiles or any
// partition's Files/Small.
func TestAggregateFromRecordsDeletesDoNotInflateFileCounts(t *testing.T) {
	const mb = 1024 * 1024
	fields := []protocol.ScanStatsPartitionField{
		{BaseColumn: "event_date", PartitionField: "event_date_day", Transform: "day", ColType: "date"},
	}
	records := []FileRecord{
		{SizeBytes: 200 * mb, RecordCount: 100, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2026-06-29"}},
	}
	// 50 tiny position-delete files all landing in the same partition as the one real data file.
	for i := 0; i < 50; i++ {
		records = append(records, FileRecord{
			SizeBytes: 4 * 1024, RecordCount: 5, Content: "POSITION_DELETES", FileFormat: "PARQUET",
			Partition: map[string]any{"event_date_day": "2026-06-29"},
		})
	}

	resp := AggregateFromRecords(records, fields, Options{})
	if !resp.OK {
		t.Fatalf("expected ok, got error=%v", resp.Error)
	}
	if resp.TotalFiles != 1 {
		t.Fatalf("total_files=%d, want 1 (delete files must not count as data files)", resp.TotalFiles)
	}
	if resp.SmallFilesCount != 0 {
		t.Fatalf("small_files_count=%d, want 0 (the one data file is 200MB, not small)", resp.SmallFilesCount)
	}
	if resp.TotalRecords != 100 {
		t.Fatalf("total_records=%d, want 100 (delete-entry row counts are deletions, not data rows)", resp.TotalRecords)
	}
	if resp.PositionDeleteFiles != 50 {
		t.Fatalf("position_delete_files=%d, want 50", resp.PositionDeleteFiles)
	}
	if len(resp.Partitions) != 1 {
		t.Fatalf("partitions=%d, want 1", len(resp.Partitions))
	}
	p := resp.Partitions[0]
	if p.Files != 1 {
		t.Fatalf("partition files=%d, want 1 (50 delete files must not inflate this)", p.Files)
	}
	if p.Small != 0 {
		t.Fatalf("partition small=%d, want 0", p.Small)
	}
}

// AutoComp's file-count reduction counts every data file below the rewrite target, including
// mid-size files the 89.6 MB small-file threshold ignores; delete files never count.
func TestAggregateFromRecordsCountsFilesBelowTarget(t *testing.T) {
	const mb = 1024 * 1024
	records := []FileRecord{
		{SizeBytes: 10 * mb, Content: "DATA"},
		{SizeBytes: 120 * mb, Content: "DATA"},
		{SizeBytes: 300 * mb, Content: "DATA"},
		{SizeBytes: 4 * 1024, Content: "POSITION_DELETES"},
	}

	resp := AggregateFromRecords(records, nil, Options{})
	if resp.ReductionTargetBytes != 256*mb {
		t.Fatalf("reduction_target_bytes=%d, want 256 MiB default", resp.ReductionTargetBytes)
	}
	if resp.FilesBelowTarget != 2 || resp.BytesBelowTarget != 130*mb {
		t.Fatalf("below target = %d files / %d bytes, want 2 / %d", resp.FilesBelowTarget, resp.BytesBelowTarget, 130*mb)
	}

	resp = AggregateFromRecords(records, nil, Options{TargetFileBytes: 512 * mb})
	if resp.FilesBelowTarget != 3 {
		t.Fatalf("files_below_target=%d with a 512 MiB table target, want 3", resp.FilesBelowTarget)
	}
}

// Regression, 2026-07-28: convert_deletes' partition-skew scoping (planner.py's
// _skewed_partition_predicates) picks partitions by small-DATA-file count. On a table where
// an earlier optimize pass already compacted the delete-heavy partitions' data files (leaving
// them with only a handful of large files), those partitions no longer look "skewed" by data-
// file count, so convert_deletes silently skipped exactly the partitions holding the delete
// files -- confirmed live, a table's 34,176 position-delete files sat in 4 partitions with
// only 5-7 data files each after an earlier optimize round, and three consecutive
// convert_deletes runs never touched them. DeleteFiles must be tracked per partition,
// independent of Files/Small, so scoping can be done by delete-file concentration instead.
func TestAggregateFromRecordsTracksDeleteFilesPerPartition(t *testing.T) {
	const mb = 1024 * 1024
	fields := []protocol.ScanStatsPartitionField{
		{BaseColumn: "event_date", PartitionField: "event_date_day", Transform: "day", ColType: "date"},
	}
	records := []FileRecord{
		// Partition A: a handful of well-sized data files, but a pile of stale position deletes.
		{SizeBytes: 200 * mb, RecordCount: 100, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2025-11-08"}},
		{SizeBytes: 200 * mb, RecordCount: 100, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2025-11-08"}},
		// Partition B: no deletes at all.
		{SizeBytes: 200 * mb, RecordCount: 100, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"event_date_day": "2025-11-09"}},
	}
	for i := 0; i < 5; i++ {
		records = append(records, FileRecord{
			SizeBytes: 4 * 1024, RecordCount: 3, Content: "POSITION_DELETES", FileFormat: "PARQUET",
			Partition: map[string]any{"event_date_day": "2025-11-08"},
		})
	}

	resp := AggregateFromRecords(records, fields, Options{})
	if !resp.OK {
		t.Fatalf("expected ok, got error=%v", resp.Error)
	}
	byKey := map[string]int{}
	for _, p := range resp.Partitions {
		byKey[p.PartitionValue] = p.DeleteFiles
	}
	if byKey["2025-11-08"] != 5 {
		t.Fatalf("partition 2025-11-08 delete_files=%d, want 5", byKey["2025-11-08"])
	}
	if byKey["2025-11-09"] != 0 {
		t.Fatalf("partition 2025-11-09 delete_files=%d, want 0", byKey["2025-11-09"])
	}
	// Delete files must not have inflated the data-file counts (already covered by
	// TestAggregateFromRecordsDeletesDoNotInflateFileCounts, re-asserted here for this
	// specific mixed-partition shape).
	partA := resp.Partitions[0]
	if partA.PartitionValue != "2025-11-08" {
		for _, p := range resp.Partitions {
			if p.PartitionValue == "2025-11-08" {
				partA = p
				break
			}
		}
	}
	if partA.Files != 2 || partA.Small != 0 {
		t.Fatalf("partition 2025-11-08 files=%d small=%d, want files=2 small=0", partA.Files, partA.Small)
	}
}

func TestAggregateFromRecordsEmpty(t *testing.T) {
	resp := AggregateFromRecords(nil, nil, Options{})
	if !resp.OK || resp.TotalFiles != 0 || len(resp.Partitions) != 0 {
		t.Fatalf("unexpected empty response: %+v", resp)
	}
}

func TestAggregateFromFixturePreservesTableProperties(t *testing.T) {
	gcEnabled := false
	req := &protocol.AggregateFixtureRequest{
		SchemaName: "demo",
		TableName:  "events",
		IcebergProperties: map[string]string{
			"gc.enabled":                      "false",
			"write.parquet.compression-codec": "zstd",
			"write.target-file-size-bytes":    "268435456",
		},
		GCEnabled: &gcEnabled,
	}

	resp := AggregateFromFixture(req)

	if got := resp.IcebergProperties["write.parquet.compression-codec"]; got != "zstd" {
		t.Fatalf("compression codec=%q, want zstd", got)
	}
	if resp.GCEnabled == nil || *resp.GCEnabled {
		t.Fatalf("gc_enabled=%v, want false", resp.GCEnabled)
	}
}

func TestParseBoolProperty(t *testing.T) {
	for _, test := range []struct {
		value string
		want  *bool
	}{
		{value: "yes", want: boolPointer(true)},
		{value: " 0 ", want: boolPointer(false)},
		{value: "invalid", want: nil},
	} {
		got := parseBoolProperty(test.value)
		if got == nil && test.want == nil {
			continue
		}
		if got == nil || test.want == nil || *got != *test.want {
			t.Fatalf("parseBoolProperty(%q)=%v, want %v", test.value, got, test.want)
		}
	}
}

func boolPointer(value bool) *bool {
	return &value
}

func TestAggregateCapsPartitions(t *testing.T) {
	const mb = 1024 * 1024
	fields := []protocol.ScanStatsPartitionField{
		{PartitionField: "p", Transform: "identity", BaseColumn: "p"},
	}
	var records []FileRecord
	for i := 0; i < 20; i++ {
		records = append(records, FileRecord{
			SizeBytes:   int64(i+1) * mb,
			RecordCount: 1,
			Content:     "DATA",
			FileFormat:  "PARQUET",
			Partition:   map[string]any{"p": i},
		})
		// Extra small files on higher indexes so they rank first when capped.
		if i >= 15 {
			records = append(records,
				FileRecord{SizeBytes: mb, RecordCount: 1, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"p": i}},
				FileRecord{SizeBytes: mb, RecordCount: 1, Content: "DATA", FileFormat: "PARQUET", Partition: map[string]any{"p": i}},
			)
		}
	}
	resp := AggregateFromRecords(records, fields, Options{MaxPartitions: 5})
	if len(resp.Partitions) != 5 {
		t.Fatalf("capped partitions=%d", len(resp.Partitions))
	}
}

func TestReadDataFileMapsRecoversFromNullValueCountsPanic(t *testing.T) {
	nulls, vals, values, note := readDataFileMaps(
		func() map[int]int64 {
			panic("interface conversion: interface {} is nil, not int32")
		},
		func() map[int]int64 {
			t.Fatal("ValueCounts should not run after NullValueCounts panic")
			return nil
		},
		func() map[string]any {
			t.Fatal("Partition should not run after NullValueCounts panic")
			return nil
		},
	)
	if nulls != nil || vals != nil {
		t.Fatalf("expected nil column maps after panic, got nulls=%v vals=%v", nulls, vals)
	}
	if len(values) != 0 {
		t.Fatalf("expected empty partition values (_unpartitioned), got %v", values)
	}
	if note == "" || !strings.Contains(note, "iceberg-go map init panic") || !strings.Contains(note, "nil, not int32") {
		t.Fatalf("expected degraded-stats note, got %q", note)
	}
	if partitionKey(values) != "_unpartitioned" {
		t.Fatalf("partition key=%q", partitionKey(values))
	}
}

func TestReadDataFileMapsPassesThrough(t *testing.T) {
	wantNulls := map[int]int64{1: 2}
	wantVals := map[int]int64{1: 10}
	wantPart := map[string]any{"d": int32(20000)}
	nulls, vals, values, note := readDataFileMaps(
		func() map[int]int64 { return wantNulls },
		func() map[int]int64 { return wantVals },
		func() map[string]any { return wantPart },
	)
	if note != "" {
		t.Fatalf("unexpected note: %q", note)
	}
	if nulls[1] != 2 || vals[1] != 10 || values["d"] != int32(20000) {
		t.Fatalf("passthrough mismatch: nulls=%v vals=%v values=%v", nulls, vals, values)
	}
}

func TestNoteManifestErrorDedupes(t *testing.T) {
	acc := newAccumulator(nil, Options{}.withDefaults())
	acc.noteManifestError("same")
	acc.noteManifestError("same")
	acc.noteManifestError("other")
	if len(acc.manifestErrors) != 2 {
		t.Fatalf("manifestErrors=%v", acc.manifestErrors)
	}
}
