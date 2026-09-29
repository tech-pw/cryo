import { describe, expect, it } from "vitest";
import {
  slaBulletState,
  unsupportedViewNotice,
  unusedCoverageCopy,
  viewFilterSpec
} from "./tableFilters";

describe("viewFilterSpec", () => {
  it("sets a server filter for each saved view", () => {
    expect(viewFilterSpec("freshness")).toEqual({ freshness_breach: true });
    expect(viewFilterSpec("maintenance-age")).toEqual({ maintenance_overdue: true });
    expect(viewFilterSpec("p95-latency")).toEqual({ p95_breach: true });
    expect(viewFilterSpec("unused")).toEqual({ no_reads_days: 30 });
    expect(viewFilterSpec("scan-failed")).toBeNull();
  });

  it("reads the unused window from the URL", () => {
    expect(viewFilterSpec("unused", new URLSearchParams("window=7"))).toEqual({ no_reads_days: 7 });
    expect(viewFilterSpec("unused", new URLSearchParams("no_reads_days=14"))).toEqual({
      no_reads_days: 14
    });
  });

  it("does not tell the operator a view is not applied", () => {
    for (const view of ["freshness", "maintenance-age", "p95-latency", "unused", null]) {
      expect(unsupportedViewNotice(view) ?? "").not.toContain("is not applied");
    }
  });
});

describe("slaBulletState", () => {
  it("is Unknown when either side is null", () => {
    expect(slaBulletState(null, 24).status).toBe("unknown");
    expect(slaBulletState(30, null).status).toBe("unknown");
    expect(slaBulletState(undefined, undefined).status).toBe("unknown");
  });

  it("measures actual against the threshold when both exist", () => {
    expect(slaBulletState(48, 24)).toEqual({ status: "measured", ratio: 1 });
    expect(slaBulletState(12, 24)).toEqual({ status: "measured", ratio: 0.5 });
  });
});

describe("unusedCoverageCopy", () => {
  it("does not describe a coverage gap as unused", () => {
    expect(unusedCoverageCopy(0, 4, 30)).toContain("not unused");
    expect(unusedCoverageCopy(0.5, 2, 7)).toContain("not listed as unused");
    expect(unusedCoverageCopy(null, null, 30)).toContain("Unknown");
    expect(unusedCoverageCopy(null, null, 30)).toContain("not listed as unused");
  });
});
