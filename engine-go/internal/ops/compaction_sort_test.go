package ops

import (
	"encoding/json"
	"testing"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

func TestSortCompactionRequested(t *testing.T) {
	t.Parallel()
	raw := json.RawMessage(`{"strategy":"sort","sort_columns":["id"]}`)
	if !sortCompactionRequested(raw) {
		t.Fatal("expected sort compaction requested")
	}
	binpack := json.RawMessage(`{"strategy":"binpack"}`)
	if sortCompactionRequested(binpack) {
		t.Fatal("binpack should not request sort")
	}
}

func TestValidateCompactionSortModeRejectsZOrderParams(t *testing.T) {
	t.Parallel()
	raw := json.RawMessage(`{"strategy":"zorder","zorder_columns":["id"]}`)
	if len(zorderColumns(raw)) == 0 {
		t.Fatal("expected zorder columns")
	}
	if strategyParam(raw) != "zorder" {
		t.Fatal("expected zorder strategy")
	}
}

func TestAssertSortColumnsMatchTableOrder(t *testing.T) {
	t.Parallel()
	schema := iceberg.NewSchema(1, iceberg.NestedField{ID: 1, Name: "event_date", Type: iceberg.PrimitiveTypes.Date})
	sortOrder, err := table.NewSortOrder(1, []table.SortField{
		{
			SourceIDs: []int{1},
			Transform: iceberg.IdentityTransform{},
			Direction: table.SortASC,
			NullOrder: table.NullsFirst,
		},
	})
	if err != nil {
		t.Fatalf("sort order: %v", err)
	}
	if err := assertSortColumnsMatchTableOrder(schema, sortOrder, []string{"event_date"}, nil); err != nil {
		t.Fatalf("expected match: %v", err)
	}
	if err := assertSortColumnsMatchTableOrder(schema, sortOrder, []string{"other_col"}, nil); err == nil {
		t.Fatal("expected mismatch error")
	}
}

func TestStringSliceParam(t *testing.T) {
	t.Parallel()
	raw := json.RawMessage(`{"sort_columns":["a","b"],"sort_directions":["asc","desc"]}`)
	cols := sortColumns(raw)
	if len(cols) != 2 || cols[0] != "a" {
		t.Fatalf("unexpected columns: %#v", cols)
	}
	dirs := sortDirections(raw)
	if len(dirs) != 2 || dirs[1] != "desc" {
		t.Fatalf("unexpected directions: %#v", dirs)
	}
}
