import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function runHealthy(page: Page, maxAttempts?: number) {
  await page.getByRole("radio", { name: /Healthy endpoint/ }).click();
  await page.getByLabel(/^Events/).fill("20");
  await page.getByLabel("Seed", { exact: true }).fill("7");
  if (maxAttempts) await page.getByLabel("Max attempts").fill(String(maxAttempts));
  await page.getByRole("button", { name: "Start simulation" }).click();
  await expect(page.locator("header").getByText("Completed")).toBeVisible({ timeout: 90_000 });
  return page.url();
}

test("compare, analytics, export, refresh, isolation, quotas and docs", async ({ page, browser }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);
  const nav = page.getByRole("navigation", { name: "Workspace" });

  // Two runs that differ only in the retry policy, then a comparison.
  await page.goto("/app/simulations/new");
  const firstRun = await runHealthy(page);
  await page.getByRole("link", { name: "Rerun with changes" }).click();
  await page.getByLabel("Max attempts").fill("3");
  await page.getByRole("button", { name: "Start simulation" }).click();
  await expect(page.locator("header").getByText("Completed")).toBeVisible({ timeout: 90_000 });

  await nav.getByRole("link", { name: "Simulations" }).click();
  const boxes = page.getByRole("checkbox", { name: /for comparison/ });
  await expect(boxes).toHaveCount(2);
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByRole("button", { name: "Compare selected (2)" }).click();
  await expect(page.getByRole("heading", { name: "Compare 2 runs" })).toBeVisible();
  await expect(page.getByText("Same seed and receiver behavior")).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "retry_policy.max_attempts" })).toBeVisible();
  await expect(page.getByRole("row", { name: /^Success rate/ })).toContainText("100%");

  // Analytics come from the 40 recorded deliveries.
  await nav.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  const stats = page.locator("dl").first();
  await expect(stats).toContainText("Deliveries40");
  await expect(stats).toContainText("Success rate100%");

  // Export downloads a real CSV of the events.
  await nav.getByRole("link", { name: "Workspace" }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "CSV export of Events" }).click()]);
  const csv = await readFile((await download.path())!, "utf8");
  expect(csv.split("\n")[0]).toContain("id,event_type,source");
  expect(csv.trim().split("\n").length).toBe(41);

  // Refresh keeps the session and workspace.
  const wsId = await page.locator("dd").first().textContent();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Workspace", level: 1 })).toBeVisible();
  await expect(page.locator("dd").first()).toHaveText(wsId!);

  // Quotas are enforced by the server and explained.
  await nav.getByRole("link", { name: "Endpoints" }).click();
  for (let i = 0; i < 4; i++) {
    await page.getByRole("button", { name: "New built-in receiver" }).click();
    await expect(page.locator("article")).toHaveCount(i + 2);
  }
  await page.getByRole("button", { name: "New built-in receiver" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Endpoint limit reached (5)" })).toBeVisible();

  // A second guest cannot see the first guest's run.
  const other = await browser.newContext();
  const page2 = await other.newPage();
  await page2.goto("/");
  await page2.getByRole("button", { name: "Try it now" }).click();
  await expect(page2).toHaveURL(/\/app\/events/);
  await page2.goto(firstRun);
  await expect(page2.getByText("This simulation does not exist in your workspace.")).toBeVisible();
  await page2.goto("/app/simulations");
  await expect(page2.getByRole("heading", { name: "Start with a scenario" })).toBeVisible();
  await other.close();

  await page.goto("/docs");
  await expect(page.getByRole("heading", { name: "Delivery states" })).toBeVisible();
});
