package fixtures

import (
	"context"
	"fmt"

	"github.com/apache/iceberg-go/table"
)

// CountLiveRows returns the live row count (with MOR deletes applied) for a table.
func CountLiveRows(ctx context.Context, cfg Config, tableRef string) (int64, error) {
	tbl, err := loadTable(ctx, cfg, tableRef)
	if err != nil {
		return 0, err
	}
	return countLiveRowsOnTable(ctx, tbl)
}

func countLiveRowsOnTable(ctx context.Context, tbl *table.Table) (int64, error) {
	tasks, err := tbl.Scan().PlanFiles(ctx)
	if err != nil {
		return 0, fmt.Errorf("plan files: %w", err)
	}
	if len(tasks) == 0 {
		return 0, nil
	}
	_, records, err := tbl.Scan().ReadTasks(ctx, tasks)
	if err != nil {
		return 0, fmt.Errorf("read tasks: %w", err)
	}
	var total int64
	for rec, err := range records {
		if err != nil {
			return 0, err
		}
		total += rec.NumRows()
		rec.Release()
	}
	return total, nil
}
