import { test } from "@playwright/test";

import { applyTheme, settlePage } from "../visual/helpers";
import { installDemo } from "./demo-routes";
import { installHomeDemo } from "./home-demo";

// Full-length Home captures against the larger synthetic fleet.
// Run: HOME_SHOT_OUT=/tmp/home npx playwright test -c e2e/docs-capture/home-shot.config.ts
const OUT = process.env.HOME_SHOT_OUT ?? "/tmp/home-shot";
const WIDTH = Number(process.env.HOME_SHOT_WIDTH ?? 1440);

for (const theme of ["light", "dark"] as const) {
  test(`home ${theme}`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
    await installDemo(page, new Set());
    await installHomeDemo(page);
    await applyTheme(page, theme);
    await page.setViewportSize({ width: WIDTH, height: 1000 });
    await page.goto("/");
    await page.getByText("Your lake at a glance").first().waitFor();
    await settlePage(page);
    await page.waitForTimeout(1500);
    const height = await page.evaluate(() => {
      const root = document.querySelector(".home-overview");
      let el: HTMLElement | null = root as HTMLElement | null;
      while (el && el.scrollHeight <= el.clientHeight + 1) el = el.parentElement;
      const top = root?.getBoundingClientRect().top ?? 0;
      return Math.ceil(top + (root?.scrollHeight ?? 1000) + 48);
    });
    await page.setViewportSize({ width: WIDTH, height: Math.max(1000, height) });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}-${theme}.png`, animations: "disabled" });
  });
}
