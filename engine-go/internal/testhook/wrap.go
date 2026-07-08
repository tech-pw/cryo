package testhook

import (
	"context"
	"iter"

	"github.com/apache/iceberg-go"
	icecatalog "github.com/apache/iceberg-go/catalog"
	iceio "github.com/apache/iceberg-go/io"
	"github.com/apache/iceberg-go/table"
)

// RebindTable returns a table that uses the provided catalog and optional FS wrapper.
func RebindTable(tbl *table.Table, cat table.CatalogIO) *table.Table {
	if tbl == nil {
		return nil
	}
	baseFS := func(ctx context.Context) (iceio.IO, error) {
		return tbl.FS(ctx)
	}
	wrappedFS := func(ctx context.Context) (iceio.IO, error) {
		base, err := baseFS(ctx)
		if err != nil {
			return nil, err
		}
		return WrapWriteIO(base), nil
	}
	return table.New(tbl.Identifier(), tbl.Metadata(), tbl.MetadataLocation(), wrappedFS, cat)
}

// WrapCatalog returns a catalog decorator that injects commit faults and rebinds tables.
func WrapCatalog(cat icecatalog.Catalog) icecatalog.Catalog {
	if cat == nil || !FaultInjectionEnabled() {
		return cat
	}
	return &faultCatalog{inner: cat}
}

type faultCatalog struct {
	inner icecatalog.Catalog
}

func (f *faultCatalog) CatalogType() icecatalog.Type {
	return f.inner.CatalogType()
}

func (f *faultCatalog) CreateTable(
	ctx context.Context,
	identifier table.Identifier,
	schema *iceberg.Schema,
	opts ...icecatalog.CreateTableOpt,
) (*table.Table, error) {
	tbl, err := f.inner.CreateTable(ctx, identifier, schema, opts...)
	if err != nil {
		return nil, err
	}
	return RebindTable(tbl, f), nil
}

func (f *faultCatalog) CommitTable(
	ctx context.Context,
	identifier table.Identifier,
	requirements []table.Requirement,
	updates []table.Update,
) (table.Metadata, string, error) {
	if err := MaybeFailCatalogCommit(); err != nil {
		return nil, "", err
	}
	return f.inner.CommitTable(ctx, identifier, requirements, updates)
}

func (f *faultCatalog) ListTables(
	ctx context.Context,
	namespace table.Identifier,
) iter.Seq2[table.Identifier, error] {
	return f.inner.ListTables(ctx, namespace)
}

func (f *faultCatalog) LoadTable(ctx context.Context, identifier table.Identifier) (*table.Table, error) {
	tbl, err := f.inner.LoadTable(ctx, identifier)
	if err != nil {
		return nil, err
	}
	return RebindTable(tbl, f), nil
}

func (f *faultCatalog) DropTable(ctx context.Context, identifier table.Identifier) error {
	return f.inner.DropTable(ctx, identifier)
}

func (f *faultCatalog) RenameTable(ctx context.Context, from, to table.Identifier) (*table.Table, error) {
	tbl, err := f.inner.RenameTable(ctx, from, to)
	if err != nil {
		return nil, err
	}
	return RebindTable(tbl, f), nil
}

func (f *faultCatalog) CheckTableExists(ctx context.Context, identifier table.Identifier) (bool, error) {
	return f.inner.CheckTableExists(ctx, identifier)
}

func (f *faultCatalog) ListNamespaces(ctx context.Context, parent table.Identifier) ([]table.Identifier, error) {
	return f.inner.ListNamespaces(ctx, parent)
}

func (f *faultCatalog) CreateNamespace(
	ctx context.Context,
	namespace table.Identifier,
	props iceberg.Properties,
) error {
	return f.inner.CreateNamespace(ctx, namespace, props)
}

func (f *faultCatalog) DropNamespace(ctx context.Context, namespace table.Identifier) error {
	return f.inner.DropNamespace(ctx, namespace)
}

func (f *faultCatalog) CheckNamespaceExists(ctx context.Context, namespace table.Identifier) (bool, error) {
	return f.inner.CheckNamespaceExists(ctx, namespace)
}

func (f *faultCatalog) LoadNamespaceProperties(
	ctx context.Context,
	namespace table.Identifier,
) (iceberg.Properties, error) {
	return f.inner.LoadNamespaceProperties(ctx, namespace)
}

func (f *faultCatalog) UpdateNamespaceProperties(
	ctx context.Context,
	namespace table.Identifier,
	removals []string,
	updates iceberg.Properties,
) (icecatalog.PropertiesUpdateSummary, error) {
	return f.inner.UpdateNamespaceProperties(ctx, namespace, removals, updates)
}
