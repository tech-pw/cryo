import { describe, expect, it } from "vitest";
import {
  formatCap,
  formatChangeAnswer,
  formatCoverage,
  formatFixedBytes,
  formatFixedUnit,
  formatSubtitle,
  formatTableCell,
  provenanceLabel,
  UNKNOWN_MARK
} from "./format";

describe("chart formatters", () => {
  it("computes the answer line and refuses free-text gaps", () => {
    expect(
      formatChangeAnswer({ label: "Small files", previous: 100, current: 82, since: "1 Sep" })
    ).toBe("Small files down 18% since 1 Sep");
    expect(
      formatChangeAnswer({ label: "Small files", previous: 100, current: 118, since: "1 Sep" })
    ).toBe("Small files up 18% since 1 Sep");
    expect(
      formatChangeAnswer({ label: "Small files", previous: 10, current: 10, since: "1 Sep" })
    ).toBe("Small files unchanged since 1 Sep");
    expect(formatChangeAnswer({ label: "Small files", previous: null, current: 82, since: "1 Sep" })).toBeNull();
    expect(formatChangeAnswer({ label: "Small files", previous: 0, current: 10, since: "1 Sep" })).toBeNull();
    expect(formatChangeAnswer({ label: "Small files", previous: Number.NaN, current: 10, since: "1 Sep" })).toBeNull();
  });

  it("formats subtitle, coverage, and cap without inventing a total", () => {
    expect(
      formatSubtitle({ unit: "GB", period: "last 7 days", asOf: "28 Sep", timeZone: "Asia/Kolkata" })
    ).toBe("GB \u00b7 last 7 days \u00b7 as of 28 Sep \u00b7 Asia/Kolkata");
    expect(formatCoverage({ measured: 8, total: 12, noun: "actions" })).toBe(
      "Partial \u00b7 measured for 8 of 12 actions"
    );
    expect(formatCoverage({ measured: 12, total: 12, noun: "actions" })).toBeNull();
    expect(formatCap({ shown: 15, total: 40 })).toBe("Top 15 of 40");
    expect(formatCap({ shown: 40, total: 40 })).toBeNull();
  });

  it("keeps one unit and never prints unknown as zero", () => {
    expect(formatTableCell(null)).toBe(UNKNOWN_MARK);
    expect(formatTableCell(undefined)).toBe(UNKNOWN_MARK);
    expect(formatTableCell(Number.NaN)).toBe(UNKNOWN_MARK);
    expect(formatTableCell(0)).toBe("0");
    expect(formatFixedUnit(null, "GB")).toBe(UNKNOWN_MARK);
    expect(formatFixedUnit(0, "GB")).toBe("0 GB");
    expect(formatFixedUnit(0.04, "GB")).toBe("< 0.1 GB");
    expect(formatFixedUnit(0.04, "GB")).not.toBe("0 GB");
    expect(formatFixedBytes(40 * 1024 * 1024, "GB")).toBe("< 0.1 GB");
    expect(formatFixedBytes(40 * 1024 * 1024, "GB")).not.toContain("MB");
    expect(formatFixedBytes(2 * 1024 ** 3, "GiB")).toBe("2 GiB");
    expect(formatFixedBytes(2 * 1000 ** 3, "GB")).toBe("2 GB");
    expect(formatFixedBytes(null, "GB")).toBe(UNKNOWN_MARK);
  });

  it("labels provenance chips", () => {
    expect(provenanceLabel("measured")).toBe("Measured");
    expect(provenanceLabel("modelled")).toBe("Modelled");
    expect(provenanceLabel("modeled")).toBe("Modelled");
    expect(provenanceLabel("estimated-pricing")).toBe("Estimated pricing");
    expect(provenanceLabel("partial")).toBe("Partial");
  });
});
