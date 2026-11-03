import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.DOCS_CAPTURE_PORT ?? 5173);

export default defineConfig({
  testDir: ".",
  // DOCS_CAPTURE_LOOP=1 records the README demo loop instead of the screenshots.
  testMatch: process.env.DOCS_CAPTURE_LOOP ? "demo-loop.ts" : "capture.ts",
  workers: 1,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${PORT} --strictPort`,
    cwd: "../..",
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: true,
    timeout: 120_000
  },
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome", deviceScaleFactor: 2 } }]
});
