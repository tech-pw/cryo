package ops

import (
	"encoding/json"
	"fmt"
	"strconv"
	"unsafe"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

// Tuned defaults used when Python/payload setdefaults them. Not applied unless
// the corresponding param key is present (preserves iceberg-go baseline for
// unconfigured runs / Phase-1 profiling).
const (
	defaultReadBatchSize         = 16_384
	defaultParquetRowGroupLimit  = 65_536
	defaultParquetRowGroupBytes  = 32 * 1024 * 1024 // 32 MiB
	defaultCompressionLevel      = 3
	defaultMetricsMode           = "truncate(16)"
	defaultGroupConcurrency      = 1
	defaultMemoryBudgetBytes     = 3 * 1024 * 1024 * 1024 // 3 GiB
	defaultMemoryExpansionFactor = 96 // dense string tables: calibrated so parallel ≤ baseline RSS
)

// tableLayout mirrors iceberg-go v0.6.0 table.Table field order so we can
// rebuild a Table with overridden metadata while reusing the same CatalogIO
// and FSysF (both unexported on Table). Pinned to v0.6.0 — update if upstream
// reorders fields.
type tableLayout struct {
	identifier       table.Identifier
	metadata         table.Metadata
	metadataLocation string
	cat              table.CatalogIO
	fsF              table.FSysF
}

// cloneTableWithProperties returns a Table that shares catalog/IO with tbl but
// has in-memory metadata properties overridden. The clone is never committed.
func cloneTableWithProperties(tbl *table.Table, overrides iceberg.Properties) (*table.Table, error) {
	if tbl == nil {
		return nil, fmt.Errorf("cloneTableWithProperties: nil table")
	}
	if len(overrides) == 0 {
		return tbl, nil
	}
	builder, err := table.MetadataBuilderFromBase(tbl.Metadata(), tbl.MetadataLocation())
	if err != nil {
		return nil, fmt.Errorf("metadata builder: %w", err)
	}
	if err := builder.SetProperties(overrides); err != nil {
		return nil, fmt.Errorf("set rewrite properties: %w", err)
	}
	meta, err := builder.Build()
	if err != nil {
		return nil, fmt.Errorf("build rewrite metadata: %w", err)
	}
	src := (*tableLayout)(unsafe.Pointer(tbl))
	return table.New(src.identifier, meta, src.metadataLocation, src.fsF, src.cat), nil
}

// rewriteIOProperties builds in-memory property overrides for a rewrite.
// Only keys present in params are applied so an empty payload preserves
// iceberg-go defaults (needed for baseline profiling).
func rewriteIOProperties(raw json.RawMessage) iceberg.Properties {
	props := iceberg.Properties{}
	if len(raw) == 0 {
		return props
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return props
	}
	if _, ok := params["read_batch_size"]; ok {
		batch := intParam(raw, "read_batch_size", defaultReadBatchSize)
		if batch > 0 {
			props[table.ParquetBatchSizeKey] = strconv.Itoa(batch)
		}
	}
	if _, ok := params["parquet_row_group_limit"]; ok {
		rowGroupLimit := intParam(raw, "parquet_row_group_limit", defaultParquetRowGroupLimit)
		if rowGroupLimit > 0 {
			props[table.ParquetRowGroupLimitKey] = strconv.Itoa(rowGroupLimit)
		}
	}
	if _, ok := params["parquet_row_group_bytes"]; ok {
		rowGroupBytes := int64Param(raw, "parquet_row_group_bytes", defaultParquetRowGroupBytes)
		if rowGroupBytes > 0 {
			props[table.ParquetRowGroupSizeBytesKey] = strconv.FormatInt(rowGroupBytes, 10)
		}
	}
	if _, ok := params["compression_level"]; ok {
		compressionLevel := intParam(raw, "compression_level", defaultCompressionLevel)
		if compressionLevel >= 0 {
			props[table.ParquetCompressionLevelKey] = strconv.Itoa(compressionLevel)
		}
	}
	if _, ok := params["metrics_mode"]; ok {
		metricsMode := stringParam(raw, "metrics_mode", defaultMetricsMode)
		if metricsMode != "" {
			props[table.DefaultWriteMetricsModeKey] = metricsMode
		}
	}
	if _, ok := params["compression_codec"]; ok {
		if codec := stringParam(raw, "compression_codec", ""); codec != "" {
			props[table.ParquetCompressionKey] = codec
		}
	}
	return props
}

func groupConcurrencyFromParams(raw json.RawMessage) int {
	n := intParam(raw, "group_concurrency", defaultGroupConcurrency)
	if n < 1 {
		return defaultGroupConcurrency
	}
	return n
}

func memoryBudgetFromParams(raw json.RawMessage) int64 {
	n := int64Param(raw, "compaction_memory_budget_bytes", defaultMemoryBudgetBytes)
	if n < 1 {
		return defaultMemoryBudgetBytes
	}
	return n
}

func memoryExpansionFromParams(raw json.RawMessage) int {
	n := intParam(raw, "memory_expansion_factor", defaultMemoryExpansionFactor)
	if n < 1 {
		return defaultMemoryExpansionFactor
	}
	return n
}

func estimateGroupFootprint(group table.CompactionTaskGroup, expansionFactor int) int64 {
	if expansionFactor < 1 {
		expansionFactor = defaultMemoryExpansionFactor
	}
	compressed := groupCompressedBytes(group)
	if compressed < 1 {
		compressed = 1
	}
	return compressed * int64(expansionFactor)
}
