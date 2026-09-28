import { describe, expect, it } from "vitest";
import { formatCurrency, formatBytesGb } from "../lib/format";
import { formatMeasuredTotal } from "./PlanPreview";

describe("PlanPreview measured totals", () => {
  it("does not sum missing savings or reclaimable bytes as zero", () => {
    expect(formatMeasuredTotal([null, undefined], formatCurrency, "Cost data unavailable")).toBe(
      "Cost data unavailable"
    );
    expect(formatMeasuredTotal([null], formatBytesGb, "Not measured")).toBe("Not measured");
    expect(formatMeasuredTotal([40, null], formatCurrency, "Cost data unavailable")).toMatch(/\(1 of 2 measured\)$/);
    expect(formatMeasuredTotal([40, null], formatCurrency, "Cost data unavailable")).not.toMatch(/^\$0/);
  });
});
