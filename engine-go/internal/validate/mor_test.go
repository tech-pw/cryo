package validate

import (
	"testing"

	"github.com/apache/iceberg-go/table"
)

func TestHasDeleteFilesInScope_empty(t *testing.T) {
	if HasDeleteFilesInScope(nil) {
		t.Fatal("expected false for nil tasks")
	}
	if HasDeleteFilesInScope([]table.FileScanTask{{}}) {
		t.Fatal("expected false for task without delete files")
	}
}

func TestAssertDeleteFileInvariant_emptyRewriteSet(t *testing.T) {
	if err := AssertDeleteFileInvariant(nil, nil); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
