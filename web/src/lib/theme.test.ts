import { beforeEach, describe, expect, it } from "vitest";

import { cryoColors, cryoColorsDark, cryoColorsLight, readActiveColors, resolveChartTheme } from "./theme";

describe("theme reader", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("matches the light Iceberg fallbacks", () => {
    expect(cryoColorsLight.primary).toBe("#277abe");
    expect(cryoColorsLight.primaryHover).toBe("#266fa8");
    expect(cryoColorsLight.primarySoft).toBe("#eaf7fd");
    expect(cryoColorsLight.layoutBg).toBe("#f5fafd");
    expect(cryoColorsLight.surface).toBe("#ffffff");
    expect(cryoColorsLight.panel).toBe("#f3f5f8");
    expect(cryoColorsLight.chartPrimary).toBe("#277abe");
    expect(cryoColorsLight.gradeB).toBe("#277abe");
    expect(cryoColorsLight.gradeA).toBe("#1e8e5a");
    expect(cryoColorsLight.gradeC).toBe("#a9791e");
    expect(cryoColorsLight.gradeD).toBe("#c1631b");
    expect(cryoColorsLight.gradeF).toBe("#b23a2e");
  });

  it("matches the dark Iceberg fallbacks", () => {
    expect(cryoColorsDark.primary).toBe("#68c1ea");
    expect(cryoColorsDark.primaryHover).toBe("#90d4f0");
    expect(cryoColorsDark.layoutBg).toBe("#0b0f16");
    expect(cryoColorsDark.surface).toBe("#0e141f");
    expect(cryoColorsDark.chartPrimary).toBe("#68c1ea");
    expect(cryoColorsDark.gradeB).toBe("#68c1ea");
    expect(cryoColorsDark.gradeA).toBe("#3fbe85");
    expect(cryoColorsDark.gradeF).toBe("#f0665a");
  });

  it("follows the stored theme when data-theme is stale", () => {
    localStorage.setItem("cryo-theme", "dark");
    document.documentElement.setAttribute("data-theme", "light");
    expect(resolveChartTheme()).toBe("dark");
    expect(cryoColors.primary).toBe("#68c1ea");
    expect(cryoColors.layoutBg).toBe("#0b0f16");
    expect(readActiveColors().chartPrimary).toBe("#68c1ea");
  });

  it("follows data-theme when nothing is stored", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    expect(resolveChartTheme()).toBe("dark");
    expect(cryoColors.chartGrid).toBe("#202a38");
  });

  it("defaults to light", () => {
    expect(resolveChartTheme()).toBe("light");
    expect(cryoColors.primary).toBe("#277abe");
    expect(cryoColors.surface).toBe("#ffffff");
  });
});
