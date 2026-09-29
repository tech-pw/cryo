import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

import {
  VISUAL_NOW_ISO,
  VISUAL_SUBJECT,
  VISUAL_TIMEZONE,
  profileFor,
  resolveVisualResponse,
  visualAccessToken,
  type VisualScenario
} from "./fixtures";

export type { VisualScenario };
export type VisualTheme = "light" | "dark";

const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS"
};

function isVisualApi(url: URL): boolean {
  return url.pathname === "/healthz" || url.pathname.includes("/api/v1/");
}

async function fulfillVisualRoute(route: Route, scenario: VisualScenario): Promise<void> {
  const request = route.request();
  if (request.method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS_HEADERS });
    return;
  }
  let posted: unknown;
  if (request.method() !== "GET" && request.method() !== "HEAD") {
    try {
      posted = request.postDataJSON();
    } catch {
      posted = undefined;
    }
  }
  const response = resolveVisualResponse(scenario, request.method(), new URL(request.url()), posted);
  await route.fulfill({
    status: response.status,
    contentType: "application/json",
    headers: CORS_HEADERS,
    body: JSON.stringify(response.body)
  });
}

export async function installVisualMocks(page: Page, scenario: VisualScenario = "populated"): Promise<void> {
  const profile = profileFor(scenario);
  await page.emulateMedia({ reducedMotion: "reduce" });
  try {
    const client = await page.context().newCDPSession(page);
    await client.send("Emulation.setTimezoneOverride", { timezoneId: VISUAL_TIMEZONE });
  } catch {
    // The init script still forces Intl to Asia/Kolkata when CDP is unavailable.
  }
  await page.clock.install({ time: new Date(VISUAL_NOW_ISO) });
  await page.clock.setFixedTime(new Date(VISUAL_NOW_ISO));
  await page.addInitScript(
    ({ timezone, role, token, subject }) => {
      localStorage.setItem("cryo.accessToken", token);
      localStorage.setItem("cryo.devSubject", subject);
      localStorage.setItem("cryo.devRole", role);
      localStorage.setItem("cryo.authMode", "dev");
      localStorage.setItem("cryo-onboarding-dismissed", "1");
      const Original = Intl.DateTimeFormat;
      const normalizeZone = (resolved: Intl.ResolvedDateTimeFormatOptions) => {
        if (resolved.timeZone === "Asia/Calcutta" || resolved.timeZone === "Asia/Kolkata") {
          resolved.timeZone = timezone;
        }
        return resolved;
      };
      function Patched(locales?: Intl.LocalesArgument, options?: Intl.DateTimeFormatOptions) {
        const format = new Original(locales, { timeZone: timezone, ...options });
        const nativeResolved = format.resolvedOptions.bind(format);
        format.resolvedOptions = () => normalizeZone(nativeResolved());
        return format;
      }
      Patched.prototype = Original.prototype;
      Patched.supportedLocalesOf = Original.supportedLocalesOf.bind(Original);
      Intl.DateTimeFormat = Patched as typeof Intl.DateTimeFormat;
    },
    {
      timezone: VISUAL_TIMEZONE,
      role: profile.role,
      token: visualAccessToken(profile.role),
      subject: VISUAL_SUBJECT
    }
  );
  await page.route(isVisualApi, (route) => fulfillVisualRoute(route, scenario));
  await page.evaluate(() => document.fonts?.ready).catch(() => undefined);
}

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
