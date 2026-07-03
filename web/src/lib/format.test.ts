import { describe, expect, it } from "vitest";
import { bytesToGb, formatBytesGb, formatPercent } from "./format";

describe("format helpers", () => {
  it("converts bytes to GB", () => {
    expect(bytesToGb(1024 ** 3)).toBeCloseTo(1);
    expect(bytesToGb(null)).toBe(0);
  });

  it("formats bytes with TB rollover", () => {
    expect(formatBytesGb(1024 ** 4)).toContain("TB");
    expect(formatBytesGb(1024 ** 3)).toContain("GB");
  });

  it("formats percent edge cases", () => {
    expect(formatPercent(42.5, 1)).toBe("42.5%");
    expect(formatPercent(null)).toBe("-");
  });
});
