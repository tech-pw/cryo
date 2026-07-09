package ops

import (
	"fmt"
	"testing"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

func TestScanConcurrencyFromParams(t *testing.T) {
	t.Parallel()
	if got := scanConcurrencyFromParams(nil); got != defaultScanConcurrency {
		t.Fatalf("nil params: got %d want %d", got, defaultScanConcurrency)
	}
	if got := scanConcurrencyFromParams([]byte(`{"scan_concurrency": 2}`)); got != 2 {
		t.Fatalf("explicit: got %d want 2", got)
	}
	if got := scanConcurrencyFromParams([]byte(`{"scan_concurrency": 0}`)); got != defaultScanConcurrency {
		t.Fatalf("zero should fall back: got %d", got)
	}
}

func TestMaxFilesPerGroupFromParams(t *testing.T) {
	t.Parallel()
	if got := maxFilesPerGroupFromParams(nil); got != defaultMaxFilesPerGroup {
		t.Fatalf("nil params: got %d want %d", got, defaultMaxFilesPerGroup)
	}
	if got := maxFilesPerGroupFromParams([]byte(`{"max_files_per_group": 4}`)); got != 4 {
		t.Fatalf("explicit: got %d want 4", got)
	}
}

func TestMaxBytesPerGroupFromParams(t *testing.T) {
	t.Parallel()
	if got := maxBytesPerGroupFromParams(nil); got != defaultTargetFileSizeBytes {
		t.Fatalf("nil params: got %d want target default %d", got, defaultTargetFileSizeBytes)
	}
	if got := maxBytesPerGroupFromParams([]byte(`{"target_file_size_bytes": 67108864}`)); got != 67108864 {
		t.Fatalf("should follow target when max_bytes unset: got %d", got)
	}
	if got := maxBytesPerGroupFromParams([]byte(`{"max_bytes_per_group": 1048576}`)); got != 1048576 {
		t.Fatalf("explicit: got %d want 1048576", got)
	}
	if got := maxBytesPerGroupFromParams([]byte(`{"max_bytes_per_group": 0, "target_file_size_bytes": 67108864}`)); got != 67108864 {
		t.Fatalf("zero max_bytes should fall back to target: got %d", got)
	}
}

func TestMinInputFilesFromParams(t *testing.T) {
	t.Parallel()
	if got := minInputFilesFromParams(nil); got != defaultMinInputFiles {
		t.Fatalf("nil params: got %d want %d", got, defaultMinInputFiles)
	}
	if got := minInputFilesFromParams([]byte(`{"min_input_files": 5}`)); got != 5 {
		t.Fatalf("explicit: got %d want 5", got)
	}
	if got := minInputFilesFromParams([]byte(`{"min_input_files": 0}`)); got != defaultMinInputFiles {
		t.Fatalf("zero should fall back: got %d", got)
	}
}

func makeTask(t *testing.T, path string, size int64) table.FileScanTask {
	t.Helper()
	builder, err := iceberg.NewDataFileBuilder(
		*iceberg.UnpartitionedSpec,
		iceberg.EntryContentData,
		path,
		iceberg.ParquetFile,
		nil,
		nil,
		nil,
		1,
		size,
	)
	if err != nil {
		t.Fatalf("build data file: %v", err)
	}
	return table.FileScanTask{File: builder.Build()}
}

func TestSplitOversizedGroupsByCount(t *testing.T) {
	t.Parallel()
	group := table.CompactionTaskGroup{
		PartitionKey: "p=1",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/a.parquet", 10),
			makeTask(t, "s3://b/b.parquet", 20),
			makeTask(t, "s3://b/c.parquet", 30),
			makeTask(t, "s3://b/d.parquet", 40),
			makeTask(t, "s3://b/e.parquet", 50),
		},
		TotalSizeBytes: 150,
	}

	// File-count split with a high byte cap so only the file limit applies.
	split := splitOversizedGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 2,
		maxBytes: 1 << 30,
	})
	if len(split) != 3 {
		t.Fatalf("expected 3 groups, got %d", len(split))
	}
	if len(split[0].Tasks) != 2 || len(split[1].Tasks) != 2 || len(split[2].Tasks) != 1 {
		t.Fatalf("unexpected sizes: %d %d %d", len(split[0].Tasks), len(split[1].Tasks), len(split[2].Tasks))
	}
	if split[0].TotalSizeBytes != 30 || split[1].TotalSizeBytes != 70 || split[2].TotalSizeBytes != 50 {
		t.Fatalf("unexpected byte totals: %d %d %d", split[0].TotalSizeBytes, split[1].TotalSizeBytes, split[2].TotalSizeBytes)
	}
	for _, g := range split {
		if g.PartitionKey != "p=1" {
			t.Fatalf("partition key changed: %q", g.PartitionKey)
		}
	}

	unchanged := splitOversizedGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: 1 << 30,
	})
	if len(unchanged) != 1 {
		t.Fatalf("under-limit group should stay intact, got %d", len(unchanged))
	}
}

