import { describe, expect, it } from "vitest";
import { constantTicks, timeScale, toEpochMs } from "./timeScale";

describe("timeScale", () => {
  it("uses constant time ticks, not the data points", () => {
    expect(constantTicks(0, 100, 5)).toEqual([0, 25, 50, 75, 100]);
    const scale = timeScale([0, 1, 1000], { tickCount: 3 });
    expect(scale?.type).toBe("time");
    expect(scale?.domain).toEqual([0, 1000]);
    expect(scale?.ticks).toEqual([0, 500, 1000]);
    expect(scale).not.toHaveProperty("curve");
  });

  it("treats missing points as gaps, not epoch zero", () => {
    expect(toEpochMs(null)).toBeNull();
    expect(toEpochMs(undefined)).toBeNull();
    expect(toEpochMs(Number.NaN)).toBeNull();
    expect(toEpochMs("not-a-date")).toBeNull();
    expect(toEpochMs(0)).toBe(0);
    const scale = timeScale([null, 1000, undefined, Number.NaN, 3000], { tickCount: 3 });
    expect(scale?.domain).toEqual([1000, 3000]);
    expect(scale?.ticks).toEqual([1000, 2000, 3000]);
    expect(scale?.domain[0]).not.toBe(0);
  });

  it("states the timezone when one is set", () => {
    const epoch = Date.parse("2024-01-15T00:00:00.000Z");
    const options = { tickCount: 2, locale: "en-GB", now: Date.parse("2026-06-01T00:00:00.000Z") };
    const utc = timeScale([epoch], { ...options, timeZone: "UTC" });
    const ist = timeScale([epoch], { ...options, timeZone: "Asia/Kolkata" });
    expect(utc?.timeZone).toBe("UTC");
    expect(ist?.timeZone).toBe("Asia/Kolkata");
    expect(ist?.formatTick(epoch)).not.toBe(utc?.formatTick(epoch));
    expect(ist?.formatTick(epoch)).toContain("05:30");
    expect(utc?.formatTick(epoch)).toContain("2024");
  });

  it("returns null when every point is missing", () => {
    expect(timeScale([null, undefined, "nope"])).toBeNull();
  });
});
