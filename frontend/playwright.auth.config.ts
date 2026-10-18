import { defineConfig, devices } from "@playwright/test";

// Browser authentication contract tests use HTTP fixtures, so no backend is needed.
// The full-stack suite in playwright.config.ts separately checks the real runtime.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "credentials.spec.ts",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://localhost:3010", ...devices["Desktop Chrome"] },
  webServer: {
    command: "npm run dev -- --port 3010",
    url: "http://localhost:3010",
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
