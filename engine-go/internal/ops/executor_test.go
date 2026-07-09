package ops

import (
	"strings"
	"testing"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func TestRecoverOperationReturnsFailedResultOnPanic(t *testing.T) {
	result := recoverOperation("optimize", func() protocol.OperationResult {
		panic("boom: invalid TimestampTz format for casting from string '20090'")
	})

	if result.Success {
		t.Fatalf("expected Success=false after a panic, got true")
	}
	if result.Operation != "optimize" {
		t.Fatalf("expected Operation=%q, got %q", "optimize", result.Operation)
	}
	if result.Error == nil || !strings.Contains(*result.Error, "panic in optimize") {
		t.Fatalf("expected error to mention the operation type, got %v", result.Error)
	}
	if result.Error == nil || !strings.Contains(*result.Error, "20090") {
		t.Fatalf("expected the original panic message to be preserved, got %v", result.Error)
	}
}

func TestRecoverOperationPassesThroughNormalResult(t *testing.T) {
	want := protocol.OperationResult{Operation: "expire_snapshots", Success: true}

	got := recoverOperation("expire_snapshots", func() protocol.OperationResult {
		return want
	})

	if !got.Success || got.Operation != "expire_snapshots" {
		t.Fatalf("expected the wrapped result to pass through unchanged, got %+v", got)
	}
}

func TestRecoverOperationDoesNotSwallowSubsequentCalls(t *testing.T) {
	// Regression test for the actual production bug: a panic in one operation must not prevent
	// later operations in the same job from running -- each call to recoverOperation is
	// independent, so a panic in operation 1 can't leak into operation 2's result.
	first := recoverOperation("optimize", func() protocol.OperationResult {
		panic("simulated corrupted manifest")
	})
	second := recoverOperation("expire_snapshots", func() protocol.OperationResult {
		return protocol.OperationResult{Operation: "expire_snapshots", Success: true}
	})

	if first.Success {
		t.Fatalf("expected first operation to report failure")
	}
	if !second.Success {
		t.Fatalf("expected second operation to run normally and succeed, got %+v", second)
	}
}
