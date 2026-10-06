import { describe, expect, it } from "vitest";
import { predicateSummary, serializePredicate, tableFiltersToPredicate } from "./predicate";

describe("tableFiltersToPredicate", () => {
  it("maps quick filters to backend predicate grammar", () => {
    expect(
      tableFiltersToPredicate({
        activeOnly: true,
        grades: ["D", "F"],
        priorities: ["HIGH"],
        needsCompaction: true,
        schemaSearch: "events",
        minSizeGb: 50,
        minSmallFiles: 128
      })
    ).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        { field: "grade", op: "in", value: ["D", "F"] },
        { field: "compaction_priority", op: "in", value: ["HIGH"] },
        { field: "needs_compaction", op: "=", value: true },
        {
          any: [
            { field: "schema_name", op: "glob", value: "*events*" },
            { field: "table_name", op: "glob", value: "*events*" }
          ]
        },
        { field: "total_size_gb", op: ">=", value: 50 },
        { field: "small_files_count", op: ">=", value: 128 }
      ]
    });
  });

  it("matches a pasted schema.table name against both columns", () => {
    expect(tableFiltersToPredicate({ schemaSearch: "analytics.orders" })).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        {
          any: [
            { field: "schema_name", op: "glob", value: "*analytics.orders*" },
            { field: "table_name", op: "glob", value: "*analytics.orders*" },
            {
              all: [
                { field: "schema_name", op: "glob", value: "*analytics*" },
                { field: "table_name", op: "glob", value: "*orders*" }
              ]
            }
          ]
        }
      ]
    });
  });

  it("maps savings filters to cost predicate fields", () => {
    expect(
      tableFiltersToPredicate({
        minMonthlySavings: 25,
        minReclaimableGb: 10,
        costPrecision: "metadata"
      })
    ).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        { field: "projected_savings_usd_month", op: ">=", value: 25 },
        { field: "reclaimable_bytes", op: ">=", value: 10 * 1024 ** 3 },
        { field: "cost_precision", op: "=", value: "metadata" }
      ]
    });
  });

  it("maps catalog filters to source_id predicates", () => {
    expect(
      tableFiltersToPredicate({
        sourceIds: ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
      })
    ).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        {
          field: "source_id",
          op: "in",
          value: ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
        }
      ]
    });
  });

  it("maps preset assignment filters to assigned_preset_id predicates", () => {
    expect(
      tableFiltersToPredicate({
        presetFilter: ["__none__", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
      })
    ).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        {
          any: [
            { field: "assigned_preset_id", op: "=", value: null },
            {
              field: "assigned_preset_id",
              op: "in",
              value: ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
            }
          ]
        }
      ]
    });
  });

  it("maps no-preset-only filter", () => {
    expect(tableFiltersToPredicate({ presetFilter: ["__none__"] })).toEqual({
      all: [
        { field: "is_active", op: "=", value: true },
        { field: "assigned_preset_id", op: "=", value: null }
      ]
    });
  });

  it("serializes empty filters as undefined", () => {
    expect(serializePredicate(null)).toBeUndefined();
  });
});

describe("predicateSummary", () => {
  it("uses standardized labels for active filters", () => {
    expect(
      predicateSummary(
        {
          grades: ["B"],
          priorities: ["MEDIUM"],
          needsCompaction: true,
          sourceIds: ["catalog-1"]
        },
        [{ label: "Prod", value: "catalog-1" }]
      )
    ).toBe("Catalog: Prod · Grade: B · Priority: Medium · Needs compaction");
  });

  it("uses standardized default scope labels", () => {
    expect(predicateSummary({ activeOnly: true })).toBe("Active tables");
    expect(predicateSummary({ activeOnly: false })).toBe("All tables");
  });
});
