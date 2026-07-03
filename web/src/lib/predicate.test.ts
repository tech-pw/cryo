import { describe, expect, it } from "vitest";
import { serializePredicate, tableFiltersToPredicate } from "./predicate";

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

  it("serializes empty filters as undefined", () => {
    expect(serializePredicate(null)).toBeUndefined();
  });
});
