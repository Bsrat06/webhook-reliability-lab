import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const PAGES = ["/", "/docs", "/app/simulations", "/app/simulations/new", "/app/events", "/app/dead-letters", "/app/endpoints", "/app/analytics", "/app/workspace"];

async function audit(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const serious = result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${path}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  // Nothing should force sideways scrolling of the whole page (tables scroll inside their own container).
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  return { serious, overflow };
}

test("core pages have no serious accessibility violations and no page-level horizontal scroll @mobile", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Try it now" }).click();
  await expect(page).toHaveURL(/\/app\/events/);
  const problems: string[] = [];
  for (const path of PAGES) {
    const { serious, overflow } = await audit(page, path);
    problems.push(...serious);
    if (overflow > 1) problems.push(`${path}: page scrolls horizontally by ${overflow}px`);
  }
  expect(problems).toEqual([]);
});

test("core flows work with the keyboard", async ({ page }) => {
  await page.goto("/");
  // Skip to the primary action with the keyboard only.
  await page.keyboard.press("Tab");
  for (let i = 0; i < 10 && !(await page.evaluate(() => document.activeElement?.textContent?.includes("Try it now"))); i++) {
    await page.keyboard.press("Tab");
  }
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/app\/events/);
  const focusVisible = await page.evaluate(() => {
    const el = document.querySelector("a[href='/app/simulations']") as HTMLElement;
    el.focus();
    return getComputedStyle(el).outlineStyle !== "none";
  });
  expect(focusVisible).toBe(true);
});
