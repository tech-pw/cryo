package catalog_test

import (
	"testing"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
)

func TestParseTableIdentSchemaTable(t *testing.T) {
	ident, err := catalog.ParseTableIdent("demo.orders")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(ident) != 2 || ident[0] != "demo" || ident[1] != "orders" {
		t.Fatalf("unexpected ident: %#v", ident)
	}
}

func TestParseTableIdentCatalogPrefix(t *testing.T) {
	ident, err := catalog.ParseTableIdent("rest.demo.orders")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(ident) != 2 || ident[0] != "demo" || ident[1] != "orders" {
		t.Fatalf("unexpected ident: %#v", ident)
	}
}

func TestParseTableIdentMultiLevelNamespace(t *testing.T) {
	ident, err := catalog.ParseTableIdent("rest.org.schema.orders")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(ident) != 3 || ident[2] != "orders" {
		t.Fatalf("unexpected ident: %#v", ident)
	}
}

func TestResolveCatalogType(t *testing.T) {
	cases := []struct {
		name        string
		catalogType string
		uri         string
		want        string
	}{
		{"thrift scheme infers hive", "", "thrift://hms:9083", "hive"},
		{"http scheme infers rest", "", "http://localhost:8181", "rest"},
		{"https scheme infers rest", "", "https://catalog:443", "rest"},
		{"explicit hive alias", "hms", "thrift://hms:9083", "hive"},
		{"explicit hive wins over http uri", "hive", "http://hms:9083", "hive"},
		{"explicit rest alias", "iceberg-rest", "http://localhost:8181", "rest"},
		{"explicit glue", "glue", "", "glue"},
		{"unknown explicit trusted", "sql", "", "sql"},
		{"empty defaults rest", "", "localhost:8181", "rest"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := catalog.ResolveCatalogType(tc.catalogType, tc.uri); got != tc.want {
				t.Fatalf("ResolveCatalogType(%q, %q) = %q, want %q", tc.catalogType, tc.uri, got, tc.want)
			}
		})
	}
}
