package testhook

import (
	"errors"
	"os"
	"testing"

	"github.com/apache/iceberg-go/table"
)

func TestMaybeFailCatalogCommitModes(t *testing.T) {
	t.Setenv(envFailCommit, "conflict")
	t.Setenv(envFailCommitRemaining, "")
	err := MaybeFailCatalogCommit()
	if err == nil || !errors.Is(err, table.ErrCommitFailed) {
		t.Fatalf("expected ErrCommitFailed, got %v", err)
	}

	t.Setenv(envFailCommit, "catalog500")
	err = MaybeFailCatalogCommit()
	if err == nil || errors.Is(err, table.ErrCommitFailed) {
		t.Fatalf("expected non-retryable catalog error, got %v", err)
	}
}

func TestMaybeFailCatalogCommitRemaining(t *testing.T) {
	t.Setenv(envFailCommit, "")
	t.Setenv(envFailCommitRemaining, "1")
	commitFailuresRemaining.Store(1)

	err := MaybeFailCatalogCommit()
	if err == nil || !errors.Is(err, table.ErrCommitFailed) {
		t.Fatalf("expected first commit to fail, got %v", err)
	}
	if err := MaybeFailCatalogCommit(); err != nil {
		t.Fatalf("expected second commit to succeed, got %v", err)
	}
}

func TestFailDataPutOn(t *testing.T) {
	t.Setenv(envFailDataPutOn, "2")
	if got := FailDataPutOn(); got != 2 {
		t.Fatalf("expected 2, got %d", got)
	}
}

func TestShouldFailDataPut(t *testing.T) {
	t.Setenv(envFailDataPutOn, "1")
	dataPutCount.Store(0)

	if err := shouldFailDataPut("s3://bucket/table/metadata/foo.json"); err != nil {
		t.Fatalf("metadata path should not fail: %v", err)
	}
	if err := shouldFailDataPut("s3://bucket/table/data/file1.parquet"); err == nil {
		t.Fatal("expected first data parquet write to fail")
	}
}

func TestExitBeforeCommitEnabled(t *testing.T) {
	t.Setenv(envExitBeforeCommit, "1")
	if !ExitBeforeCommitEnabled() {
		t.Fatal("expected exit hook enabled")
	}
	t.Setenv(envExitBeforeCommit, "0")
	if ExitBeforeCommitEnabled() {
		t.Fatal("expected exit hook disabled")
	}
}

func TestCommitNumRetriesOverride(t *testing.T) {
	t.Setenv(envCommitNumRetries, "2")
	if got := CommitNumRetriesOverride(); got != "2" {
		t.Fatalf("expected override 2, got %q", got)
	}
}

func TestFaultInjectionEnabled(t *testing.T) {
	for _, key := range []string{
		envFailCommit,
		envFailCommitRemaining,
		envExitBeforeCommit,
		envCommitNumRetries,
		envFailDataPutOn,
		"CRYO_TEST_PAUSE_BEFORE_COMMIT",
	} {
		os.Unsetenv(key)
	}
	commitFailuresRemaining.Store(0)
	if FaultInjectionEnabled() {
		t.Fatal("expected no fault injection by default")
	}
	t.Setenv(envFailCommit, "conflict")
	if !FaultInjectionEnabled() {
		t.Fatal("expected fault injection when FAIL_COMMIT set")
	}
}
