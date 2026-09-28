import { describe, expect, it } from "vitest";

import { NAV_GROUPS, resolveActiveNavHref } from "./nav-config";

const hrefs = () => NAV_GROUPS.flatMap((group) => group.items.map((item) => item.href));

describe("nav regroup", () => {
  it("keeps every existing destination and adds Impact", () => {
    expect(hrefs()).toEqual([
      "/health",
      "/impact",
      "/sla",
      "/catalogs",
      "/operations",
      "/smart-maintenance",
      "/control-room",
      "/cost",
      "/queries",
      "/anomalies",
      "/scan-cost",
      "/dark-tables",
      "/similarity",
      "/governance",
      "/ai",
      "/skillsets",
      "/connections"
    ]);
  });

  it("uses the seven group labels from the runbook", () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual([
      "Overview",
      "Tables",
      "Maintenance",
      "Insights",
      "Settings"
    ]);
    expect(NAV_GROUPS.find((group) => group.label === "Maintenance")?.items.map((item) => item.label)).toEqual([
      "Operations",
      "Recommendations",
      "Control Room"
    ]);
  });

  it("keeps Skillsets admin-only and resolves existing routes", () => {
    const skillsets = NAV_GROUPS.flatMap((group) => group.items).find((item) => item.href === "/skillsets");
    expect(skillsets?.adminOnly).toBe(true);
    expect(resolveActiveNavHref("/tables/abc")).toBe("/catalogs");
    expect(resolveActiveNavHref("/operations/runs/1")).toBe("/operations");
    expect(resolveActiveNavHref("/impact")).toBe("/impact");
    expect(resolveActiveNavHref("/smart-maintenance")).toBe("/smart-maintenance");
  });
});
