package ops

import (
	"encoding/json"
	"testing"

	"github.com/apache/iceberg-go/table"
)

func TestGroupConcurrencyFromParams(t *testing.T) {
	t.Parallel()
	if got := groupConcurrencyFromParams(nil); got != defaultGroupConcurrency {
		t.Fatalf("nil: got %d want %d", got, defaultGroupConcurrency)
	}
	if got := groupConcurrencyFromParams([]byte(`{"group_concurrency": 4}`)); got != 4 {
		t.Fatalf("explicit: got %d want 4", got)
	}
	if got := groupConcurrencyFromParams([]byte(`{"group_concurrency": 0}`)); got != defaultGroupConcurrency {
		t.Fatalf("zero should fall back: got %d", got)
	}
}

func TestMemoryBudgetFromParams(t *testing.T) {
	t.Parallel()
	if got := memoryBudgetFromParams(nil); got != defaultMemoryBudgetBytes {
		t.Fatalf("nil: got %d want %d", got, defaultMemoryBudgetBytes)
	}
	if got := memoryBudgetFromParams([]byte(`{"compaction_memory_budget_bytes": 1073741824}`)); got != 1073741824 {
		t.Fatalf("explicit: got %d", got)
	}
}

func TestRewriteIOProperties(t *testing.T) {
	t.Parallel()
	props := rewriteIOProperties(nil)
	if len(props) != 0 {
		t.Fatalf("nil params should not override IO props: %v", props)
	}
	props = rewriteIOProperties([]byte(`{}`))
	if len(props) != 0 {
		t.Fatalf("empty object should not override IO props: %v", props)
	}

	raw, _ := json.Marshal(map[string]any{
		"read_batch_size":         4096,
		"parquet_row_group_limit": 8192,
		"parquet_row_group_bytes": 8 * 1024 * 1024,
		"compression_level":       3,
		"metrics_mode":            "counts",
		"compression_codec":       "zstd",
	})
	props = rewriteIOProperties(raw)
	if props[table.ParquetBatchSizeKey] != "4096" {
		t.Fatalf("override batch: %q", props[table.ParquetBatchSizeKey])
	}
	if props[table.ParquetRowGroupLimitKey] != "8192" {
		t.Fatalf("override row group: %q", props[table.ParquetRowGroupLimitKey])
	}
	if props[table.ParquetCompressionLevelKey] != "3" {
		t.Fatalf("override level: %q", props[table.ParquetCompressionLevelKey])
	}
	if props[table.DefaultWriteMetricsModeKey] != "counts" {
		t.Fatalf("override metrics: %q", props[table.DefaultWriteMetricsModeKey])
	}
	if props[table.ParquetCompressionKey] != "zstd" {
		t.Fatalf("override codec: %q", props[table.ParquetCompressionKey])
	}
}

func TestEstimateGroupFootprint(t *testing.T) {
	t.Parallel()
	group := table.CompactionTaskGroup{TotalSizeBytes: 10 * 1024 * 1024}
	got := estimateGroupFootprint(group, 8)
	want := int64(80 * 1024 * 1024)
	if got != want {
		t.Fatalf("got %d want %d", got, want)
	}
	got64 := estimateGroupFootprint(group, 64)
	if got64 != int64(640*1024*1024) {
		t.Fatalf("64x: got %d", got64)
	}
}

func TestMemoryGateAdmitsUnderBudget(t *testing.T) {
	t.Parallel()
	gate := newMemoryGate(100, 2)
	if err := gate.acquire(t.Context(), 40); err != nil {
		t.Fatalf("first acquire: %v", err)
	}
	if err := gate.acquire(t.Context(), 40); err != nil {
		t.Fatalf("second acquire: %v", err)
	}
	gate.release(40)
	gate.release(40)
}

func TestMemoryGateOversizedRunsAlone(t *testing.T) {
	t.Parallel()
	gate := newMemoryGate(50, 2)
	if err := gate.acquire(t.Context(), 200); err != nil {
		t.Fatalf("oversized alone: %v", err)
	}
	gate.release(200)
}
