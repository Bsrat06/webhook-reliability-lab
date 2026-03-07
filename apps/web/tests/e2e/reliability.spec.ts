import { expect, test, type Page } from "@playwright/test";

async function enterAsGuest(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);
}

async function configureReceiver(page: Page, mode: string, maxAttempts?: number) {
  await page.goto("/app/endpoints");
  const card = page.locator("article").first();
  await card.getByLabel("Responds with").selectOption(mode);
  if (maxAttempts !== undefined) await card.getByLabel("Max attempts").fill(String(maxAttempts));
  await card.getByRole("button", { name: "Save changes" }).click();
  await expect(card.getByText("Saved")).toBeVisible();
}

async function sendEvent(page: Page, idempotencyKey?: string) {
  await page.goto("/app/events");
  await page.getByRole("button", { name: "Send event" }).first().click();
  if (idempotencyKey) await page.getByLabel("Idempotency key (optional)").fill(idempotencyKey);
  await page.locator("form").getByRole("button", { name: "Send event" }).click();
  await expect(page).toHaveURL(/\/app\/events\/[0-9a-f-]{36}/);
  return page.url();
}

test("dead-lettered delivery is replayed with preserved lineage", async ({ page }) => {
  await enterAsGuest(page);
  await configureReceiver(page, "fail", 1);
  const eventUrl = await sendEvent(page);
  await expect(page.getByText("Dead-lettered: non-retryable failure").or(page.getByText(/Dead-lettered: retries exhausted/))).toBeVisible();

  await configureReceiver(page, "succeed");
  await page.goto("/app/dead-letters");
  await expect(page.getByRole("tab", { name: "Open 1" })).toBeVisible();
  await page.getByRole("button", { name: "order.created" }).click();
  const panel = page.getByRole("complementary", { name: "Dead-letter detail" });
  await expect(panel.getByText("HTTP 503")).toBeVisible();
  await panel.getByRole("button", { name: "Replay delivery" }).click();
  await expect(panel.getByText(/Resolved .*Replayed as delivery/)).toBeVisible();

  await page.goto(eventUrl);
  await expect(page.getByText(/Replay of delivery/)).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 }).locator("..").getByText("Delivered")).toBeVisible();
  // The original failed attempt is still in the history.
  await expect(page.getByRole("button", { name: /#1 .*HTTP 503/ })).toBeVisible();

  await page.goto("/app/workspace");
  await expect(page.getByText("delivery.replayed")).toBeVisible();
});

test("idempotent receiver suppresses a duplicate key", async ({ page }) => {
  await enterAsGuest(page);
  const key = `e2e-${Date.now()}`;
  await sendEvent(page, key);
  await expect(page.getByText("Processed by the receiver")).toBeVisible();
  await sendEvent(page, key);
  await expect(page.getByText("Duplicate suppressed")).toBeVisible();
});
