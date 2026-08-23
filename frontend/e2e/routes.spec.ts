import { test, expect } from "@playwright/test";

// Every route that renders today, verified against app/**/page.tsx. This is the
// Zero-Dead-UI smoke: if any route 404s or the persistent shell fails to mount,
// it fails here rather than shipping a dead page.
const ROUTES = [
  "/",
  "/tasks",
  "/tasks/live",
  "/approvals",
  "/audit",
  "/events/search",
  "/memory",
  "/capabilities",
  "/automations",
  "/analytics",
  "/experiences",
  "/skills",
  "/providers",
  "/cost",
  "/tools",
  "/models",
  "/schedules",
  "/settings",
];

test.describe("every route renders the app shell (no dead pages)", () => {
  for (const route of ROUTES) {
    test(`GET ${route} loads with the shell present`, async ({ page }) => {
      const response = await page.goto(route, { waitUntil: "domcontentloaded" });
      expect(response?.status() ?? 500, `status for ${route}`).toBeLessThan(400);
      // The persistent Topbar proves the layout mounted — not an error page.
      await expect(page.locator("header.topbar")).toBeVisible();
    });
  }

  test("/dashboard redirects to Home (retired legacy prototype)", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("header.topbar")).toBeVisible();
  });
});
