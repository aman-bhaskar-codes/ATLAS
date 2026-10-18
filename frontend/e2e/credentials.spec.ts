import { test, expect } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`Settings credential recovery and removal at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let rejected = 0;
    const requests: { url: string; auth: string | undefined }[] = [];
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const auth = request.headers().authorization;
      requests.push({ url: request.url(), auth });
      if (auth !== "Bearer browser-test-key") {
        rejected++;
        await route.fulfill({ status: 401, json: { detail: "invalid API key" } });
      } else if (request.url().includes("/runtime/status")) {
        await route.fulfill({ json: { state: "ready", version: "test", environment: "dev", kill_switch_active: false, active_task_count: 0, pending_approval_count: 0, last_audit_at: null } });
      } else if (request.url().includes("/runtime/health")) {
        await route.fulfill({ json: { overall: "healthy", checks: [] } });
      } else {
        await route.fulfill({ json: [] });
      }
    });
    await page.goto("/settings");
    await expect(page.getByText("Failed to load settings")).toBeVisible();
    const key = page.getByLabel("API key", { exact: true });
    await expect(key).toBeVisible();
    expect((await key.boundingBox())?.width).toBeGreaterThan(200);
    await key.fill("invalid key");
    await page.getByRole("button", { name: "Save API key", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "without spaces" })).toBeVisible();
    await expect(key).toBeFocused();
    await key.fill("browser-test-key");
    await key.press("Enter");
    await expect(page.getByText("Failed to load settings")).toBeHidden();
    await expect(page.getByText("API key saved for this tab.", { exact: true })).toBeVisible();
    expect(requests.some((request) => request.auth === "Bearer browser-test-key")).toBe(true);
    expect(requests.some((request) => request.url.includes("browser-test-key"))).toBe(false);
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("browser-test-key");
    await page.reload();
    await expect(page.getByText("An API key is saved for this tab.", { exact: true })).toBeVisible();
    await expect(key).toHaveValue("");
    const failuresBeforeRemoval = rejected;
    await page.getByRole("button", { name: "Remove API key", exact: true }).click();
    await expect(page.getByText("Failed to load settings")).toBeVisible();
    await expect(page.getByText("API key removed.", { exact: true })).toBeVisible();
    expect(rejected).toBeGreaterThan(failuresBeforeRemoval);
    expect(await page.evaluate(() => sessionStorage.getItem("atlas.api-credential"))).toBeNull();
    await page.screenshot({ path: `/tmp/atlas-settings-${width}.png`, fullPage: true });
  });
}
