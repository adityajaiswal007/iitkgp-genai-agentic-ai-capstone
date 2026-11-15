import { expect, test } from "@playwright/test";

const id = "29e30f04-5843-40c5-91d7-89daf752ce62";
test("shows conversation usage, blocks exhausted budgets, and recovers after the daily reset", async ({
  page,
}) => {
  let exhausted = true;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const conversation = {
    id,
    title: "Budget conversation",
    version: 1,
    createdAt: "2026-11-15T00:00:00Z",
    updatedAt: "2026-11-15T00:00:00Z",
    tokensCharged: 1200,
    estimatedTokens: 100,
  };
  const pagination = { page: 1, pageSize: 100, totalItems: 1, totalPages: 1 };
  await page.route("**/api/conversations**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith("/usage/daily"))
      body = {
        day: exhausted ? "2026-11-15" : "2026-11-16",
        timezone: "Asia/Kolkata",
        limit: 10000,
        used: exhausted ? 10000 : 0,
        estimated: exhausted ? 100 : 0,
        remaining: exhausted ? 0 : 10000,
      };
    else if (url.pathname.endsWith("/messages"))
      body = { data: [], pagination: { ...pagination, totalItems: 0 } };
    else if (url.pathname.endsWith(`/${id}`)) body = conversation;
    else body = { data: [conversation], pagination };
    await route.fulfill({ json: body });
  });
  await page.goto(`/?conversation=${id}`);
  await expect(
    page.getByText("Today: 10,000 / 10,000 tokens · 0 remaining"),
  ).toBeVisible();
  await expect(
    page.getByText("Conversation: 1,200 tokens (includes estimates)"),
  ).toBeVisible();
  await expect(
    page.getByText("Daily token allowance reached. Come back after the reset."),
  ).toBeVisible();
  const input = page.getByRole("textbox", {
    name: "Message Operations Copilot",
  });
  await expect(input).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  await page.screenshot({ path: 'test-results/token-budget-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/token-budget-mobile.png",
    fullPage: true,
  });
  exhausted = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByText("Today: 0 / 10,000 tokens · 10,000 remaining"),
  ).toBeVisible();
  await expect(input).toBeEnabled();
  await input.fill("Another question");
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeEnabled();
  expect(errors).toEqual([]);
});
