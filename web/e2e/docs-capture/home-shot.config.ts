import { defineConfig, devices } from "@playwright/test";

const PORT = 5181;

export default defineConfig({
  testDir: ".",
  testMatch: "home-shot.ts",
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
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome", deviceScaleFactor: 1 } }]
});
