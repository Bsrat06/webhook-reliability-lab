import { expect, test, type Page } from "@playwright/test";

async function runStress(page: Page, maxAttempts: number, seed: string) {
  await page.getByRole("radio", { name: /Retry policy stress test/ }).click();
  await page.getByLabel(/^Events/).fill("30");
  await page.getByLabel("Seed", { exact: true }).fill(seed);
  await page.getByLabel("Max attempts").fill(String(maxAttempts));
  await page.getByRole("button", { name: "Start simulation" }).click();
  await expect(page).toHaveURL(/\/app\/simulations\/[0-9a-f-]{36}$/);
  return page.url();
}

test("guest runs a seeded simulation, drills into a failure, reruns with a new policy", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);

  await page.getByRole("link", { name: "Simulations" }).click();
  await page.getByRole("link", { name: "New simulation" }).click();
  const first = await runStress(page, 2, "4242");

  // Progress is pushed over SSE through the web proxy, then the run finishes on its own.
  await expect(page.getByText(/Live \(server-sent events\)|Final results/)).toBeVisible();
  await expect(page.locator("header").getByText("Completed")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("30 of 30 deliveries finished")).toBeVisible();

  // Drill down: a dead-lettered event and its real attempt history.
  await page.getByRole("tab", { name: "Dead-lettered" }).click();
  await page.locator("section", { hasText: "Events in this run" }).locator("tbody tr").first().getByRole("link").click();
  await expect(page.getByText(/Dead-lettered: retries exhausted after 2 attempts/)).toBeVisible();
  await expect(page.getByRole("button", { name: /#1 .*HTTP 500/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /#2 .*HTTP 500/ })).toBeVisible();
  await expect(page.getByText(/from simulation/)).toBeVisible();

  // Same seed, more attempts.
  await page.goto(first);
  await page.getByRole("link", { name: "Rerun with changes" }).click();
  await expect(page.getByRole("heading", { name: "Rerun simulation" })).toBeVisible();
  await expect(page.getByLabel("Seed", { exact: true })).toHaveValue("4242");
  await page.getByLabel("Max attempts").fill("8");
  await page.getByRole("button", { name: "Start simulation" }).click();
  await expect(page.locator("header").getByText("Completed")).toBeVisible({ timeout: 90_000 });

  await page.getByRole("link", { name: "Simulations" }).first().click();
  await expect(page.locator("tbody tr")).toHaveCount(2);
});
