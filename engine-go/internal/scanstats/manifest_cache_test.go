package scanstats

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/apache/iceberg-go"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestManifestCacheSaveLoadParityAndLRUEviction(t *testing.T) {
	dir := t.TempDir()
	const mb = 1024 * 1024
	fields := []protocol.ScanStatsPartitionField{
		{BaseColumn: "event_date", PartitionField: "event_date_day", Transform: "day", ColType: "date"},
	}

	low10, err := iceberg.NewLiteral[int64](10).MarshalBinary()
	if err != nil {
		t.Fatalf("marshal low: %v", err)
	}
	high99, err := iceberg.NewLiteral[int64](99).MarshalBinary()
	if err != nil {
		t.Fatalf("marshal high: %v", err)
	}

	size1 := int64(12 * mb)
	size2 := int64(24 * mb)
	sizeDel := int64(1 * mb)
	specID := int32(0)
	snap1 := int64(101)
	entries := []cachedManifestEntry{
		{
			FilePath:        "s3://b/t/data/f1.parquet",
			FileSizeInBytes: &size1,
			RecordCount:     100,
			Content:         "DATA",
			FileFormat:      "PARQUET",
			SpecID:          &specID,
			PartitionValues: map[string]any{"event_date_day": "2026-06-01"},
			NullValueCounts: map[string]int64{"1": 2},
			ValueCounts:     map[string]int64{"1": 100},
			LowerBounds:     map[string]string{"1": base64.StdEncoding.EncodeToString(low10)},
			UpperBounds:     map[string]string{"1": base64.StdEncoding.EncodeToString(high99)},
			SnapshotID:      &snap1,
		},
		{
			FilePath:        "s3://b/t/data/f2.parquet",
			FileSizeInBytes: &size2,
			RecordCount:     200,
			Content:         "DATA",
			FileFormat:      "PARQUET",
			SpecID:          &specID,
			PartitionValues: map[string]any{"event_date_day": "2026-06-01"},
			NullValueCounts: map[string]int64{"1": 0},
			ValueCounts:     map[string]int64{"1": 200},
			LowerBounds:     map[string]string{"1": base64.StdEncoding.EncodeToString(low10)},
			UpperBounds:     map[string]string{"1": base64.StdEncoding.EncodeToString(high99)},
			SnapshotID:      &snap1,
		},
		{
			FilePath:        "s3://b/t/data/del1.parquet",
			FileSizeInBytes: &sizeDel,
			RecordCount:     5,
			Content:         "POSITION_DELETES",
			FileFormat:      "PARQUET",
			SpecID:          &specID,
			PartitionValues: map[string]any{"event_date_day": "2026-06-01"},
			SnapshotID:      &snap1,
		},
	}

	manifestPath := "s3://b/t/metadata/snap-101-m0.avro"
	saveCachedManifest(dir, manifestPath, 10*mb, entries)

	loaded, ok := loadCachedManifest(dir, manifestPath, 10*mb)
	if !ok || len(loaded) != 3 {
		t.Fatalf("expected cache hit of 3 entries, got ok=%v len=%d", ok, len(loaded))
	}

	opts := Options{SmallFileMB: 89.6, SampleColumns: []string{"amount"}}.withDefaults()
	acc := newAccumulator(fields, opts)
	acc.columnDefs = map[int]protocol.AggregateFixtureColumn{
		1: {FieldID: 1, ColumnName: "amount", ColumnType: "long"},
	}
	acc.columnTypes = map[int]iceberg.Type{1: iceberg.PrimitiveTypes.Int64}
	times := map[int64]int64{101: 1780272000000}
	for _, item := range loaded {
		acc.addCachedEntry(item, times)
	}
	resp := acc.finish("demo", "events", &snapshotInfo{totalSnapshots: 1})
	if resp.TotalFiles != 2 || resp.SmallFilesCount != 2 || resp.DeleteFiles != 1 || resp.PositionDeleteFiles != 1 {
		t.Fatalf("unexpected file counts from cached entries: %+v", resp)
	}
	if len(resp.ColumnQuality) != 1 || resp.ColumnQuality[0].LowerBound != int64(10) || resp.ColumnQuality[0].UpperBound != int64(99) {
		t.Fatalf("unexpected column quality from cached entries: %+v", resp.ColumnQuality)
	}
	if len(resp.WairFileSample) != 2 {
		t.Fatalf("expected 2 WAIR samples from cached entries, got %d", len(resp.WairFileSample))
	}

	// Verify LRU size eviction removes oldest manifest summary when maxBytes is exceeded.
	oldPath := manifestCacheFilePath(dir, manifestPath)
	past := time.Now().Add(-10 * time.Minute)
	if err := os.Chtimes(oldPath, past, past); err != nil {
		t.Fatalf("chtimes: %v", err)
	}
	info, err := os.Stat(oldPath)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	// Cap fits ~1 entry file, so writing a second manifest evicts the older one.
	saveCachedManifest(dir, "s3://b/t/metadata/snap-102-m1.avro", info.Size()+64, entries)
	if _, stillThere := loadCachedManifest(dir, manifestPath, 10*mb); stillThere {
		t.Fatalf("expected older manifest %s to be evicted by LRU", filepath.Base(oldPath))
	}
	if _, newestThere := loadCachedManifest(dir, "s3://b/t/metadata/snap-102-m1.avro", 10*mb); !newestThere {
		t.Fatalf("expected newer manifest to remain cached")
	}
}
