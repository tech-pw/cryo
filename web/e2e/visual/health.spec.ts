/**
 * Visual regression — datalake health overview (authenticated).
 *
 * Requires API on :8000 — tests skip gracefully when `GET /healthz` fails.
 * Baselines: `health.spec.ts-snapshots/`
 * Update: `npm run test:visual:update -- --grep health`
 * Compare: `npm run test:visual -- --grep health`
 */
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

      await expect(page.getByRole("heading", { name: "Home", exact: true })).toBeVisible();
      await expect(page).toHaveScreenshot(`health-${theme}.png`, {
        fullPage: true,
        animations: "disabled"
      });
    });
  });
}
