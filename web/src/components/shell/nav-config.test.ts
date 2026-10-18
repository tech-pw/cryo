import { describe, expect, it } from "vitest";

import { DESTINATIONS, commandPaletteEntries, visibleSettingsSections } from "../../lib/routes";
import { NAV_GROUPS, resolveActiveNavHref } from "./nav-config";

const hrefs = () => NAV_GROUPS.flatMap((group) => group.items.map((item) => item.href));

const OLD_HREFS = [
  "/health",
  "/impact",
  "/sla",
  "/catalogs",
  "/operations",
  "/smart-maintenance",
  "/control-room",
  "/queries",
  "/anomalies",
  "/scan-cost",
  "/dark-tables",
  "/similarity",
  "/governance",
  "/ai",
  "/skillsets",
  "/connections"
];

describe("seven destinations", () => {
  it("renders exactly the registry, with no old sidebar hrefs", () => {
    expect(hrefs()).toEqual(DESTINATIONS.map((destination) => destination.path));
    expect(hrefs()).toHaveLength(7);
    expect(NAV_GROUPS.flatMap((group) => group.items).map((item) => item.label)).toEqual([
      "Home",
      "Tables",
      "Runs",
      "Decisions",
      "Cost",
      "Insights",
      "Settings"
    ]);
    expect(NAV_GROUPS.map((group) => group.label)).toEqual(["", "Operate", "Understand", ""]);
    for (const href of OLD_HREFS) {
      expect(hrefs()).not.toContain(href);
    }
    expect(NAV_GROUPS.flatMap((group) => group.items).some((item) => item.adminOnly)).toBe(false);
  });

  it("hides admin-only registry entries from viewers", () => {
    expect(visibleSettingsSections("viewer").map((section) => section.path)).not.toContain("/settings/admin");
    expect(visibleSettingsSections("admin").map((section) => section.path)).toContain("/settings/admin");
    expect(commandPaletteEntries("viewer").map((entry) => entry.label)).not.toContain("Skillsets");
    expect(commandPaletteEntries("admin").map((entry) => entry.label)).toContain("Skillsets");
    expect(commandPaletteEntries("viewer").some((entry) => entry.adminOnly)).toBe(false);
  });

  it("highlights the destination for old and new paths", () => {
    expect(resolveActiveNavHref("/")).toBe("/");
    expect(resolveActiveNavHref("/health")).toBe("/");
    expect(resolveActiveNavHref("/tables/abc")).toBe("/tables");
    expect(resolveActiveNavHref("/catalogs")).toBe("/tables");
    expect(resolveActiveNavHref("/operations/runs/1")).toBe("/runs");
    expect(resolveActiveNavHref("/runs/jobs/job-1")).toBe("/runs");
    expect(resolveActiveNavHref("/control-room")).toBe("/runs");
    expect(resolveActiveNavHref("/impact")).toBe("/cost");
    expect(resolveActiveNavHref("/scan-cost")).toBe("/cost");
    expect(resolveActiveNavHref("/smart-maintenance")).toBe("/decisions");
    expect(resolveActiveNavHref("/similarity")).toBe("/insights");
    expect(resolveActiveNavHref("/connections")).toBe("/settings");
    expect(resolveActiveNavHref("/skillsets")).toBe("/settings");
    expect(resolveActiveNavHref("/attribution")).toBe("");
  });
});
