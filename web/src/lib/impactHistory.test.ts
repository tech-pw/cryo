import { describe, expect, it } from "vitest";

import {
  engineBytesLabel,
  fullyMeasuredSummary,
  impactBytesLabel,
  impactChartGb,
  type ImpactDay
} from "./impactHistory";

const day = (state: ImpactDay["measurement_state"], extra: Partial<ImpactDay> = {}): ImpactDay => ({
  report_date: "2026-09-28",
  measurement_state: state,
  bytes_freed: extra.bytes_freed ?? 0,
  actions_succeeded: extra.actions_succeeded ?? 0,
  actions_with_bytes_evidence: extra.actions_with_bytes_evidence ?? 0,
  actions_without_bytes_evidence: extra.actions_without_bytes_evidence ?? 0,
  tables_touched: 1,
  optimize_files_in: 10,
  optimize_files_out: 2,
  jobs_succeeded: 1,
  jobs_failed: 0,
  ...extra
});

describe("impact history states", () => {
  it("renders all four measurement states without a zero bar for an unmeasured day", () => {
    const unmeasured = day("unmeasured", { bytes_freed: 0, actions_succeeded: 4 });
    const partial = day("partial", {
      bytes_freed: 2 * 1024 ** 3,
      actions_succeeded: 4,
      actions_with_bytes_evidence: 1,
      actions_without_bytes_evidence: 3
    });
    const complete = day("complete", {
      bytes_freed: 5 * 1024 ** 3,
      actions_succeeded: 2,
      actions_with_bytes_evidence: 2
    });
    const none = day("no_actions");

    expect(impactBytesLabel(unmeasured)).toBe("Not measured");
    expect(impactChartGb(unmeasured)).toBeNull();
    expect(impactBytesLabel(partial)).toBe("Partial 1 of 4");
    expect(impactChartGb(partial)).toBeCloseTo(2);
    expect(impactBytesLabel(complete)).toBe("5.00 GB");
    expect(impactChartGb(complete)).toBeCloseTo(5);
    expect(impactBytesLabel(none)).toBe("No actions");
    expect(impactChartGb(none)).toBeNull();
    expect(fullyMeasuredSummary([unmeasured, partial, complete, none])).toBe("1 of 4 days fully measured");
    expect(engineBytesLabel(null)).toBe("Unknown");
    expect(engineBytesLabel({ bytes_freed_by_engine: {} })).toBe("Unknown");
  });
});
