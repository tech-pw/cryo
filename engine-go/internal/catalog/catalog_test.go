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
