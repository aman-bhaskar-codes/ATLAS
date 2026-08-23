import { defineConfig, devices } from "@playwright/test";

/**
 * ATLAS end-to-end tests.
 *
 * These exercise the WHOLE product the way a browser does — no in-browser mocks:
 *
 *  - A CLEAN ATLAS backend on :8730 booted from a fresh data dir every run, with
 *    the external models absent (the honest local posture: DB up, LLM/sandbox
 *    unavailable). Every status/approval assertion is against real runtime state.
 *  - A PRODUCTION Next.js build served on :3000 — the only origin the backend's
 *    CORS policy allows (see api/app.py `allow_origins=["http://localhost:3000"]`),
 *    so the frontend E2E MUST run there.
 *
 * `reuseExistingServer` is on locally (fast iteration against already-running
 * servers) and off in CI (always a clean boot).
 */
const REPO_ROOT = "..";
const API_URL = "http://localhost:8730/api/v1";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Serialize: ATLAS is single-user/local-first and the backend rate-limits per
  // client identity (token bucket, 60/min refill). One worker models the real
  // product AND keeps a single page's polling under the limit — parallel browser
  // contexts share the 127.0.0.1 bucket and would trip 429s (an unrealistic load).
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Clean backend: wipe the data dir so runtime state is deterministic.
      command:
        "rm -rf .e2e-data && uv run uvicorn atlas.interfaces.api.app:create_app " +
        "--factory --host 127.0.0.1 --port 8730",
      cwd: REPO_ROOT,
      url: `${API_URL}/runtime/health`,
      env: {
        ATLAS_ENV: "dev",
        ATLAS_DATA_DIR: ".e2e-data",
        // Lift the per-identity rate-limit ceiling for functional E2E: every
        // Playwright test gets a fresh browser context (cold CORS-preflight
        // cache), so the suite re-issues preflights faster than the production
        // 60/min bucket refills. Defaults are unchanged in production.
        ATLAS_RATE_LIMIT_CAPACITY: "1000000",
        ATLAS_RATE_LIMIT_PER_MINUTE: "1000000",
      },
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // Production build, served on the CORS-allowed origin. API base URL is a
      // build-time inline (NEXT_PUBLIC_*), so it must be set for `next build`.
      command: "npm run build && npx next start -p 3000",
      url: "http://localhost:3000",
      env: { NEXT_PUBLIC_ATLAS_API_URL: API_URL },
      timeout: 180_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
