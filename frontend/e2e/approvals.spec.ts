import { test, expect } from "@playwright/test";

test.describe("approvals reflect real backend state (storage deferred)", () => {
  test("Home Approval Inbox shows Inbox Zero when nothing is pending", async ({ page }) => {
    await page.goto("/");
    // GET /approvals/pending returns [] today (approval storage is a documented
    // deferral), so the inbox is honestly empty rather than faking rows.
    await expect(page.getByText("Inbox Zero")).toBeVisible({ timeout: 15_000 });
  });

  test("/approvals renders the shell without error", async ({ page }) => {
    const response = await page.goto("/approvals");
    expect(response?.status() ?? 500).toBeLessThan(400);
    await expect(page.locator("header.topbar")).toBeVisible();
  });
});
