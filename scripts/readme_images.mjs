// Renders the README images in docs/img/: the hero banner from docs/img/src/readme-hero.html,
// the explainer diagrams from docs/img/src/<name>.html, and element captures of the built docs
// site (site/index.html). Rebuild the site first.
//
//   cd web && node ../scripts/readme_images.mjs
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(`${ROOT}web/package.json`);
const { chromium } = require("@playwright/test");
const IMG = `${ROOT}docs/img/`;
const HERO = pathToFileURL(`${IMG}src/readme-hero.html`).href;
const SITE = pathToFileURL(`${ROOT}site/index.html`).href;

const DIAGRAMS = ["table-decay", "health-grade", "planner-chunks", "governor-admission"];

const SITE_SHOTS = {
  architecture: "section[aria-labelledby='how'] .window",
  "operation-order": "section[aria-labelledby='order'] .ops",
  receipts: "section[aria-labelledby='guarantees'] .receipt",
};

const browser = await chromium.launch();
try {
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 440 }, deviceScaleFactor: 2, colorScheme: scheme });
    const page = await ctx.newPage();
    await page.goto(`${HERO}${scheme === "dark" ? "#dark" : ""}`);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${IMG}hero-${scheme}.png` });
    await ctx.close();
  }

  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    for (const name of DIAGRAMS) {
      await page.goto(`${pathToFileURL(`${IMG}src/${name}.html`).href}${scheme === "dark" ? "#dark" : ""}`);
      await page.evaluate(() => document.fonts.ready);
      await page.locator(".stage").screenshot({ path: `${IMG}${name}-${scheme}.png` });
    }
    await ctx.close();
  }

  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({
      viewport: { width: 1360, height: 1000 },
      deviceScaleFactor: 2,
      colorScheme: scheme,
      reducedMotion: "reduce",
    });
    const page = await ctx.newPage();
    await page.goto(SITE);
    await page.evaluate((s) => document.documentElement.setAttribute("data-theme", s), scheme);
    const shots = { ...SITE_SHOTS };
    // The maintenance flow is a dark instrument panel in both themes, so one file serves both.
    if (scheme === "light") {
      shots["maintenance-flow"] = ".maintenance-machine";
      await page.addStyleTag({ content: "[data-mf-play]{visibility:hidden}" });
    }
    for (const [name, selector] of Object.entries(shots)) {
      const el = page.locator(selector).first();
      await el.scrollIntoViewIfNeeded();
      await page.waitForTimeout(900);
      const file = name === "maintenance-flow" ? `${IMG}${name}.png` : `${IMG}${name}-${scheme}.png`;
      await el.screenshot({ path: file, animations: "disabled" });
    }
    await ctx.close();
  }
} finally {
  await browser.close();
}
