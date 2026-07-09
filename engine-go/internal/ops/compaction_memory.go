package ops

import (
	"encoding/json"

	"github.com/apache/iceberg-go/table"
)

const (
	// defaultScanConcurrency caps parallel Parquet→Arrow readers during rewrite.
	// iceberg-go defaults to GOMAXPROCS; high row-density tables (GeoIP-like) expand
	// tens of× on decode, so unbounded concurrency OOMs well before compressed size
	// would suggest.
	defaultScanConcurrency = 1

	// defaultMaxFilesPerGroup splits oversized bin-pack groups so each rewrite
	// processes a bounded number of input files (and commits separately when
	// partial_progress is enabled).
	defaultMaxFilesPerGroup = 8

	// defaultMaxBytesPerGroup caps compressed input bytes per rewrite group.
	// Matches defaultTargetFileSizeBytes so the memory split does not shred
	// iceberg-go's bin-pack groups that were packed toward the output target.
	// Operators may lower this for dense tables, but then filterGroupsByMinInputFiles
	// drops sub-min groups instead of rewriting 1→1.
	defaultMaxBytesPerGroup = defaultTargetFileSizeBytes

	// defaultMinInputFiles is the Spark/Iceberg rewrite floor: a group must
	// contain at least this many input files or it is skipped (no 1→1 rewrite).
	// iceberg-go's library default is 5; we use 2 so two undersized files can
	// still merge toward target_file_size_bytes.
	defaultMinInputFiles = 2
)

// groupSplitLimits holds the file-count and compressed-byte caps applied after
// iceberg-go bin-pack planning.
type groupSplitLimits struct {
	maxFiles int
	maxBytes int64
}

func rewriteGroupOptions(cfgTargetBytes int64, params json.RawMessage) []table.CompactionGroupOption {
	opts := []table.CompactionGroupOption{
		table.WithCompactionTargetFileSize(cfgTargetBytes),
		table.WithCompactionScanConcurrency(scanConcurrencyFromParams(params)),
	}
	return opts
}

func scanConcurrencyFromParams(raw json.RawMessage) int {
	n := intParam(raw, "scan_concurrency", defaultScanConcurrency)
	if n < 1 {
		return defaultScanConcurrency
	}
	return n
}

func maxFilesPerGroupFromParams(raw json.RawMessage) int {
	n := intParam(raw, "max_files_per_group", defaultMaxFilesPerGroup)
	if n < 1 {
		return defaultMaxFilesPerGroup
	}
	return n
}

func maxBytesPerGroupFromParams(raw json.RawMessage) int64 {
	// Prefer an explicit max_bytes_per_group; otherwise align the memory cap with
	// the configured output target so splits cannot undo bin-pack packing.
	target := int64Param(raw, "target_file_size_bytes", defaultTargetFileSizeBytes)
	if target < 1 {
		target = defaultTargetFileSizeBytes
	}
	n := int64Param(raw, "max_bytes_per_group", target)
	if n < 1 {
		return target
	}
	return n
}

func minInputFilesFromParams(raw json.RawMessage) int {
	n := intParam(raw, "min_input_files", defaultMinInputFiles)
	if n < 1 {
		return defaultMinInputFiles
	}
	return n
}

func groupSplitLimitsFromParams(raw json.RawMessage) groupSplitLimits {
	return groupSplitLimits{
		maxFiles: maxFilesPerGroupFromParams(raw),
		maxBytes: maxBytesPerGroupFromParams(raw),
	}
}

// finalizeRewriteGroups applies memory/file caps after iceberg-go bin-pack, then
// drops groups that no longer meet min_input_files (Spark/Iceberg semantics:
// never rewrite fewer than N files unless MOR forced MinInputFiles=1).
func finalizeRewriteGroups(
	groups []table.CompactionTaskGroup,
	limits groupSplitLimits,
	minInputFiles int,
) []table.CompactionTaskGroup {
	return filterGroupsByMinInputFiles(splitOversizedGroups(groups, limits), minInputFiles)
}

// filterGroupsByMinInputFiles drops rewrite groups with fewer than minInputFiles
// tasks. Matches Spark rewrite_data_files / iceberg-go PlanCompaction bin filtering
// so a memory split cannot turn a multi-file plan into wasteful 1→1 rewrites.
func filterGroupsByMinInputFiles(groups []table.CompactionTaskGroup, minInputFiles int) []table.CompactionTaskGroup {
	if minInputFiles < 1 {
		minInputFiles = 1
	}
	out := make([]table.CompactionTaskGroup, 0, len(groups))
	for _, group := range groups {
		if len(group.Tasks) >= minInputFiles {
			out = append(out, group)
		}
	}
	return out
}

// splitOversizedGroups breaks planned compaction groups that exceed maxFiles or
// maxBytes into smaller groups that share the same partition key. A single file
// larger than maxBytes still forms its own group (cannot split mid-file); callers
// must run filterGroupsByMinInputFiles afterward so such groups are skipped when
// min_input_files > 1.
// Preserves task order within each original group.
func splitOversizedGroups(groups []table.CompactionTaskGroup, limits groupSplitLimits) []table.CompactionTaskGroup {
	maxFiles := limits.maxFiles
	maxBytes := limits.maxBytes
	if maxFiles < 1 {
		maxFiles = defaultMaxFilesPerGroup
	}
	if maxBytes < 1 {
		maxBytes = defaultMaxBytesPerGroup
	}

	out := make([]table.CompactionTaskGroup, 0, len(groups))
	for _, group := range groups {
		if !groupNeedsSplit(group, maxFiles, maxBytes) {
			out = append(out, group)
			continue
		}
		out = append(out, splitGroupByLimits(group, maxFiles, maxBytes)...)
	}
	return out
}

func groupNeedsSplit(group table.CompactionTaskGroup, maxFiles int, maxBytes int64) bool {
	if len(group.Tasks) > maxFiles {
		return true
	}
	if len(group.Tasks) <= 1 {
		return false
	}
	return groupCompressedBytes(group) > maxBytes
}

func splitGroupByLimits(group table.CompactionTaskGroup, maxFiles int, maxBytes int64) []table.CompactionTaskGroup {
	out := make([]table.CompactionTaskGroup, 0)
	var chunk []table.FileScanTask
	var size int64

	flush := func() {
		if len(chunk) == 0 {
			return
		}
		out = append(out, table.CompactionTaskGroup{
			PartitionKey:   group.PartitionKey,
			Tasks:          append([]table.FileScanTask(nil), chunk...),
			TotalSizeBytes: size,
		})
		chunk = nil
		size = 0
	}

	for _, task := range group.Tasks {
		fileBytes := task.File.FileSizeBytes()
		// Start a new chunk if adding this file would exceed either cap
		// (unless the chunk is empty — a single oversized file stays alone).
		wouldExceedFiles := len(chunk) >= maxFiles
		wouldExceedBytes := len(chunk) > 0 && size+fileBytes > maxBytes
		if wouldExceedFiles || wouldExceedBytes {
			flush()
		}
		chunk = append(chunk, task)
		size += fileBytes
	}
	flush()
	return out
}

func groupCompressedBytes(group table.CompactionTaskGroup) int64 {
	if group.TotalSizeBytes > 0 {
		return group.TotalSizeBytes
	}
	var size int64
	for _, task := range group.Tasks {
		size += task.File.FileSizeBytes()
	}
	return size
}
