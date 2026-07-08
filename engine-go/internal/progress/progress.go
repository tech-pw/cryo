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
