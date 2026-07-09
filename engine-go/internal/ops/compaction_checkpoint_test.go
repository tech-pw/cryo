package ops

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/apache/iceberg-go/table"
)

func TestIsCommitRaceLost(t *testing.T) {
	t.Parallel()

	wrapped := errors.Join(errCommitRaceLost, fmt.Errorf("boom: %w", table.ErrCommitFailed))
	if !isCommitRaceLost(wrapped) {
		t.Fatal("expected wrapped error to be classified as commit-race lost")
	}
	// The underlying commit-conflict sentinel must survive the join for upstream checks.
	if !errors.Is(wrapped, table.ErrCommitFailed) {
		t.Fatal("expected ErrCommitFailed to be preserved through errors.Join")
	}
	if isCommitRaceLost(errors.New("some other failure")) {
		t.Fatal("plain error must not be classified as commit-race lost")
	}
	if isCommitRaceLost(nil) {
		t.Fatal("nil error must not be classified as commit-race lost")
	}
}

func TestRewriteErrorMessage(t *testing.T) {
	t.Parallel()

	if msg := rewriteErrorMessage(nil); msg != "" {
		t.Fatalf("nil error should render empty, got %q", msg)
	}

	// A raw retryable commit conflict (single-commit path exhausted retries) is tagged.
	retryable := fmt.Errorf("commit rewrite_data_files: %w", table.ErrCommitFailed)
	if msg := rewriteErrorMessage(retryable); !strings.HasPrefix(msg, commitRaceLostToken+":") {
		t.Fatalf("retryable commit error should carry the commit-race token, got %q", msg)
	}

	// The explicit checkpoint-exhaustion sentinel is tagged.
	raced := errors.Join(errCommitRaceLost, errors.New("exhausted"))
	if msg := rewriteErrorMessage(raced); !strings.HasPrefix(msg, commitRaceLostToken+":") {
		t.Fatalf("commit-race-lost error should carry the token, got %q", msg)
	}

	// Unrelated failures are passed through verbatim (no false-positive alerts).
	other := errors.New("output file missing before commit")
	if msg := rewriteErrorMessage(other); msg != other.Error() {
		t.Fatalf("non-commit error should pass through, got %q", msg)
	}
}

func TestCollectGroupTasks(t *testing.T) {
	t.Parallel()

	groups := []table.CompactionTaskGroup{
		{Tasks: make([]table.FileScanTask, 2)},
		{Tasks: make([]table.FileScanTask, 3)},
		{Tasks: nil},
	}
	if got := len(collectGroupTasks(groups)); got != 5 {
		t.Fatalf("expected 5 flattened tasks, got %d", got)
	}
	if got := len(collectGroupTasks(nil)); got != 0 {
		t.Fatalf("expected 0 tasks for nil groups, got %d", got)
	}
}
