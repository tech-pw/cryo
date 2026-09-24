import { describe, expect, it } from "vitest";

import { buildParams, describeConstraint } from "./QualityConstraints";

describe("describeConstraint", () => {
  it("renders each rule kind in plain language", () => {
    expect(describeConstraint({ kind: "size", params: { min_rows: 1 }, column_name: null })).toBe(
      "Live rows between 1 and ∞"
    );
    expect(describeConstraint({ kind: "completeness", params: { min: 0.999 }, column_name: "user_id" })).toBe(
      "user_id at least 99.9% non-null"
    );
    expect(describeConstraint({ kind: "range", params: { min: "0" }, column_name: "amount" })).toBe(
      "amount within [0, ∞]"
    );
    expect(describeConstraint({ kind: "size_change", params: { max_decrease: 0.3 }, column_name: null })).toBe(
      "Between snapshots, live rows drop at most 30%"
    );
  });
});

describe("buildParams", () => {
  it("converts percentages to fractions and drops blank fields", () => {
    expect(buildParams("completeness", { min_pct: "99.5" })).toEqual({ min: 0.995 });
    expect(buildParams("size", { min_rows: "10", max_rows: "" })).toEqual({ min_rows: 10 });
    expect(buildParams("size_change", { dec_pct: "30", inc_pct: "100" })).toEqual({
      max_decrease: 0.3,
      max_increase: 1
    });
    expect(buildParams("range", { min: " 2026-01-01 ", max: "" })).toEqual({ min: "2026-01-01" });
  });
});
