import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

const backendUrl =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:3001/api";
const created = new Map<string, Set<string>>();
test.beforeEach(async ({ page }, testInfo) => {
  const ids = new Set<string>();
  created.set(testInfo.testId, ids);
  page.on("response", async (response) => {
    if (
      response.request().method() === "POST" &&
      response.url() === `${backendUrl}/conversations` &&
      response.ok()
    )
      ids.add((await response.json()).id);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Start a new conversation" }).click();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toBeEnabled();
});
test.afterEach(async ({ request }, testInfo) => {
  for (const id of created.get(testInfo.testId) ?? [])
    await request.delete(`${backendUrl}/conversations/${id}`, {
      headers: { "X-Client-ID": randomUUID() },
    });
  created.delete(testInfo.testId);
});

const csv = {
  name: "operations.csv",
  mimeType: "text/csv",
  buffer: Buffer.from("metric,value\nRevenue,42\n"),
};

test("select, remove, send, reload and download an attachment", async ({
  page,
}, testInfo) => {
  const picker = page.getByLabel("Choose attachments");
  await picker.setInputFiles([csv]);
  await expect(
    page.getByRole("button", { name: "Remove operations.csv" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Remove operations.csv" }).click();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  await picker.setInputFiles([csv]);
  const title = `Review this file ${randomUUID()}`;
  await page
    .getByRole("textbox", { name: "Message Operations Copilot" })
    .fill(title);
  const sendBounds = await page
    .getByRole("button", { name: "Send message", exact: true })
    .boundingBox();
  expect(sendBounds!.y + sendBounds!.height).toBeLessThanOrEqual(
    page.viewportSize()!.height,
  );
  await page.screenshot({
    path: testInfo.outputPath("selected-attachment.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  const link = page.getByRole("button", { name: "Download operations.csv" });
  await expect(link).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
    { timeout: 10000 },
  );
  await expect(
    page.getByRole("button", { name: "Remove operations.csv" }),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: title, exact: true }).click();
  await expect(link).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  const downloadRequest = page.waitForRequest((request) =>
    request.url().includes("/attachments/"),
  );
  await link.click();
  expect((await downloadRequest).headers()["x-client-id"]).toBe(
    await page.evaluate(() =>
      localStorage.getItem("operations_copilot_client_id"),
    ),
  );
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe(csv.name);
  expect(await readFile((await download.path())!)).toEqual(csv.buffer);
  await page.route("**/attachments/*", (route) =>
    route.fulfill({
      status: 429,
      headers: { "access-control-allow-origin": "*" },
      json: {
        error: {
          code: "RATE_LIMITED",
          message: "Too many requests. Please try again in 12 seconds.",
          retryAfterSeconds: 12,
        },
      },
    }),
  );
  await link.click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Too many requests." }),
  ).toHaveText("Too many requests. Please try again in 12 seconds.");
});

test("sends a file without text and fits the mobile composer", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByLabel("Choose attachments").setInputFiles([csv]);
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Download operations.csv" }),
  ).toBeVisible();
  await expect(page.getByLabel("Your message")).toContainText("Attached files");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("rejects unsupported and oversized files before sending", async ({
  page,
}) => {
  await page.getByLabel("Choose attachments").setInputFiles({
    name: "script.html",
    mimeType: "text/html",
    buffer: Buffer.from("<html>sample</html>"),
  });
  await expect(page.locator(".attachment-error")).toContainText("use PDF");
  await page.getByLabel("Choose attachments").setInputFiles({
    name: "large.txt",
    mimeType: "text/plain",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
  });
  await expect(page.locator(".attachment-error")).toContainText("5 MB");
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
});

test("retains selected attachments if the upload fails", async ({ page }) => {
  await page.route("**/api/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 503,
      json: { error: { message: "Upload temporarily unavailable." } },
      headers: { "access-control-allow-origin": "*" },
    });
  });
  await page.getByLabel("Choose attachments").setInputFiles([csv]);
  await page
    .getByRole("textbox", { name: "Message Operations Copilot" })
    .fill("Keep my draft");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Upload temporarily unavailable.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Remove operations.csv" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toHaveValue("Keep my draft");
});

test("uses an uploaded CSV to answer and retains it for a follow-up", async ({
  page,
}) => {
  const input = page.getByRole("textbox", {
    name: "Message Operations Copilot",
  });
  await page.getByLabel("Choose attachments").setInputFiles({
    name: "revenue-proof.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("metric,value\nRevenue,8472\n"),
  });
  await input.fill("What is the Revenue value in the attached CSV?");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  const replies = page.locator(".assistant-message .message-content");
  await expect(replies.first()).toContainText("8472", { timeout: 30000 });
  await expect(replies.first()).toContainText("upload:");
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
    { timeout: 30000 },
  );
  await input.fill("What was the Revenue value in my CSV again?");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(replies).toHaveCount(2, { timeout: 30000 });
  await expect(replies.last()).toContainText("8472", { timeout: 30000 });
  await expect(replies.last()).not.toContainText("not read or analysed");
});
