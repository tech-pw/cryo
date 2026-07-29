import { expect, test } from "@playwright/test";

import { applyTheme, requireApi, settlePage, signIn, type VisualTheme } from "./helpers";

const themes: VisualTheme[] = ["light", "dark"];

for (const theme of themes) {
  test.describe(`health visual (${theme})`, () => {
    test.use({ colorScheme: theme });

    test.beforeEach(async ({ request }) => {
      await requireApi(request);
    });

    test(`datalake health overview matches baseline (${theme})`, async ({ page }) => {
      await applyTheme(page, theme);
      await signIn(page);
      await page.goto("/health");
      await settlePage(page);

      await expect(page.getByRole("heading", { name: "Datalake health" })).toBeVisible();
      await expect(page).toHaveScreenshot(`health-${theme}.png`, {
        fullPage: true,
        animations: "disabled"
      });
    });
  });
}
