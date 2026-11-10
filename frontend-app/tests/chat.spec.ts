import { expect, test } from "@playwright/test";
const backendUrl =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:3001/api";
let createdIds: string[] = [];
test.beforeEach(async ({ page }) => {
  createdIds = [];
  page.on("response", async (response) => {
    if (
      response.request().method() === "POST" &&
      response.url() === `${backendUrl}/conversations` &&
      response.ok()
    ) {
      createdIds.push((await response.json()).id);
    }
  });
});
test.afterEach(async ({ request }) => {
  for (const id of createdIds)
    await request.delete(`${backendUrl}/conversations/${id}`);
});

test("send, reload, switch, and delete a conversation", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Start a new conversation" }).click();
  await expect(
    page.getByRole("heading", { name: "What are we working on?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "Message Operations Copilot" })
    .fill("Help me plan a vendor review");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Assistant response", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toBeFocused();
  await page.reload();
  await expect(page.getByText("Assistant response", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "What are we working on?" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Help me plan a vendor review", exact: true })
    .click();
  await expect(page.getByText("Assistant response", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Help me plan a vendor review" })
    .click();
  await expect(
    page.getByRole("heading", { name: "What are we working on?" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("mobile navigation and keyboard composition work", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto("/");
  await page.getByRole("button", { name: "Start a new conversation" }).click();
  await page.getByRole("button", { name: "Open sidebar" }).click();
  await expect(
    page.getByRole("navigation", { name: "Chat history" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  const input = page.getByRole("textbox", {
    name: "Message Operations Copilot",
  });
  await input.fill("Summarize a document");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("Summarize a document\n");
  await input.press("Enter");
  await expect(page.getByText("Assistant response", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