func TestSplitOversizedGroupsByBytes(t *testing.T) {
	t.Parallel()
	// Tight memory cap (32 MiB) with ~20 MiB files: split yields pairs that still
	// fit under the cap (2×20=40 would exceed → flush after first? wait: 20+20=40>32
	// so each file alone). Split may produce 1-file groups; finalize drops them.
	group := table.CompactionTaskGroup{
		PartitionKey: "ingest_date=2025-02-13",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/a.parquet", 20*1024*1024),
			makeTask(t, "s3://b/b.parquet", 20*1024*1024),
			makeTask(t, "s3://b/c.parquet", 20*1024*1024),
			makeTask(t, "s3://b/d.parquet", 20*1024*1024),
		},
		TotalSizeBytes: 80 * 1024 * 1024,
	}

	split := splitOversizedGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: 32 * 1024 * 1024,
	})
	if len(split) != 4 {
		t.Fatalf("expected 4 single-file groups under 32MiB cap, got %d", len(split))
	}
	for i, g := range split {
		if len(g.Tasks) != 1 {
			t.Fatalf("group %d: want 1 file, got %d", i, len(g.Tasks))
		}
		if g.TotalSizeBytes != 20*1024*1024 {
			t.Fatalf("group %d: unexpected bytes %d", i, g.TotalSizeBytes)
		}
		if g.PartitionKey != "ingest_date=2025-02-13" {
			t.Fatalf("partition key changed: %q", g.PartitionKey)
		}
	}

	// Spark/Iceberg: do not rewrite 1→1 after a memory split that cannot keep ≥2 files.
	final := finalizeRewriteGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: 32 * 1024 * 1024,
	}, 2)
	if len(final) != 0 {
		t.Fatalf("expected all 1-file groups dropped at min_input_files=2, got %d", len(final))
	}
}

func TestFinalizeRewriteGroupsPacksTowardTarget(t *testing.T) {
	t.Parallel()
	// Default-aligned: max_bytes == target (256 MiB). Five ~50 MiB files stay one
	// bin-pack-sized group (≥2 files) — the GeoIP shape that previously 1→1'd under 32 MiB.
	const fileBytes = 50 * 1024 * 1024
	group := table.CompactionTaskGroup{
		PartitionKey: "ingest_date=2025-02-13",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/a.parquet", fileBytes),
			makeTask(t, "s3://b/b.parquet", fileBytes),
			makeTask(t, "s3://b/c.parquet", fileBytes),
			makeTask(t, "s3://b/d.parquet", fileBytes),
			makeTask(t, "s3://b/e.parquet", fileBytes),
		},
		TotalSizeBytes: 5 * fileBytes,
	}
	final := finalizeRewriteGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: defaultTargetFileSizeBytes, // 256 MiB
	}, 2)
	if len(final) != 1 {
		t.Fatalf("expected 1 group packed toward target, got %d", len(final))
	}
	if len(final[0].Tasks) != 5 {
		t.Fatalf("expected 5 input files kept together, got %d", len(final[0].Tasks))
	}
}

