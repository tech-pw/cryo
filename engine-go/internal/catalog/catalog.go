package catalog

import (
	"context"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/catalog"
	"github.com/apache/iceberg-go/table"

	_ "github.com/apache/iceberg-go/catalog/rest"
	_ "github.com/apache/iceberg-go/io/gocloud"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

// LoadTable opens the REST (or configured) catalog and loads the target table.
func LoadTable(ctx context.Context, req *protocol.ExecuteRequest) (catalog.Catalog, *table.Table, error) {
	props := buildProperties(req)
	cat, err := catalog.Load(ctx, "cryo", props)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to connect to catalog: %w", err)
	}

	ident, err := ParseTableIdent(req.TableRef)
	if err != nil {
		return nil, nil, err
	}

	tbl, err := cat.LoadTable(ctx, ident)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to load table: %w", err)
	}

	return cat, tbl, nil
}

func buildProperties(req *protocol.ExecuteRequest) iceberg.Properties {
	props := iceberg.Properties{
		"type":      "rest",
		"uri":       req.CatalogURI,
		"warehouse": req.Warehouse,
	}
	if req.S3Endpoint != nil && *req.S3Endpoint != "" {
		props["s3.endpoint"] = *req.S3Endpoint
	}
	if req.S3AccessKey != nil && *req.S3AccessKey != "" {
		props["s3.access-key-id"] = *req.S3AccessKey
	}
	if req.S3SecretKey != nil && *req.S3SecretKey != "" {
		props["s3.secret-access-key"] = *req.S3SecretKey
	}
	for key, value := range req.CatalogProperties {
		if value != "" {
			props[key] = value
		}
	}
	return props
}

// ParseTableIdent mirrors cryo-engine parse_table_ident.
func ParseTableIdent(tableRef string) (table.Identifier, error) {
	parts := strings.Split(tableRef, ".")
	if len(parts) < 2 {
		return nil, fmt.Errorf(
			"invalid table_ref: %s (expected namespace.table or catalog.namespace.table)",
			tableRef,
		)
	}
	tableName := parts[len(parts)-1]
	var namespaceParts []string
	if len(parts) >= 3 {
		namespaceParts = parts[1 : len(parts)-1]
	} else {
		namespaceParts = []string{parts[0]}
	}
	return catalog.ToIdentifier(append(namespaceParts, tableName)...), nil
}
