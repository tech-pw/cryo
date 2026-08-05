package protocol

// ScanStatsRequest is the stdin JSON for `cryo-engine-go scan-stats`.
// Catalog/S3 fields mirror ExecuteRequest so we can reuse catalog.LoadTable helpers.
type ScanStatsRequest struct {
	TableRef          string            `json:"table_ref"`
	CatalogURI        string            `json:"catalog_uri"`
	CatalogType       string            `json:"catalog_type,omitempty"`
	Warehouse         string            `json:"warehouse"`
	S3Endpoint        *string           `json:"s3_endpoint,omitempty"`
	S3AccessKey       *string           `json:"s3_access_key,omitempty"`
	S3SecretKey       *string           `json:"s3_secret_key,omitempty"`
	CatalogProperties map[string]string `json:"catalog_properties,omitempty"`
	// SmallFileMB is the compaction small-file threshold (default 89.6).
	SmallFileMB float64 `json:"small_file_mb,omitempty"`
	// HealthSmallFileBytes defaults to 8 MiB when zero.
	HealthSmallFileBytes int64 `json:"health_small_file_bytes,omitempty"`
	// MaxPartitions caps partitions_to_rewrite / returned partition rows (0 = 5000).
	MaxPartitions int `json:"max_partitions,omitempty"`
	// MaxFileSamples caps deterministic file-bound evidence retained for offline WAIR.
	MaxFileSamples int `json:"max_file_samples,omitempty"`
}

// ToExecuteRequest adapts scan-stats catalog fields for catalog.LoadTable.
func (r *ScanStatsRequest) ToExecuteRequest() *ExecuteRequest {
	return &ExecuteRequest{
		TableRef:          r.TableRef,
		CatalogURI:        r.CatalogURI,
		CatalogType:       r.CatalogType,
		Warehouse:         r.Warehouse,
		S3Endpoint:        r.S3Endpoint,
		S3AccessKey:       r.S3AccessKey,
		S3SecretKey:       r.S3SecretKey,
		CatalogProperties: r.CatalogProperties,
	}
}

// ScanStatsPartitionField mirrors analyze.py partition_fields entries.
type ScanStatsPartitionField struct {
	BaseColumn     string `json:"base_column"`
	PartitionField string `json:"partition_field"`
	Transform      string `json:"transform"`
	TransformParam *int   `json:"transform_param"`
	ColType        string `json:"col_type"`
	// FieldID is the Iceberg partition field id (used by Go to map Partition()).
	FieldID int `json:"field_id,omitempty"`
}

// ScanStatsPartitionAccum is one partition aggregate from the current snapshot.
type ScanStatsPartitionAccum struct {
	PartitionKey    string         `json:"partition_key"`
	PartitionValue  string         `json:"partition_value"`
	PartitionValues map[string]any `json:"partition_values"`
	PartitionSpecID *int32         `json:"partition_spec_id,omitempty"`
	Files           int            `json:"files"`
	Small           int            `json:"small"`
	SizeBytes       int64          `json:"size_bytes"`
	// DeleteFiles is position+equality delete files whose partition value maps to this
	// partition -- tracked separately from Files/Small (which are data-file-only as of
	// 2026-07-28) so callers can scope delete-cleanup work by where deletes are actually
	// concentrated, independent of where small data files are concentrated.
	DeleteFiles int `json:"delete_files"`
}

// ScanStatsColumnQuality mirrors analyze.py raw.column_quality entries.
type ScanStatsColumnQuality struct {
	FieldID    int     `json:"field_id"`
	ColumnName string  `json:"column_name"`
	ColumnType string  `json:"column_type,omitempty"`
	NullCount  int64   `json:"null_count"`
	ValueCount int64   `json:"value_count"`
	NullRate   float64 `json:"null_rate"`
	LowerBound any     `json:"lower_bound,omitempty"`
	UpperBound any     `json:"upper_bound,omitempty"`
}

type ScanStatsFileBoundSample struct {
	FilePath    string         `json:"file_path"`
	SizeBytes   int64          `json:"size_bytes"`
	LowerBounds map[string]any `json:"lower_bounds"`
	UpperBounds map[string]any `json:"upper_bounds"`
}

