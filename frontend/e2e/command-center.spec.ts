import { test, expect } from "@playwright/test";

test.describe("Command Center (Home) is wired to real runtime state", () => {
  test("ATLAS status settles to READY against a clean, idle backend", async ({ page }) => {
    await page.goto("/");
    // The pill is derived ONLY from GET /runtime/status (deriveAtlasState). A
    // freshly booted, idle, kill-switch-off backend is honestly READY — this is
    // the un-fakeable status source, so a broken/lying pill fails here.
    await expect(
      page.locator(".health .health-head").getByText("READY"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("Cmd/Ctrl+K opens the command palette and Escape closes it", async ({ page }) => {
    await page.goto("/");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeHidden();

    await page.keyboard.press("ControlOrMeta+KeyK");
    await expect(palette).toBeVisible();
    await expect(palette.getByPlaceholder(/Search commands/i)).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
  });

  test("the Topbar launcher opens the palette via the global event", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /command palette/i }).click();
    await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  });
});