func TestFinalizeRewriteGroupsKeepsMultiFileChunksUnderTightCap(t *testing.T) {
	t.Parallel()
	// 10 MiB files + 32 MiB cap → groups of 3 files (30 MiB); all keep min_input_files=2.
	const fileBytes = 10 * 1024 * 1024
	group := table.CompactionTaskGroup{
		PartitionKey: "p=1",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/a.parquet", fileBytes),
			makeTask(t, "s3://b/b.parquet", fileBytes),
			makeTask(t, "s3://b/c.parquet", fileBytes),
			makeTask(t, "s3://b/d.parquet", fileBytes),
			makeTask(t, "s3://b/e.parquet", fileBytes),
			makeTask(t, "s3://b/f.parquet", fileBytes),
		},
		TotalSizeBytes: 6 * fileBytes,
	}
	final := finalizeRewriteGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: 32 * 1024 * 1024,
	}, 2)
	if len(final) != 2 {
		t.Fatalf("expected 2 groups of 3 files, got %d", len(final))
	}
	for i, g := range final {
		if len(g.Tasks) != 3 {
			t.Fatalf("group %d: want 3 files, got %d", i, len(g.Tasks))
		}
	}
}

func TestFilterGroupsByMinInputFiles(t *testing.T) {
	t.Parallel()
	groups := []table.CompactionTaskGroup{
		{PartitionKey: "p=1", Tasks: []table.FileScanTask{makeTask(t, "s3://b/a.parquet", 10)}},
		{PartitionKey: "p=1", Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/b.parquet", 10),
			makeTask(t, "s3://b/c.parquet", 10),
		}},
	}
	kept := filterGroupsByMinInputFiles(groups, 2)
	if len(kept) != 1 || len(kept[0].Tasks) != 2 {
		t.Fatalf("expected only the 2-file group, got %+v", kept)
	}
}

func TestSplitOversizedGroupsSingleFileLargerThanCap(t *testing.T) {
	t.Parallel()
	group := table.CompactionTaskGroup{
		PartitionKey: "p=1",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/huge.parquet", 100*1024*1024),
		},
		TotalSizeBytes: 100 * 1024 * 1024,
	}
	split := splitOversizedGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 8,
		maxBytes: 32 * 1024 * 1024,
	})
	if len(split) != 1 {
		t.Fatalf("single oversized file must stay one group, got %d", len(split))
	}
	if len(split[0].Tasks) != 1 || split[0].TotalSizeBytes != 100*1024*1024 {
		t.Fatalf("unexpected group: files=%d bytes=%d", len(split[0].Tasks), split[0].TotalSizeBytes)
	}
}

func TestSplitOversizedGroupsBothCapsInteract(t *testing.T) {
	t.Parallel()
	// Small files: byte cap allows many, but file-count cap of 2 forces splits.
	group := table.CompactionTaskGroup{
		PartitionKey: "p=1",
		Tasks: []table.FileScanTask{
			makeTask(t, "s3://b/a.parquet", 1*1024*1024),
			makeTask(t, "s3://b/b.parquet", 1*1024*1024),
			makeTask(t, "s3://b/c.parquet", 1*1024*1024),
			makeTask(t, "s3://b/d.parquet", 1*1024*1024),
		},
		TotalSizeBytes: 4 * 1024 * 1024,
	}
	split := splitOversizedGroups([]table.CompactionTaskGroup{group}, groupSplitLimits{
		maxFiles: 2,
		maxBytes: 32 * 1024 * 1024,
	})
	if len(split) != 2 {
		t.Fatalf("expected 2 groups from file-count cap, got %d", len(split))
	}
	if len(split[0].Tasks) != 2 || len(split[1].Tasks) != 2 {
		t.Fatalf("unexpected sizes: %d %d", len(split[0].Tasks), len(split[1].Tasks))
	}
}

func TestRewriteGroupOptionsIncludesScanConcurrency(t *testing.T) {
	t.Parallel()
	opts := rewriteGroupOptions(256*1024*1024, []byte(`{"scan_concurrency": 1}`))
	if len(opts) != 2 {
		t.Fatalf("expected target-size + scan-concurrency options, got %d", len(opts))
	}
	_ = fmt.Sprintf("%d", len(opts))
}
