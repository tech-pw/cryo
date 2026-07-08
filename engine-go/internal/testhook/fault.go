package testhook

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync/atomic"

	"github.com/apache/iceberg-go/table"
)

const (
	envFailCommit          = "CRYO_TEST_FAIL_COMMIT"
	envFailCommitRemaining = "CRYO_TEST_FAIL_COMMIT_REMAINING"
	envExitBeforeCommit    = "CRYO_TEST_EXIT_BEFORE_COMMIT"
	envCommitNumRetries    = "CRYO_TEST_COMMIT_NUM_RETRIES"
	envFailDataPutOn       = "CRYO_TEST_FAIL_DATA_PUT_ON"
)

var commitFailuresRemaining atomic.Int64

func init() {
	if raw := strings.TrimSpace(os.Getenv(envFailCommitRemaining)); raw != "" {
		if n, err := strconv.ParseInt(raw, 10, 64); err == nil && n > 0 {
			commitFailuresRemaining.Store(n)
		}
	}
}

// CommitNumRetriesOverride returns commit.retry.num-retries when
// CRYO_TEST_COMMIT_NUM_RETRIES is set, else empty.
func CommitNumRetriesOverride() string {
	return strings.TrimSpace(os.Getenv(envCommitNumRetries))
}

// FailDataPutOn returns the 1-based data-file write index to fail, or 0.
func FailDataPutOn() int {
	raw := strings.TrimSpace(os.Getenv(envFailDataPutOn))
	if raw == "" {
		return 0
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return 0
	}
	return n
}

// FaultInjectionEnabled reports whether any fault-injection env var is active.
func FaultInjectionEnabled() bool {
	return FailCommitMode() != "" ||
		commitFailuresRemaining.Load() > 0 ||
		ExitBeforeCommitEnabled() ||
		CommitNumRetriesOverride() != "" ||
		FailDataPutOn() > 0 ||
		PauseBeforeCommitEnabled()
}

func PauseBeforeCommitEnabled() bool {
	raw := strings.TrimSpace(os.Getenv("CRYO_TEST_PAUSE_BEFORE_COMMIT"))
	if raw == "" {
		return false
	}
	n, err := strconv.Atoi(raw)
	return err == nil && n > 0
}

// FailCommitMode returns the active commit-fault mode: conflict, catalog500, or "".
func FailCommitMode() string {
	return strings.ToLower(strings.TrimSpace(os.Getenv(envFailCommit)))
}

// ExitBeforeCommitEnabled is true when CRYO_TEST_EXIT_BEFORE_COMMIT is set.
func ExitBeforeCommitEnabled() bool {
	raw := strings.TrimSpace(os.Getenv(envExitBeforeCommit))
	if raw == "" {
		return false
	}
	switch strings.ToLower(raw) {
	case "0", "false", "no", "off":
		return false
	default:
		return true
	}
}

// BeforeCommit runs test hooks immediately before txn.Commit.
func BeforeCommit() {
	PauseBeforeCommit()
	if ExitBeforeCommitEnabled() {
		os.Exit(137)
	}
}

// MaybeFailCatalogCommit injects catalog commit failures for integration tests.
func MaybeFailCatalogCommit() error {
	if remaining := commitFailuresRemaining.Load(); remaining > 0 {
		commitFailuresRemaining.Add(-1)
		return fmt.Errorf("%w: injected commit conflict (remaining faults)", table.ErrCommitFailed)
	}
	switch mode := FailCommitMode(); mode {
	case "conflict", "always", "exhaust":
		return fmt.Errorf("%w: injected commit conflict", table.ErrCommitFailed)
	case "catalog500", "catalog", "rest500":
		return errors.New("injected catalog commit failure: HTTP 500 Internal Server Error")
	default:
		return nil
	}
}
