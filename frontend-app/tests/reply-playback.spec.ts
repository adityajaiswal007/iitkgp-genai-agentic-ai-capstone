import { expect, test, type Page } from "@playwright/test";

const replyText =
  "Here is a practical plan. ✅\n\n" +
  Array.from(
    { length: 60 },
    (_, index) => `Step ${index + 1}: gather evidence and review the result.\n`,
  ).join("");

async function mockChatApi(page: Page, failReply = false) {
  const conversation = {
    id: "4be64d86-a108-418d-87ec-7b33b8f65c58",
    title: "Typing test",
    version: 0,
    createdAt: "2026-11-04T12:00:00.000Z",
    updatedAt: "2026-11-04T12:00:00.000Z",
  };
  let created = false;
  let messages: { id: string; role: string; content: string }[] = [];
  let historyReadsAfterSend = 0;
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let body: unknown;
    let status = 200;
    if (method === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "content-type,x-client-id",
          "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
        },
      });
      return;
    }
    if (method === "DELETE") {
      created = false;
      messages = [];
      await route.fulfill({
        status: 204,
        headers: { "access-control-allow-origin": "*" },
      });
      return;
    }
    if (method === "POST" && path.endsWith("/messages")) {
      if (failReply) {
        status = 500;
        body = { error: { message: "Sample request failed." } };
      } else {
        const input = request.postDataJSON();
        const turn = [
          { id: crypto.randomUUID(), role: "user", content: input.content },
          { id: crypto.randomUUID(), role: "assistant", content: replyText },
        ];
        messages = [...messages, ...turn];
        conversation.version += 1;
        body = { conversation, messages: turn, mode: "agent" };
        status = 201;
      }
    } else if (method === "POST") {
      created = true;
      conversation.title = request.postDataJSON().title;
      body = conversation;
      status = 201;
    } else if (path.endsWith("/messages")) {
      body = {
        data: messages,
        pagination: {
          page: 1,
          pageSize: 100,
          totalItems: messages.length,
          totalPages: messages.length ? 1 : 0,
        },
      };
    } else if (path.endsWith("/conversations")) {
      if (messages.length) historyReadsAfterSend += 1;
      body = {
        data: created ? [conversation] : [],
        pagination: {
          page: 1,
          pageSize: 100,
          totalItems: created ? 1 : 0,
          totalPages: created ? 1 : 0,
        },
      };
    } else {
      body = conversation;
    }
    await route.fulfill({
      status,
      json: body,
      headers: { "access-control-allow-origin": "*" },
    });
  });
  await page.clock.install({ time: new Date("2026-11-04T12:00:00Z") });
  await page.goto("/");
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toBeEnabled();
  await page.clock.pauseAt(new Date("2026-11-04T12:01:00Z"));
  return { historyReads: () => historyReadsAfterSend };
}

async function send(page: Page) {
  const input = page.getByRole("textbox", {
    name: "Message Operations Copilot",
  });
  await input.fill("Help me make a plan");
  await input.press("Enter");
}

test("waits one second, reveals chunks, and opens saved history without replaying", async ({
  page,
}, testInfo) => {
  const api = await mockChatApi(page);
  await send(page);
  await expect(page.getByLabel("Your message")).toHaveText(
    "Help me make a plan",
  );
  await expect(
    page.getByRole("status", { name: "Operations Copilot is typing" }),
  ).toBeVisible();
  await expect.poll(api.historyReads).toBeGreaterThan(0);
  await page.clock.runFor(999);
  await expect(page.locator(".assistant-message")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("typing-indicator.png"),
    animations: "disabled",
  });
  await page.clock.runFor(100);
  const content = page.locator(".assistant-message .message-content");
  await expect(content).toContainText("Here is");
  expect((await content.innerText()).length).toBeLessThan(replyText.length);
  await expect(page.getByRole("button", { name: "Copy response" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Stop response" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("partial-reply.png"),
    animations: "disabled",
  });
  await page.clock.runFor(30000);
  await expect(content).toHaveText(replyText);
  await expect(
    page.getByRole("button", { name: "Copy response" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toHaveValue("");
  await page.reload();
  await expect(content).toHaveText(replyText);
  await expect(
    page.getByRole("status", { name: "Operations Copilot is typing" }),
  ).toHaveCount(0);
});

test("Stop freezes the visible reply while leaving the full saved reply intact", async ({
  page,
}) => {
  const api = await mockChatApi(page);
  await send(page);
  await expect.poll(api.historyReads).toBeGreaterThan(0);
  await page.clock.runFor(1150);
  const content = page.locator(".assistant-message .message-content");
  await expect(content).toContainText("Here is");
  await page.getByRole("button", { name: "Stop response" }).click();
  const stoppedText = await content.innerText();
  await page.clock.runFor(30000);
  await expect(content).toHaveText(stoppedText);
  expect(stoppedText.length).toBeLessThan(replyText.length);
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
  );
  await page.reload();
  await expect(content).toHaveText(replyText);
});

test("starting a new chat cancels the old reply timers", async ({ page }) => {
  const api = await mockChatApi(page);
  await send(page);
  await expect.poll(api.historyReads).toBeGreaterThan(0);
  await page.clock.runFor(1150);
  await expect(page.locator(".assistant-message")).toBeVisible();
  await page.getByRole("button", { name: "Start a new conversation" }).click();
  await page.clock.runFor(30000);
  await expect(
    page.getByRole("heading", { name: "What are we working on?" }),
  ).toBeVisible();
  await expect(page.locator(".assistant-message")).toHaveCount(0);
});

test("a failed request removes typing and preserves the draft", async ({
  page,
}) => {
  await mockChatApi(page, true);
  await send(page);
  await expect(page.getByText("Sample request failed.")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toHaveValue("Help me make a plan");
  await expect(
    page.getByRole("status", { name: "Operations Copilot is typing" }),
  ).toHaveCount(0);
  await expect(page.locator(".assistant-message")).toHaveCount(0);
});

test("keeps the previous answer visible while a follow-up types", async ({
  page,
}) => {
  const api = await mockChatApi(page);
  await send(page);
  await expect.poll(api.historyReads).toBeGreaterThan(0);
  await page.clock.runFor(30000);
  const contents = page.locator(".assistant-message .message-content");
  await expect(contents.first()).toHaveText(replyText);
  const previousReads = api.historyReads();
  await send(page);
  await expect.poll(api.historyReads).toBeGreaterThan(previousReads);
  await page.clock.runFor(999);
  await expect(contents).toHaveCount(1);
  await expect(contents.first()).toHaveText(replyText);
  await page.clock.runFor(100);
  await expect(contents).toHaveCount(2);
  await expect(contents.first()).toHaveText(replyText);
  expect((await contents.nth(1).innerText()).length).toBeLessThan(
    replyText.length,
  );
  await page.clock.runFor(30000);
  await expect(contents.nth(1)).toHaveText(replyText);
});

test("deleting the conversation cancels its active typing effect", async ({
  page,
}) => {
  const api = await mockChatApi(page);
  await send(page);
  await expect.poll(api.historyReads).toBeGreaterThan(0);
  await page.clock.runFor(1150);
  await expect(page.locator(".assistant-message")).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Help me make a plan" })
    .click();
  await page.clock.runFor(30000);
  await expect(
    page.getByRole("heading", { name: "What are we working on?" }),
  ).toBeVisible();
  await expect(page.locator(".assistant-message")).toHaveCount(0);
});
