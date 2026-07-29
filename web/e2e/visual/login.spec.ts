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
