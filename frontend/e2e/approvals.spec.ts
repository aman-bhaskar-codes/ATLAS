import { test, expect } from "@playwright/test";

test.describe("approvals reflect durable backend state", () => {
  test("Home Approval Inbox shows Inbox Zero when nothing is pending", async ({ page }) => {
    await page.goto("/");
    // A clean runtime has no pending guarded actions; the durable inbox stays empty.
    await expect(page.getByText("Inbox Zero")).toBeVisible({ timeout: 15_000 });
  });

  test("/approvals renders the shell without error", async ({ page }) => {
    const response = await page.goto("/approvals");
    expect(response?.status() ?? 500).toBeLessThan(400);
    await expect(page.locator("header.topbar")).toBeVisible();
    await expect(page.getByText("mail.send", { exact: true })).toHaveCount(0);
    await expect(page.getByText("calendar.delete_event", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Yesterday · Tier 2", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "audit log", exact: true })).toBeVisible();
  });
});
