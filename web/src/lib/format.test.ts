import { describe, expect, it } from "vitest";
import { bytesToGb, formatBytesGb, formatGb, formatPercent, formatTime, humanizeInstants } from "./format";

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

  it("does not print zero for unknown or sub-0.1 GB sizes", () => {
    expect(formatBytesGb(null)).toBe("-");
    expect(formatBytesGb(undefined)).toBe("-");
    expect(formatBytesGb(Number.NaN)).toBe("-");
    expect(formatBytesGb(40 * 1024 * 1024)).toBe("40 MB");
    expect(formatGb(40 / 1024)).toBe("40 MB");
    expect(formatBytesGb(0)).toBe("0 GB");
  });

  it("rewrites embedded ISO instants and leaves the surrounding words", () => {
    const text = humanizeInstants(
      "Cooldown until 2026-10-20T07:45:49.679306+00:00 (last finish 2026-10-19T11:45:49.679306+00:00, cooldown_hours=20.0)"
    );
    expect(text).not.toContain("T07:45:49");
    expect(text).not.toContain("+00:00");
    expect(text).toContain("cooldown_hours=20.0");
    expect(text.startsWith("Cooldown until ")).toBe(true);
  });

  it("includes the year when the date is not in the current year", () => {
    const year = new Date().getFullYear();
    const previous = new Date(year - 1, 7, 19, 10, 0, 0);
    expect(formatTime(previous.toISOString())).toContain(String(year - 1));
    const current = new Date(year, 5, 15, 12, 30, 0);
    expect(formatTime(current.toISOString())).not.toContain(String(year));
  });
});
