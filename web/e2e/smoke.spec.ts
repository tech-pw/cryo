import { expect, test } from "@playwright/test";

test.describe("Cryo smoke", () => {
  test("login → health → tables → run cancel", async ({ page, request }) => {
    const health = await request.get("http://127.0.0.1:8000/healthz");
    test.skip(!health.ok(), "API must be running on :8000 for smoke e2e");

    await page.goto("/");

    await page.getByLabel("Username").fill("e2e-operator");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Datalake health")).toBeVisible({ timeout: 15_000 });

    await page.getByRole("link", { name: "Tables" }).click();
    await expect(page.getByRole("heading", { name: "Table explorer" })).toBeVisible();

    await page.getByRole("link", { name: "Runs" }).click();
    await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();

    const cancelButton = page.getByRole("button", { name: "Cancel" }).first();
    if (await cancelButton.isVisible()) {
      await cancelButton.click();
      await page.getByRole("button", { name: "Confirm" }).click();
      await expect(page.getByText(/cancel/i)).toBeVisible({ timeout: 10_000 });
    }
  });
});
