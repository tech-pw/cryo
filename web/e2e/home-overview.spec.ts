import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { applyTheme, installVisualMocks, settlePage } from "./visual/helpers";
import { homeResponses } from "./visual/fixtures/home";

async function home(page: Page, theme: "light" | "dark" = "light") {
  await installVisualMocks(page);
  await applyTheme(page, theme);
  for (const [path, body] of Object.entries(homeResponses)) {
    await page.route(`**${path}*`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) }));
  }
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Lake health", exact: true })).toBeVisible();
  await settlePage(page);
}

for (const theme of ["light", "dark"] as const) {
  test(`Home ${theme}: charts, drill-downs and accessible metrics`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await home(page, theme);
    await expect(page.getByRole("region", { name: "Storage impact" })).toContainText("38 of 42 measured");
    await expect(page.getByRole("region", { name: "Latest nightly maintenance" })).toContainText("462 / 500 selected");
    await expect(page.getByRole("region", { name: "Latest nightly maintenance" })).toContainText("Partial");
    await expect(page.getByRole("img", { name: /Cryo platform health/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Grade F, Critical/ })).toHaveAttribute("href", "/tables?grade=F");
    await expect(page.getByRole("link", { name: /76 tables need compaction/ })).toHaveAttribute("href", "/tables?needs=compaction");
    const checks = await new AxeBuilder({ page }).include(".home-overview").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(checks.violations).toEqual([]);
    await page.screenshot({ path: `/tmp/cryo-home-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: "Refresh overview" }).click();
    await expect(page.getByRole("button", { name: "Refresh overview" })).toBeEnabled();
    await page.getByRole("link", { name: /Grade F, Critical/ }).click();
    await expect(page).toHaveURL(/\/tables\?grade=F/);
  });
}

for (const width of [320, 768, 1280]) {
  test(`Home fits a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await home(page);
    const overflow = await page.evaluate(() => ({
      width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth,
      offenders: [...document.querySelectorAll("body *")].filter((el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1).map((el) => ({ tag: el.tagName, className: el.className, width: el.getBoundingClientRect().width })).slice(-12)
    }));
    await page.screenshot({ path: `/tmp/cryo-home-${width}.png`, fullPage: true, animations: "disabled" });
    expect(overflow.scroll, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.width);
    await expect(page.getByRole("link", { name: /Updated in last 24 hours/ })).toBeVisible();
    await page.screenshot({ path: `/tmp/cryo-home-${width}.png`, fullPage: true, animations: "disabled" });
  });
}

for (const scenario of ["unknown", "first-use-empty", "api-failure"] as const) {
  test(`Home handles ${scenario} without fabricated success`, async ({ page }) => {
    await installVisualMocks(page, scenario);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Home", exact: true })).toBeVisible();
    await settlePage(page);
    if (scenario === "unknown") {
      await expect(page.getByText("Grade split unknown")).toBeVisible();
      await expect(page.getByRole("region", { name: "Storage impact" })).toContainText("Not measured");
    } else if (scenario === "first-use-empty") {
      await expect(page.getByRole("region", { name: "Lake health" }).getByRole("link", { name: "Connect a catalog", exact: true })).toBeVisible();
      await expect(page.getByText("No nightly run")).toBeVisible();
    } else {
      await expect(page.getByText("Dashboard unavailable")).toBeVisible();
      await expect(page.getByText("Nightly run unavailable")).toBeVisible();
      await expect(page.getByRole("region", { name: "Storage impact" })).not.toContainText("0 GB");
    }
  });
}
