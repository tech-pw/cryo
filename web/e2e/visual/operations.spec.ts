/**
 * Visual regression — operations home (authenticated).
 *
 * Requires API on :8000 — tests skip gracefully when `GET /healthz` fails.
 * Baselines: `operations.spec.ts-snapshots/`
 * Update: `npm run test:visual:update -- --grep operations`
 * Compare: `npm run test:visual -- --grep operations`
 */
import { expect, test } from "@playwright/test";

import { applyTheme, requireApi, settlePage, signIn, type VisualTheme } from "./helpers";

const themes: VisualTheme[] = ["light", "dark"];

for (const theme of themes) {
  test.describe(`operations visual (${theme})`, () => {
    test.use({ colorScheme: theme });

    test.beforeEach(async ({ request }) => {
      await requireApi(request);
    });

    test(`operations home matches baseline (${theme})`, async ({ page }) => {
      await applyTheme(page, theme);
      await signIn(page);
      await page.goto("/operations");
      await settlePage(page);

      await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible({ timeout: 15_000 });
      await expect(page).toHaveScreenshot(`operations-${theme}.png`, {
        fullPage: true,
        animations: "disabled"
      });
    });
  });
}
