package fixtures

import (
	"context"
	"fmt"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/iceberg-go/table"
)

// LiveIntColumnValues returns live int64 values for a column (MOR deletes applied).
func LiveIntColumnValues(ctx context.Context, cfg Config, tableRef, column string) ([]int64, error) {
	tbl, err := loadTable(ctx, cfg, tableRef)
	if err != nil {
		return nil, err
	}
	return liveIntColumnValuesOnTable(ctx, tbl, column)
}

func liveIntColumnValuesOnTable(ctx context.Context, tbl *table.Table, column string) ([]int64, error) {
	tasks, err := tbl.Scan().PlanFiles(ctx)
	if err != nil {
		return nil, fmt.Errorf("plan files: %w", err)
	}
	if len(tasks) == 0 {
		return nil, nil
	}
	_, records, err := tbl.Scan().ReadTasks(ctx, tasks)
	if err != nil {
		return nil, fmt.Errorf("read tasks: %w", err)
	}
	var values []int64
	for rec, err := range records {
		if err != nil {
			return nil, err
		}
		colIdx := rec.Schema().FieldIndices(column)
		if len(colIdx) == 0 {
			rec.Release()
			return nil, fmt.Errorf("column %q not found in scan schema", column)
		}
		col := rec.Column(colIdx[0])
		for row := 0; row < int(rec.NumRows()); row++ {
			values = append(values, int64ValueAt(col, row))
		}
		rec.Release()
	}
	return values, nil
}

func int64ValueAt(col arrow.Array, row int) int64 {
	switch arr := col.(type) {
	case *array.Int64:
		return arr.Value(row)
	case *array.Int32:
		return int64(arr.Value(row))
	default:
		panic(fmt.Sprintf("unsupported int column type %T", col))
	}
}
