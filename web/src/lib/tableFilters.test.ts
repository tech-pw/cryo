import { describe, expect, it } from "vitest";
import {
  explorerPredicate,
  scanStaleCutoff,
  slaBulletState,
  triageViewPredicate,
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

describe("triage views", () => {
  it("compiles each quick view to a server predicate", () => {
    expect(triageViewPredicate("failed")).toEqual({ field: "last_job_status", op: "in", value: ["failed", "partial"] });
    expect(triageViewPredicate("grade-dropped")).toEqual({ field: "score_delta_7d", op: "<=", value: -10 });
    expect(JSON.stringify(triageViewPredicate("skipped"))).toContain("last_selection_reason");
    expect(triageViewPredicate("unknown")).toBeNull();
  });

  it("treats never-scanned and stale tables as scan issues with an hour-stable cutoff", () => {
    const now = Date.parse("2026-10-18T12:34:56Z");
    expect(scanStaleCutoff(now)).toBe("2026-10-16T12:00:00.000Z");
    const serialized = JSON.stringify(triageViewPredicate("scan-issues", scanStaleCutoff(now)));
    expect(serialized).toContain('{"field":"scan_at","op":"=","value":null}');
    expect(serialized).toContain("2026-10-16T12:00:00.000Z");
  });

  it("adds the view predicate on top of the explorer filters", () => {
    const serialized = JSON.stringify(explorerPredicate({ activeOnly: true }, { view: "in-progress" }));
    expect(serialized).toContain("is_active");
    expect(serialized).toContain("last_job_status");
  });
});
