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
