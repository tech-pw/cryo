import { describe, expect, it } from "vitest";
import { parseMetricLine } from "./PrometheusPreview";

describe("parseMetricLine", () => {
  it("splits HELP and TYPE metadata", () => {
    expect(parseMetricLine("# HELP cryo_api_requests_total API requests")).toEqual({
      kind: "meta",
      directive: "HELP",
      name: "cryo_api_requests_total",
      rest: "API requests"
    });
    expect(parseMetricLine("# TYPE python_gc_objects_collected_total counter")).toEqual({
      kind: "meta",
      directive: "TYPE",
      name: "python_gc_objects_collected_total",
      rest: "counter"
    });
  });

  it("splits a labeled sample from its value", () => {
    expect(
      parseMetricLine('cryo_api_requests_total{method="GET",status_class="2xx"} 1.0')
    ).toEqual({
      kind: "sample",
      name: "cryo_api_requests_total",
      labels: '{method="GET",status_class="2xx"}',
      value: "1.0"
    });
  });

  it("keeps truncation notes and blanks readable", () => {
    expect(parseMetricLine("")).toEqual({ kind: "blank" });
    expect(parseMetricLine("… (12 more lines)")).toEqual({
      kind: "comment",
      text: "… (12 more lines)"
    });
  });
});
