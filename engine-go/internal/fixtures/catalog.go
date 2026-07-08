package fixtures

import (
	"context"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/catalog"
	"github.com/apache/iceberg-go/table"

	_ "github.com/apache/iceberg-go/catalog/rest"
	_ "github.com/apache/iceberg-go/io/gocloud"
)

func connectCatalog(ctx context.Context, cfg Config) (catalog.Catalog, error) {
	return catalog.Load(ctx, "cryo-fixtures", cfg.CatalogProperties())
}

func ensureNamespace(ctx context.Context, cat catalog.Catalog) error {
	ns := catalog.ToIdentifier(Namespace)
	err := cat.CreateNamespace(ctx, ns, nil)
	if err == nil {
		return nil
	}
	lowered := strings.ToLower(err.Error())
	if strings.Contains(lowered, "already exists") || strings.Contains(lowered, "409") {
		return nil
	}
	return fmt.Errorf("create namespace: %w", err)
}

func recreateTable(ctx context.Context, cat catalog.Catalog, name string) (*table.Table, error) {
	ident := catalog.ToIdentifier(Namespace, name)
	_ = cat.DropTable(ctx, ident)

	schema := iceberg.NewSchema(0,
		iceberg.NestedField{ID: 1, Name: "id", Type: iceberg.PrimitiveTypes.Int64, Required: true},
		iceberg.NestedField{ID: 2, Name: "label", Type: iceberg.PrimitiveTypes.String, Required: false},
	)
	tbl, err := cat.CreateTable(ctx, ident, schema,
		catalog.WithProperties(iceberg.Properties{
			table.PropertyFormatVersion: "2",
			table.WriteDeleteModeKey:    table.WriteModeMergeOnRead,
		}),
	)
	if err != nil {
		return nil, fmt.Errorf("create table %s: %w", name, err)
	}
	return tbl, nil
}

func dataFilePaths(ctx context.Context, tbl *table.Table) ([]string, error) {
	tasks, err := tbl.Scan().PlanFiles(ctx)
	if err != nil {
		return nil, err
	}
	paths := make([]string, 0, len(tasks))
	for _, task := range tasks {
		paths = append(paths, task.File.FilePath())
	}
	return paths, nil
}
