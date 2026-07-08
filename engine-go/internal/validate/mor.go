package validate

import (
	"fmt"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

// HasDeleteFilesInScope reports whether any scoped scan task carries delete files.
func HasDeleteFilesInScope(tasks []table.FileScanTask) bool {
	for _, task := range tasks {
		if len(task.DeleteFiles) > 0 ||
			len(task.EqualityDeleteFiles) > 0 ||
			len(task.DeletionVectorFiles) > 0 {
			return true
		}
	}
	return false
}

// AssertPartitionedDeletionVectorsUnsupported gates v3 partitioned deletion vectors (#1135).
func AssertPartitionedDeletionVectorsUnsupported(tbl *table.Table, tasks []table.FileScanTask) error {
	if tbl.Metadata().Version() < 3 {
		return nil
	}
	if tbl.Metadata().PartitionSpec().IsUnpartitioned() {
		return nil
	}
	for _, task := range tasks {
		if len(task.DeletionVectorFiles) > 0 {
			return fmt.Errorf(
				"v3 partitioned deletion vectors not supported in pinned iceberg-go (#1135); route to Trino/Spark",
			)
		}
	}
	return nil
}

// AssertDeleteFileInvariant enforces invariant #7: position deletes staged for removal
// must reference only data files in the rewrite scope.
func AssertDeleteFileInvariant(
	rewrittenPaths map[string]struct{},
	tasks []table.FileScanTask,
) error {
	if len(rewrittenPaths) == 0 {
		return nil
	}

	safePosDeletes := table.CollectSafePositionDeletes(tasks)
	for _, df := range safePosDeletes {
		ref := df.ReferencedDataFile()
		if ref != nil && *ref != "" {
			if _, ok := rewrittenPaths[*ref]; !ok {
				return fmt.Errorf(
					"delete file invariant #7 violated: position delete %s references %s outside rewrite scope",
					df.FilePath(),
					*ref,
				)
			}
		}
	}

	for _, task := range tasks {
		for _, dv := range task.DeletionVectorFiles {
			ref := dv.ReferencedDataFile()
			if ref == nil || *ref == "" {
				continue
			}
			if _, rewriting := rewrittenPaths[*ref]; rewriting {
				return fmt.Errorf(
					"delete file invariant #7 violated: deletion vector %s references rewritten file %s outside supported scope",
					dv.FilePath(),
					*ref,
				)
			}
		}
	}

	return assertNoOrphanedPositionDeletes(rewrittenPaths, tasks, safePosDeletes)
}

func assertNoOrphanedPositionDeletes(
	rewrittenPaths map[string]struct{},
	tasks []table.FileScanTask,
	safePosDeletes []iceberg.DataFile,
) error {
	safePaths := make(map[string]struct{}, len(safePosDeletes))
	for _, df := range safePosDeletes {
		safePaths[df.FilePath()] = struct{}{}
	}

	seenDeletes := make(map[string]struct{})
	for _, task := range tasks {
		dataPath := task.File.FilePath()
		if _, rewriting := rewrittenPaths[dataPath]; !rewriting {
			continue
		}
		for _, df := range task.DeleteFiles {
			if df.ContentType() != iceberg.EntryContentPosDeletes {
				continue
			}
			path := df.FilePath()
			if _, ok := seenDeletes[path]; ok {
				continue
			}
			seenDeletes[path] = struct{}{}
			if _, markedSafe := safePaths[path]; !markedSafe {
				continue
			}
			ref := df.ReferencedDataFile()
			if ref != nil && *ref != "" {
				if _, ok := rewrittenPaths[*ref]; !ok {
					return fmt.Errorf(
						"delete file invariant #7 violated: position delete %s references %s not in rewrite scope",
						path,
						*ref,
					)
				}
				continue
			}
			if _, ok := rewrittenPaths[dataPath]; !ok {
				return fmt.Errorf(
					"delete file invariant #7 violated: position delete %s bound to %s outside rewrite scope",
					path,
					dataPath,
				)
			}
		}
	}
	return nil
}
