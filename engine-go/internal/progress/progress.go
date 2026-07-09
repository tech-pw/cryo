package progress

import (
	"encoding/json"
	"fmt"
	"os"
)

const Prefix = "CRYO_PROGRESS:"

// Event mirrors cryo-engine/src/progress.rs ProgressEvent.
type Event struct {
	Event          string  `json:"event"`
	Operation      string  `json:"operation"`
	ProgressPct    *uint8  `json:"progress_pct,omitempty"`
	FilesProcessed *uint64 `json:"files_processed,omitempty"`
	BytesProcessed *uint64 `json:"bytes_processed,omitempty"`
	Message        *string `json:"message,omitempty"`
	// CommittedGroups / TotalGroups are set on "checkpoint" events so the worker can
	// record durable progress: how many chunk-level commits have landed so far. A
	// mid-run interruption (spot reclaim, commit-race loss, node failure) after a
	// checkpoint only forfeits work done since the last committed group.
	CommittedGroups *int `json:"committed_groups,omitempty"`
	TotalGroups     *int `json:"total_groups,omitempty"`
	// GroupIndex / PartitionKey / MaxFiles / MaxBytes are set on "group_started"
	// events so operators can see which rewrite group was in flight when an OOM
	// killed the subprocess (dense-partition diagnosis).
	GroupIndex    *int    `json:"group_index,omitempty"`
	PartitionKey  *string `json:"partition_key,omitempty"`
	MaxFiles      *int    `json:"max_files_per_group,omitempty"`
	MaxBytes      *uint64 `json:"max_bytes_per_group,omitempty"`
}

func Started(operation string) {
	emit(Event{
		Event:       "started",
		Operation:   operation,
		ProgressPct: uint8Ptr(0),
	})
}

func Progress(operation string, pct uint8, files, bytes uint64) {
	emit(Event{
		Event:          "progress",
		Operation:      operation,
		ProgressPct:    &pct,
		FilesProcessed: &files,
		BytesProcessed: &bytes,
	})
}

// GroupStarted marks the start of a single compaction-group rewrite under
// partial_progress. files/bytes are the compressed input size of this group;
// maxFiles/maxBytes are the active split caps (for log diagnosis after OOM).
func GroupStarted(
	operation, partitionKey string,
	files int,
	bytes uint64,
	maxFiles int,
	maxBytes uint64,
	groupIndex, totalGroups int,
) {
	filesU := uint64(files)
	idx := groupIndex
	total := totalGroups
	maxF := maxFiles
	msg := fmt.Sprintf(
		"group %d/%d partition=%q files=%d bytes=%d max_files=%d max_bytes=%d",
		groupIndex+1, totalGroups, partitionKey, files, bytes, maxFiles, maxBytes,
	)
	emit(Event{
		Event:           "group_started",
		Operation:       operation,
		FilesProcessed:  &filesU,
		BytesProcessed:  &bytes,
		Message:         &msg,
		GroupIndex:      &idx,
		TotalGroups:     &total,
		PartitionKey:    &partitionKey,
		MaxFiles:        &maxF,
		MaxBytes:        &maxBytes,
	})
}

// Checkpoint marks a durable, chunk-level commit: group committedGroups of totalGroups
// has landed atomically. Emitted after each partial-progress group commit so an
// interrupted run can be resumed (or its progress observed) without re-doing committed
// work. Mirrors Iceberg's PARTIAL_PROGRESS_ENABLED per-file-group commit precedent.
func Checkpoint(operation string, committedGroups, totalGroups int, files, bytes uint64) {
	pct := uint8(0)
	if totalGroups > 0 {
		pct = uint8(committedGroups * 100 / totalGroups)
	}
	emit(Event{
		Event:           "checkpoint",
		Operation:       operation,
		ProgressPct:     &pct,
		FilesProcessed:  &files,
		BytesProcessed:  &bytes,
		CommittedGroups: &committedGroups,
		TotalGroups:     &totalGroups,
	})
}

func Completed(operation string, files, bytes uint64) {
	emit(Event{
		Event:          "completed",
		Operation:      operation,
		ProgressPct:    uint8Ptr(100),
		FilesProcessed: &files,
		BytesProcessed: &bytes,
	})
}

func Failed(operation, message string) {
	emit(Event{
		Event:     "failed",
		Operation: operation,
		Message:   &message,
	})
}

// Phase emits a fine-grained progress event for profiling (read/write/commit).
// eventName should be a stable token such as "read_write_started" or "commit_done".
func Phase(operation, eventName string, groupIndex, totalGroups int, files, bytes uint64) {
	idx := groupIndex
	total := totalGroups
	msg := eventName
	emit(Event{
		Event:          eventName,
		Operation:      operation,
		FilesProcessed: &files,
		BytesProcessed: &bytes,
		Message:        &msg,
		GroupIndex:     &idx,
		TotalGroups:    &total,
	})
}

func emit(event Event) {
	payload, err := json.Marshal(event)
	if err != nil {
		return
	}
	fmt.Fprintf(os.Stdout, "%s%s\n", Prefix, payload)
}

func uint8Ptr(v uint8) *uint8 {
	return &v
}