// ScanStatsResponse is stdout JSON for `cryo-engine-go scan-stats`.
// Python maps this into TableAnalysis (health/cost still computed in Python).
type ScanStatsResponse struct {
	OK    bool    `json:"ok"`
	Error *string `json:"error,omitempty"`

	SchemaName string `json:"schema_name,omitempty"`
	TableName  string `json:"table_name,omitempty"`

	TotalFiles          int64 `json:"total_files"`
	TotalRecords        int64 `json:"total_records"`
	TotalSizeBytes      int64 `json:"total_size_bytes"`
	HealthSmallFiles    int64 `json:"health_small_files"`
	SmallFilesCount     int64 `json:"small_files_count"`
	DeleteFiles         int64 `json:"delete_files"`
	EqualityDeleteFiles int64 `json:"equality_delete_files"`
	PositionDeleteFiles int64 `json:"position_delete_files"`
	DeleteBytes         int64 `json:"delete_bytes"`
	ManifestCount       int   `json:"manifest_count"`
	ManifestTotalBytes  int64 `json:"manifest_total_bytes"`
	TotalSnapshots      int   `json:"total_snapshots"`
	// TotalPartitions is the full distinct-partition count before MaxPartitions truncation.
	TotalPartitions int `json:"total_partitions"`
	// Full-map partition distribution (computed before MaxPartitions truncation).
	MaxFilesPerPartition int     `json:"max_files_per_partition"`
	AvgFilesPerPartition float64 `json:"avg_files_per_partition"`
	PartitionSkewRatio   float64 `json:"partition_skew_ratio"`

	FirstSnapshotAt       *string                   `json:"first_snapshot_at,omitempty"`
	LastSnapshotAt        *string                   `json:"last_snapshot_committed_at,omitempty"`
	CurrentSnapshotID     *int64                    `json:"current_snapshot_id,omitempty"`
	FileFormats           []string                  `json:"file_formats"`
	PartitionFields       []ScanStatsPartitionField `json:"partition_fields"`
	Partitions            []ScanStatsPartitionAccum `json:"partitions"`
	WriteDistributionMode *string                   `json:"write_distribution_mode,omitempty"`
	HasSortOrder          *bool                     `json:"has_sort_order,omitempty"`
	TargetFileSizeBytes   *int64                    `json:"target_file_size_bytes,omitempty"`
	IcebergProperties     map[string]string         `json:"iceberg_properties"`
	GCEnabled             *bool                     `json:"gc_enabled"`
	Location              string                    `json:"location,omitempty"`
	FormatVersion         *int                      `json:"format_version,omitempty"`
	ManifestReadErrors    []string                  `json:"manifest_read_errors,omitempty"`

	ColumnQuality          []ScanStatsColumnQuality   `json:"column_quality,omitempty"`
	AvgNullRate            *float64                   `json:"avg_null_rate,omitempty"`
	MaxNullRate            *float64                   `json:"max_null_rate,omitempty"`
	WorstNullColumn        *string                    `json:"worst_null_column,omitempty"`
	WairFileSample         []ScanStatsFileBoundSample `json:"wair_file_sample,omitempty"`
	WairFileSampleComplete bool                       `json:"wair_file_sample_complete"`
}

// AggregateFixtureRequest drives `cryo-engine-go aggregate-fixture` for parity tests
// without needing a live catalog.
type AggregateFixtureRequest struct {
	SchemaName            string                    `json:"schema_name"`
	TableName             string                    `json:"table_name"`
	SmallFileMB           float64                   `json:"small_file_mb,omitempty"`
	HealthSmallFileBytes  int64                     `json:"health_small_file_bytes,omitempty"`
	MaxPartitions         int                       `json:"max_partitions,omitempty"`
	PartitionFields       []ScanStatsPartitionField `json:"partition_fields"`
	Records               []AggregateFixtureRecord  `json:"records"`
	TotalSnapshots        int                       `json:"total_snapshots"`
	FirstSnapshotAt       *string                   `json:"first_snapshot_at,omitempty"`
	LastSnapshotAt        *string                   `json:"last_snapshot_committed_at,omitempty"`
	CurrentSnapshotID     *int64                    `json:"current_snapshot_id,omitempty"`
	ManifestCount         int                       `json:"manifest_count"`
	ManifestTotalBytes    int64                     `json:"manifest_total_bytes"`
	WriteDistributionMode *string                   `json:"write_distribution_mode,omitempty"`
	HasSortOrder          *bool                     `json:"has_sort_order,omitempty"`
	TargetFileSizeBytes   *int64                    `json:"target_file_size_bytes,omitempty"`
	IcebergProperties     map[string]string         `json:"iceberg_properties"`
	GCEnabled             *bool                     `json:"gc_enabled"`
	Location              string                    `json:"location,omitempty"`
	FormatVersion         *int                      `json:"format_version,omitempty"`
	Columns               []AggregateFixtureColumn  `json:"columns,omitempty"`
}

// AggregateFixtureRecord is one synthetic data/delete file.
type AggregateFixtureRecord struct {
	SizeBytes       int64            `json:"size_bytes"`
	RecordCount     int64            `json:"record_count"`
	Content         string           `json:"content"`
	FileFormat      string           `json:"file_format"`
	SpecID          int32            `json:"spec_id"`
	Partition       map[string]any   `json:"partition"`
	NullValueCounts map[string]int64 `json:"null_value_counts,omitempty"` // field_id string → count
	ValueCounts     map[string]int64 `json:"value_counts,omitempty"`
}

// AggregateFixtureColumn names a schema field for column-quality aggregation.
type AggregateFixtureColumn struct {
	FieldID    int    `json:"field_id"`
	ColumnName string `json:"column_name"`
	ColumnType string `json:"column_type"`
}
