package scanstats

import (
	"strings"
	"testing"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

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

func TestAggregateFromRecordsEmpty(t *testing.T) {
	resp := AggregateFromRecords(nil, nil, Options{})
	if !resp.OK || resp.TotalFiles != 0 || len(resp.Partitions) != 0 {
		t.Fatalf("unexpected empty response: %+v", resp)
	}
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
