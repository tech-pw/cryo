package fixtures

import (
	"context"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go/catalog"
	"github.com/apache/iceberg-go/table"
)

func loadTable(ctx context.Context, cfg Config, tableRef string) (*table.Table, error) {
	cat, err := connectCatalog(ctx, cfg)
	if err != nil {
		return nil, err
	}
	parts := strings.SplitN(tableRef, ".", 2)
	if len(parts) != 2 {
		return nil, fmt.Errorf("invalid table ref %q", tableRef)
	}
	ident := catalog.ToIdentifier(parts[0], parts[1])
	tbl, err := cat.LoadTable(ctx, ident)
	if err != nil {
		return nil, fmt.Errorf("load table: %w", err)
	}
	return tbl, nil
}
