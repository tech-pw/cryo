import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "capture.ts",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5173" },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    cwd: "../..",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 120_000
  },
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome", deviceScaleFactor: 2 } }]
});
