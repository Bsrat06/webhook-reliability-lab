import { expect, test } from "@playwright/test";

test("guest resets, then deletes, their workspace", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);

  await page.getByRole("button", { name: "Send your first event" }).click();
  await page.locator("form").getByRole("button", { name: "Send event" }).click();
  await expect(page).toHaveURL(/\/app\/events\/[0-9a-f-]{36}/);

  await page.goto("/app/workspace");
  const confirm = page.getByLabel(/Type reset or delete to confirm/);
  await expect(page.getByRole("button", { name: "Reset workspace" })).toBeDisabled();
  await confirm.fill("reset");
  await page.getByRole("button", { name: "Reset workspace" }).click();
  await expect(page.getByText("Workspace reset. You can start fresh.")).toBeVisible();
  await expect(page.getByText("workspace.reset")).toBeVisible();

  await page.goto("/app/events");
  await expect(page.getByText("No events yet.")).toBeVisible();

  await page.goto("/app/workspace");
  await page.getByLabel(/Type reset or delete to confirm/).fill("delete");
  await page.getByRole("button", { name: "Delete workspace now" }).click();
  await expect(page.getByText("Your workspace and all of its data were deleted.")).toBeVisible();
  await page.goto("/app/events");
  await expect(page).toHaveURL(/session=expired/);
});
