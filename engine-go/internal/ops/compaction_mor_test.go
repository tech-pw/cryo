package ops

import (
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/apache/iceberg-go/table"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestIsRESTCatalog(t *testing.T) {
	t.Parallel()
	if !isRESTCatalog(nil) {
		t.Fatal("nil request should default to REST")
	}
	if !isRESTCatalog(&protocol.ExecuteRequest{}) {
		t.Fatal("empty catalog type should default to REST")
	}
	if !isRESTCatalog(&protocol.ExecuteRequest{CatalogProperties: map[string]string{"type": "rest"}}) {
		t.Fatal("type=rest should be REST")
	}
	if !isRESTCatalog(&protocol.ExecuteRequest{CatalogProperties: map[string]string{"type": "REST"}}) {
		t.Fatal("type=REST should case-insensitively match")
	}
	if isRESTCatalog(&protocol.ExecuteRequest{CatalogProperties: map[string]string{"type": "hive"}}) {
		t.Fatal("type=hive should not be REST")
	}
}

// A "branch main has changed" optimistic-concurrency conflict from a Hive metastore does
// not get wrapped with table.ErrCommitFailed/ErrCommitDiverged the way REST catalog
// conflicts do. Confirmed live, 2026-07-21: this exact error propagated as an immediate
// hard failure against a Hive-backed table instead of retrying.
var errUnwrappedHiveConflict = errors.New(`commit rewrite_data_files group 0: requirement failed: branch "main" has changed: expected id 5740756594462960238, found 7046114876609983580`)

func TestIsRetryableCommitError(t *testing.T) {
	t.Parallel()

	if isRetryableCommitError(nil, true) {
		t.Fatal("nil error is never retryable regardless of catalog")
	}
	if isRetryableCommitError(nil, false) {
		t.Fatal("nil error is never retryable regardless of catalog")
	}

	wrapped := fmt.Errorf("commit failed: %w", table.ErrCommitFailed)
	if !isRetryableCommitError(wrapped, true) {
		t.Fatal("REST catalog: a properly wrapped ErrCommitFailed must be retryable")
	}

	diverged := fmt.Errorf("commit diverged: %w", table.ErrCommitDiverged)
	if !isRetryableCommitError(diverged, true) {
		t.Fatal("REST catalog: a properly wrapped ErrCommitDiverged must be retryable")
	}

	// PDAE-8353: the same unwrapped Hive "branch main has changed" error must be
	// retryable even when the request looks like REST (empty catalog type defaults to REST).
	if !isRetryableCommitError(errUnwrappedHiveConflict, true) {
		t.Fatal("REST catalog: unwrapped branch-changed conflict during rewrite_data_files must be retryable")
	}
	if !isRetryableCommitError(errUnwrappedHiveConflict, false) {
		t.Fatal("non-REST catalog: an unwrapped commit conflict must be treated as retryable")
	}

	other := errors.New("output file missing before commit")
	if isRetryableCommitError(other, true) {
		t.Fatal("REST catalog: unrelated errors must not be retryable")
	}
}

func TestRewriteDataFilesRetriesUnwrappedBranchChange(t *testing.T) {
	t.Parallel()

	// Reproduce the rewrite_data_files commit conflict from PDAE-8353
	// (lsq_pw.gold_prospect_activity_extension_base) and assert the outer rewrite
	// loop would retry rather than fail the first group.
	commitErr := fmt.Errorf("commit rewrite_data_files group 0: %w", errUnwrappedHiveConflict)
	if !isRetryableCommitError(commitErr, isRESTCatalog(&protocol.ExecuteRequest{})) {
		t.Fatal("rewrite_data_files must retry an unwrapped branch-changed commit conflict")
	}
	if rewriteCommitAttempts() < 2 {
		t.Fatal("rewrite_data_files must budget more than one commit attempt")
	}
	if rewriteErrorMessage(commitErr) == commitErr.Error() {
		t.Fatal("rewrite_data_files conflict should be tagged as a commit race, not a generic failure")
	}
}

func TestExpireCommitBackoffSchedule(t *testing.T) {
	t.Parallel()
	if got := expireCommitBackoffFor(0); got != 250*time.Millisecond {
		t.Fatalf("attempt 0: got %v want 250ms", got)
	}
	if got := expireCommitBackoffFor(-1); got != 250*time.Millisecond {
		t.Fatalf("negative attempt should clamp to first entry: got %v", got)
	}
	last := expireCommitBackoffFor(len(_expireCommitBackoff) - 1)
	// Attempts beyond the schedule's length must repeat the cap, not panic or grow unbounded.
	if got := expireCommitBackoffFor(len(_expireCommitBackoff) + 10); got != last {
		t.Fatalf("out-of-range attempt: got %v want capped %v", got, last)
	}
	if _expireCommitMaxRetries <= appCommitRetries {
		t.Fatalf("expire_snapshots retry budget (%d) should exceed the rewrite path's (%d) -- "+
			"a live streaming writer needs more attempts, not fewer", _expireCommitMaxRetries, appCommitRetries)
	}
}
