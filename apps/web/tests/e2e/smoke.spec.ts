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

  // Navigate through the app's own nav, like a user (page.goto would abort in-flight route prefetches).
  const nav = page.getByRole("navigation", { name: "Workspace" });
  for (const [link, heading] of [
    ["Simulations", "Simulation lab"],
    ["Dead letters", "Dead-letter queue"],
    ["Endpoints", "Endpoints"],
    ["Workspace", "Workspace"],
    ["Events", "Events"],
  ]) {
    await nav.getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
  }
  await page.goto("/app/simulations/new");
  await expect(page.getByRole("heading", { name: "New simulation", level: 1 })).toBeVisible();
  expect(errors).toEqual([]);
});
