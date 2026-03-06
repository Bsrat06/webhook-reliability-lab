import { expect, test } from "@playwright/test";

test("every page renders without client errors @mobile", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Try it now" })).toBeVisible();
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);
  await expect(page.getByRole("heading", { name: "Events", level: 1 })).toBeVisible();

  for (const [path, heading] of [
    ["/app/endpoints", "Endpoints"],
    ["/app/workspace", "Workspace"],
    ["/app/events", "Events"],
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
  }
  expect(errors).toEqual([]);
});
