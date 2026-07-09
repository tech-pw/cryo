package progress

import (
	"encoding/json"
	"io"
	"os"
	"testing"
)

// captureStdout runs fn while os.Stdout is redirected to a pipe and returns what was written.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	orig := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w
	defer func() { os.Stdout = orig }()

	fn()
	_ = w.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	return string(out)
}

func TestCheckpointEmitsParseablePayload(t *testing.T) {
	out := captureStdout(t, func() {
		Checkpoint("rewrite_data_files", 2, 5, 12, 34567)
	})

	const want = Prefix
	if len(out) < len(want) || out[:len(want)] != want {
		t.Fatalf("checkpoint line must start with %q, got %q", Prefix, out)
	}

	var ev Event
	if err := json.Unmarshal([]byte(out[len(Prefix):]), &ev); err != nil {
		t.Fatalf("unmarshal checkpoint payload: %v (raw %q)", err, out)
	}
	if ev.Event != "checkpoint" {
		t.Fatalf("expected event=checkpoint, got %q", ev.Event)
	}
	if ev.Operation != "rewrite_data_files" {
		t.Fatalf("unexpected operation %q", ev.Operation)
	}
	if ev.CommittedGroups == nil || *ev.CommittedGroups != 2 {
		t.Fatalf("expected committed_groups=2, got %v", ev.CommittedGroups)
	}
	if ev.TotalGroups == nil || *ev.TotalGroups != 5 {
		t.Fatalf("expected total_groups=5, got %v", ev.TotalGroups)
	}
	if ev.ProgressPct == nil || *ev.ProgressPct != 40 {
		t.Fatalf("expected progress_pct=40, got %v", ev.ProgressPct)
	}
	if ev.BytesProcessed == nil || *ev.BytesProcessed != 34567 {
		t.Fatalf("expected bytes_processed=34567, got %v", ev.BytesProcessed)
	}
}

func TestCheckpointZeroTotalGroups(t *testing.T) {
	out := captureStdout(t, func() {
		Checkpoint("rewrite_data_files", 0, 0, 0, 0)
	})
	var ev Event
	if err := json.Unmarshal([]byte(out[len(Prefix):]), &ev); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if ev.ProgressPct == nil || *ev.ProgressPct != 0 {
		t.Fatalf("expected progress_pct=0 with zero total groups, got %v", ev.ProgressPct)
	}
}
