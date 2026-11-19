import { describe, expect, it } from "vitest";
import { buildFilterChips } from "./filterChips";
import { predicateSummary, tableFiltersToPredicate } from "./predicate";
import { filtersFromSearchParams, filtersToSearchParamEntries } from "./tableFilters";

const GIB = 1024 ** 3;

describe("storage filters (#602)", () => {
  it("filters by bucket and storage thresholds, with 0 meaning any measured amount", () => {
    const predicate = tableFiltersToPredicate({
      activeOnly: false,
      buckets: ["audit-bucket"],
      minOrphanGb: 0,
      minJunkGb: 2
    });
    expect(predicate).toEqual({
      all: [
        { field: "bucket", op: "in", value: ["audit-bucket"] },
        { field: "orphan_bytes", op: ">", value: 0 },
        { field: "junk_bytes", op: ">=", value: 2 * GIB }
      ]
    });
  });

  it("round-trips through the URL", () => {
    const filters = { activeOnly: true, buckets: ["a", "b"], minOrphanGb: 0, minSnapshotGb: 5, minJunkGb: 1.5 };
    const params = new URLSearchParams(filtersToSearchParamEntries(filters));
    expect(params.get("bucket")).toBe("a,b");
    expect(params.get("orphans")).toBe("0");
    const back = filtersFromSearchParams(params);
    expect(back.buckets).toEqual(["a", "b"]);
    expect(back.minOrphanGb).toBe(0);
    expect(back.minSnapshotGb).toBe(5);
    expect(back.minJunkGb).toBe(1.5);
  });

  it("summarises and chips the new filters", () => {
    const filters = { buckets: ["audit-bucket"], minOrphanGb: 0, minJunkGb: 10 };
    expect(predicateSummary(filters)).toContain("Bucket: audit-bucket");
    expect(predicateSummary(filters)).toContain("Has orphans");
    const labels = buildFilterChips(filters, [], () => undefined).map((chip) => chip.label);
    expect(labels).toEqual(expect.arrayContaining(["Bucket: audit-bucket", "Has orphans", "Junk ≥ 10 GB"]));
  });
});
