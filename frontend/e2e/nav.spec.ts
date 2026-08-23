import { test, expect } from "@playwright/test";

test.describe("navigation is Zero-Dead-UI", () => {
  test("an enabled nav item navigates to a real route", async ({ page }) => {
    await page.goto("/");
    // Sidebar "Tasks" is the only link with that accessible name (MobileNav has
    // none), so this uniquely targets the real, navigable sidebar entry.
    await page.getByRole("link", { name: "Tasks", exact: true }).click();
    await expect(page).toHaveURL(/\/tasks$/);
    await expect(page.locator("header.topbar")).toBeVisible();
  });

  test("unbuilt sections are disabled with a visible reason, never dead links", async ({ page }) => {
    await page.goto("/");
    const disabled = page.locator('[aria-disabled="true"]');
    // Knowledge, Research, Workspaces are deliberately disabled this pass.
    const count = await disabled.count();
    expect(count).toBeGreaterThan(0);

    // Invariant #1: a disabled item is never an <a> pointing at a 404.
    expect(await page.locator('a[aria-disabled="true"]').count()).toBe(0);

    // Invariant #2: each disabled item explains WHY it is unavailable.
    for (let i = 0; i < count; i++) {
      const reason = await disabled.nth(i).getAttribute("title");
      expect(reason, "a disabled nav item must carry a human reason").toBeTruthy();
    }
  });
});
