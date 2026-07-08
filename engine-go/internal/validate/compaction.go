package validate

import (
	"context"
	"fmt"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

// MetadataSnapshot captures table metadata ids for pre/post commit checks.
type MetadataSnapshot struct {
	SchemaID        int
	PartitionSpecID int
	SortOrderID     int
}

// CaptureMetadataSnapshot records schema/spec/sort-order ids before compaction.
func CaptureMetadataSnapshot(tbl *table.Table) MetadataSnapshot {
	meta := tbl.Metadata()
	snapshot := MetadataSnapshot{
		SchemaID:        meta.CurrentSchema().ID,
		PartitionSpecID: meta.DefaultPartitionSpec(),
		SortOrderID:     meta.DefaultSortOrder(),
	}
	return snapshot
}

// AssertMetadataStable ensures schema, default partition spec, and sort order are unchanged.
func AssertMetadataStable(before MetadataSnapshot, after *table.Table) error {
	current := CaptureMetadataSnapshot(after)
	if current.SchemaID != before.SchemaID {
		return fmt.Errorf("schema id changed during compaction: %d -> %d", before.SchemaID, current.SchemaID)
	}
	if current.PartitionSpecID != before.PartitionSpecID {
		return fmt.Errorf(
			"default partition spec id changed during compaction: %d -> %d",
			before.PartitionSpecID, current.PartitionSpecID,
		)
	}
	if current.SortOrderID != before.SortOrderID {
		return fmt.Errorf(
			"sort order id changed during compaction: %d -> %d",
			before.SortOrderID, current.SortOrderID,
		)
	}
	return nil
}

// AssertRowCountInvariant verifies scoped row counts are unchanged (COW guard rail).
func AssertRowCountInvariant(beforeRows, afterRows int64) error {
	if beforeRows != afterRows {
		return fmt.Errorf("row count invariant violated: %d -> %d", beforeRows, afterRows)
	}
	return nil
}

// AssertOutputFilesExist checks that rewritten output files are reachable before commit.
func AssertOutputFilesExist(ctx context.Context, tbl *table.Table, paths []string) error {
	if len(paths) == 0 {
		return nil
	}
	fs, err := tbl.FS(ctx)
	if err != nil {
		return fmt.Errorf("open table filesystem: %w", err)
	}
	for _, path := range paths {
		file, err := fs.Open(path)
		if err != nil {
			return fmt.Errorf("output file missing before commit: %s: %w", path, err)
		}
		_ = file.Close()
	}
	return nil
}

// CustomSnapshotProps copies non-system summary properties from the current snapshot.
func CustomSnapshotProps(tbl *table.Table) iceberg.Properties {
	snap := tbl.CurrentSnapshot()
	if snap == nil || snap.Summary == nil || len(snap.Summary.Properties) == 0 {
		return nil
	}
	props := iceberg.Properties{}
	for key, value := range snap.Summary.Properties {
		if isSystemSummaryKey(key) {
			continue
		}
		props[key] = value
	}
	if len(props) == 0 {
		return nil
	}
	return props
}

func isSystemSummaryKey(key string) bool {
	switch key {
	case "operation",
		"added-data-files", "added-delete-files", "added-equality-deletes", "added-files-size",
		"added-position-deletes", "added-position-delete-files", "added-records", "added-equality-delete-files",
		"deleted-data-files", "deleted-records", "removed-delete-files", "removed-equality-deletes",
		"removed-equality-delete-files", "removed-files-size", "removed-position-deletes",
		"removed-position-delete-files", "total-equality-deletes", "total-position-deletes",
		"total-data-files", "total-delete-files", "total-records", "total-files-size",
		"changed-partition-count":
		return true
	default:
		return len(key) >= len("partitions.") && key[:len("partitions.")] == "partitions."
	}
}
