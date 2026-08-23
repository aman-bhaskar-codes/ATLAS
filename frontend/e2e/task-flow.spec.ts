import { test, expect } from "@playwright/test";

test("creating a task from the palette hits the real backend and routes to its run page", async ({
  page,
}) => {
  await page.goto("/");

  // Open the palette via the documented global event (the same one the Topbar
  // launcher dispatches) — a deterministic path independent of OS key mapping.
  await page.evaluate(() =>
    window.dispatchEvent(new CustomEvent("atlas:open-command-palette")),
  );
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await expect(palette).toBeVisible();

  const input = palette.getByPlaceholder(/Search commands/i);
  await input.fill("summarize the ATLAS safety model in three bullets");

  // With text present, the first palette item is a real "Create task" action.
  await expect(palette.getByText(/Create task:/)).toBeVisible();
  await input.press("Enter");

  // POST /tasks returns 202 with a task id; the client routes to /tasks/{id}.
  // The negative lookahead keeps /tasks/live from satisfying the match.
  await expect(page).toHaveURL(/\/tasks\/(?!live$)[^/]+$/, { timeout: 20_000 });
  await expect(page.locator("header.topbar")).toBeVisible();
});
