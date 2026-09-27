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

  test("disabled nav (if any) is never a dead link — always a reason, never an <a>", async ({ page }) => {
    await page.goto("/");
    const disabled = page.locator('[aria-disabled="true"]');
    const count = await disabled.count();

    // Invariant #1: a disabled item is never an <a> pointing at a 404.
    await expect(page.locator('a[aria-disabled="true"]')).toHaveCount(0);

    // Invariant #2: each disabled item explains WHY it is unavailable.
    // (All sections are currently built — count may be 0 — but IF a
    // disabled item exists it must carry a human reason.)
    for (let i = 0; i < count; i++) {
      const reason = await disabled.nth(i).getAttribute("title");
      expect(reason, "a disabled nav item must carry a human reason").toBeTruthy();
    }
  });
});
