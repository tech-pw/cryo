import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

export type VisualTheme = "light" | "dark";

export async function applyTheme(page: Page, theme: VisualTheme): Promise<void> {
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("cryo-theme", selectedTheme);
    localStorage.setItem("cryo-onboarding-dismissed", "1");
    document.documentElement.setAttribute("data-theme", selectedTheme);
  }, theme);
}

export async function requireApi(request: APIRequestContext): Promise<void> {
  const health = await request.get("http://127.0.0.1:8000/healthz");
  test.skip(!health.ok(), "API must be running on :8000 for authenticated visual snapshots");
}

export async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("visual-e2e");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Datalake health").first()).toBeVisible({ timeout: 15_000 });
}

export async function settlePage(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts?.ready);
}
