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

// groupSplitLimits holds optional file-count and compressed-byte caps applied
// after iceberg-go bin-pack planning. maxFiles <= 0 means no file-count split
// (bytes-only); packing toward target_file_size_bytes must not be shredded by a
// small fixed file cap (e.g. thousands of 1 KiB files filling one target bin).
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

// maxFilesPerGroupFromParams returns an optional soft file-count ceiling.
// 0 (default / unset / non-positive) means unlimited — split only on
// max_bytes_per_group so tiny-file storms can pack toward the output target.
func maxFilesPerGroupFromParams(raw json.RawMessage) int {
	n := intParam(raw, "max_files_per_group", 0)
	if n < 1 {
		return 0
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
		n = target
	}
	// Defense in depth: never execute groups larger than budget ÷ planning
	// expansion, even if a stale/over-eager planner stamped a multi-GiB cap
	// (run 3483a157: 2 GiB groups → ~20 GiB RSS under 512 MiB budget).
	n = clampMaxBytesToMemoryBudget(n, target, raw)
	return n
}

// planningMemoryExpansion is the streaming-rewrite factor used to size groups
// from compaction_memory_budget_bytes. Lower than defaultMemoryExpansionFactor
// (admit-gate / dense-string calibration) because ExecuteCompactionGroup streams
// batches; observed ~10× on unpartitioned ~2 GiB compressed groups.
const planningMemoryExpansion = 8

// packingTargetsPerGroup is how many target-sized outputs we prefer to pack
// into one ExecuteCompactionGroup so the rolling writer does not emit one
// undersized remainder file per tiny group.
const packingTargetsPerGroup = 2

func clampMaxBytesToMemoryBudget(maxBytes, target int64, raw json.RawMessage) int64 {
	budget := memoryBudgetFromParams(raw)
	if budget < 1 {
		return maxBytes
	}
	if target < 1 {
		target = defaultTargetFileSizeBytes
	}
	packingFloor := target * packingTargetsPerGroup
	if packingFloor < target {
		packingFloor = target
	}
	safe := budget / planningMemoryExpansion
	if safe < 1 {
		safe = 1
	}
	// Never shred below one target file. Prefer the packing floor when the
	// budget can hold it so remainder tails stay rare.
	floor := target
	if safe >= packingFloor {
		floor = packingFloor
	}
	if safe < floor {
		safe = floor
	}
	// Preserve explicit dense-table overrides below the floor.
	if maxBytes > 0 && maxBytes < floor {
		return maxBytes
	}
	if maxBytes > safe {
		return safe
	}
	return maxBytes
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

// finalizeRewriteGroups applies memory/file caps after iceberg-go bin-pack:
// split bins that exceed max_bytes (dense / tight overrides), then coalesce
// same-partition bins up to max_bytes so larger pods/chunks run fewer groups
// without changing target_file_size_bytes (output size). Finally drop groups
// that no longer meet min_input_files.
func finalizeRewriteGroups(
	groups []table.CompactionTaskGroup,
	limits groupSplitLimits,
	minInputFiles int,
) []table.CompactionTaskGroup {
	split := splitOversizedGroups(groups, limits)
	coalesced := coalesceGroupsUpTo(split, limits)
	return filterGroupsByMinInputFiles(coalesced, minInputFiles)
}

// coalesceGroupsUpTo merges consecutive same-partition rewrite groups until
// adding the next group would exceed maxBytes or the optional maxFiles ceiling.
// iceberg-go PlanCompaction packs
// toward target_file_size_bytes; raising max_bytes_per_group alone does not
// merge those bins — this step does, so pod-scaled caps reduce group count
// while ExecuteCompactionGroup still writes at target_file_size_bytes.
func coalesceGroupsUpTo(groups []table.CompactionTaskGroup, limits groupSplitLimits) []table.CompactionTaskGroup {
	maxBytes := limits.maxBytes
	if maxBytes < 1 || len(groups) <= 1 {
		return groups
	}
	out := make([]table.CompactionTaskGroup, 0, len(groups))
	cur := groups[0]
	if cur.TotalSizeBytes <= 0 {
		cur.TotalSizeBytes = groupCompressedBytes(cur)
	}
	for i := 1; i < len(groups); i++ {
		next := groups[i]
		nextBytes := groupCompressedBytes(next)
		curBytes := groupCompressedBytes(cur)
		wouldExceedFiles := limits.maxFiles >= 1 && len(cur.Tasks)+len(next.Tasks) > limits.maxFiles
		if next.PartitionKey != cur.PartitionKey ||
			curBytes+nextBytes > maxBytes ||
			wouldExceedFiles {
			out = append(out, cur)
			cur = next
			if cur.TotalSizeBytes <= 0 {
				cur.TotalSizeBytes = nextBytes
			}
			continue
		}
		cur.Tasks = append(cur.Tasks, next.Tasks...)
		cur.TotalSizeBytes = curBytes + nextBytes
	}
	out = append(out, cur)
	return out
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

// splitOversizedGroups breaks planned compaction groups that exceed maxBytes
// (and optionally maxFiles when > 0) into smaller groups that share the same
// partition key. A single file larger than maxBytes still forms its own group
// (cannot split mid-file); callers must run filterGroupsByMinInputFiles afterward
// so such groups are skipped when min_input_files > 1.
// Preserves task order within each original group.
func splitOversizedGroups(groups []table.CompactionTaskGroup, limits groupSplitLimits) []table.CompactionTaskGroup {
	maxFiles := limits.maxFiles // <= 0 → no file-count split
	maxBytes := limits.maxBytes
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
	if maxFiles >= 1 && len(group.Tasks) > maxFiles {
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
		// Start a new chunk if adding this file would exceed the byte cap
		// (or an optional file-count ceiling). An empty chunk always accepts
		// the next file — a single oversized file stays alone.
		wouldExceedFiles := maxFiles >= 1 && len(chunk) >= maxFiles
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
