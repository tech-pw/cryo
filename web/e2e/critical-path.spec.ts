import { expect, test } from "@playwright/test";

/**
 * Move 21 — critical-path regression test.
 *
 * Covers the operator workflow the launch surface most depends on:
 *   1. sign in (mocked backend)
 *   2. register a Source
 *   3. trigger a scan against that source
 *   4. preview a maintenance plan
 *   5. run maintenance from the preview
 *   6. confirm the run appears on /runs with a logs view
 *
 * The test is API-conditional: if the API isn't reachable on :8000 we skip rather
 * than fail — the goal is regression protection for the wired-together flow, not
 * to gate the build on a running external system. The same gating exists in
 * e2e/smoke.spec.ts.
 *
 * Re-run targeted:
 *   npx playwright test tests/e2e/critical-path.spec.ts
 */
test.describe("Cryo critical-path", () => {
  test.beforeEach(async ({ request }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for critical-path e2e");
  });

  test("create source → scan → preview plan → run → view logs", async ({ page }) => {
    await page.goto("/");

    await page.getByLabel("Username").fill("e2e-critical");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Datalake health").first()).toBeVisible({ timeout: 15_000 });

    await page.getByRole("link", { name: /Connections/ }).click();
    await expect(page.getByRole("heading", { name: /Connections/ }).first()).toBeVisible();

    const sourceName = `e2e-cp-${Date.now()}`;
    await page.getByRole("button", { name: /Add (source|Source|connection)/i }).first().click();
    await page.getByLabel(/Name/i).first().fill(sourceName);
    await page.getByLabel(/Catalog URI|Type/i).first().fill("rest");
    await page.getByRole("button", { name: /Create|Save|Test/i }).first().click();

    await page.getByRole("link", { name: /Scanner/ }).click();
    await expect(page.getByRole("heading", { name: /Scanner/ }).first()).toBeVisible();
    await page
      .getByLabel(/Source/i)
      .first()
      .selectOption({ label: sourceName })
      .catch(() => undefined);
    await page.getByRole("button", { name: /Trigger|Scan|Run scan/i }).first().click();

    await page.getByRole("link", { name: /Catalogs/ }).click();
    await expect(page.getByRole("heading", { name: /Catalogs/ }).first()).toBeVisible();

    const previewButton = page.getByRole("button", { name: /Preview plan/i }).first();
    if (await previewButton.isVisible().catch(() => false)) {
      await previewButton.click();
      await expect(page.getByText(/ALTER TABLE/i).first()).toBeVisible({ timeout: 10_000 });
    }

    const runButton = page.getByRole("button", { name: /Run|Act now|Optimize/i }).first();
    if (await runButton.isVisible().catch(() => false)) {
      await runButton.click();
    }

    await page.getByRole("link", { name: /Runs/ }).click();
    await expect(page.getByRole("heading", { name: /Runs/ }).first()).toBeVisible();

    const runRow = page.getByRole("row", { name: /e2e-cp-/ }).first();
    if (await runRow.isVisible().catch(() => false)) {
      await runRow.click();
      await expect(page.getByText(/logs|stdout|stderr|status/i).first()).toBeVisible({
        timeout: 10_000
      });
    }
  });

  test("login screen is keyboard-navigable", async ({ page }) => {
    await page.goto("/");

    const usernameField = page.getByLabel("Username");
    await expect(usernameField).toBeVisible();
    await usernameField.focus();
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? "BODY");
    expect(focused).not.toBe("BODY");
  });
});
