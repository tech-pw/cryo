import { describe, expect, it } from "vitest";
import type { ChartCardProps } from "./ChartCard";
import type { EventMarkersProps } from "./EventMarkers";
import type { MissingPatternProps } from "./MissingPattern";
import type { TimeScaleOptions } from "./timeScale";

type ForbiddenAxis = "yAxisId" | "secondaryAxis" | "secondaryYAxis" | "yAxisRight" | "dualAxis" | "rightAxis";
type NoForbidden<T> = Extract<keyof T, ForbiddenAxis> extends never ? true : never;

const chartCard: NoForbidden<ChartCardProps> = true;
const events: NoForbidden<EventMarkersProps> = true;
const missing: NoForbidden<MissingPatternProps> = true;
const timeAxis: NoForbidden<TimeScaleOptions> = true;

const sources = import.meta.glob("./**/*.{ts,tsx}", {
  eager: true,
  import: "default",
  query: "?raw"
}) as Record<string, string>;

const FORBIDDEN = /\b(yAxisId|secondaryAxis|secondaryYAxis|yAxisRight|dualAxis|rightAxis)\b/;

describe("chart axis contract", () => {
  it("exports no second Y-axis prop", () => {
    expect(chartCard).toBe(true);
    expect(events).toBe(true);
    expect(missing).toBe(true);
    expect(timeAxis).toBe(true);
    const hits = Object.entries(sources)
      .filter(([key]) => !key.includes(".test."))
      .filter(([, source]) => FORBIDDEN.test(source))
      .map(([key]) => key);
    expect(hits).toEqual([]);
  });
});
