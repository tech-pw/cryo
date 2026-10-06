/**
 * Visual regression — login screen (no API required).
 *
 * Baselines live beside this file: `login.spec.ts-snapshots/`
 * Update after intentional UI change: `npm run test:visual:update -- --grep login`
 * Compare only: `npm run test:visual -- --grep login`
 */
import { expect, test } from "@playwright/test";

import { applyTheme, settlePage, type VisualTheme } from "./helpers";

const themes: VisualTheme[] = ["light", "dark"];

for (const theme of themes) {
  test.describe(`login visual (${theme})`, () => {
    test.use({ colorScheme: theme });

    test(`login screen matches baseline (${theme})`, async ({ page }) => {
      await applyTheme(page, theme);
      await page.goto("/");
      await settlePage(page);

      await expect(page.getByRole("heading", { name: "Sign in to Cryo" })).toBeVisible();
      await expect(page).toHaveScreenshot(`login-${theme}.png`, {
        fullPage: true,
        animations: "disabled"
      });
    });
  });
}
