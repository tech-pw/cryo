import { describe, expect, it } from "vitest";
import { qualifiedTableName, schemaTableName } from "./tableLabels";

describe("tableLabels", () => {
  it("builds schema.table and qualified names", () => {
    const table = {
      schema_name: "sales",
      table_name: "orders",
      catalog_name: "prod",
      qualified_table_name: "prod.sales.orders"
    };

    expect(schemaTableName(table)).toBe("sales.orders");
    expect(qualifiedTableName(table)).toBe("prod.sales.orders");
  });

  it("falls back to schema.table when qualified name is missing", () => {
    const table = {
      schema_name: "sales",
      table_name: "orders"
    };

    expect(qualifiedTableName(table)).toBe("sales.orders");
  });
});
