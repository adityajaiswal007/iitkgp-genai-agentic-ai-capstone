import { expect, test, type Page } from "@playwright/test";

const olderId = "11111111-1111-4111-8111-111111111111";
const newerId = "22222222-2222-4222-8222-222222222222";
const createdId = "33333333-3333-4333-8333-333333333333";

async function mockChats(
  page: Page,
  options: { missing?: boolean; hideOlder?: boolean; holdReply?: boolean } = {},
) {
  const makeChat = (id: string, title: string) => ({
    id,
    title,
    version: 0,
    createdAt: "2026-11-10T12:00:00Z",
    updatedAt: "2026-11-10T12:00:00Z",
  });
  const chats = [
    makeChat(newerId, "Newer chat"),
    makeChat(olderId, "Older chat"),
  ];
  if (options.missing) chats.pop();
  const messages = new Map(
    chats.map((chat) => [
      chat.id,
      [
        {
          id: `${chat.id}-user`,
          role: "user",
          content: `Question in ${chat.title}`,
        },
        {
          id: `${chat.id}-reply`,
          role: "assistant",
          content: `Saved answer in ${chat.title}`,
        },
      ],
    ]),
  );
  let releaseReply = () => {};
  const replyGate = new Promise<void>((resolve) => {
    releaseReply = resolve;
  });
  const paginated = (data: unknown[]) => ({
    data,
    pagination: {
      page: 1,
      pageSize: 100,
      totalItems: data.length,
      totalPages: data.length ? 1 : 0,
    },
  });
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const method = request.method();
    const parts = new URL(request.url()).pathname.split("/");
    const id = parts[3];
    const chat = chats.find((item) => item.id === id);
    const headers = {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "content-type,x-client-id",
      "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
    };
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (method === "POST" && !id) {
      const created = makeChat(createdId, request.postDataJSON().title);
      chats.unshift(created);
      messages.set(created.id, []);
      return route.fulfill({ status: 201, json: created, headers });
    }
    if (method === "GET" && !id)
      return route.fulfill({
        json: paginated(
          chats.filter((item) => !options.hideOlder || item.id !== olderId),
        ),
        headers,
      });
    if (!chat)
      return route.fulfill({
        status: 404,
        json: { error: { message: "Conversation not found" } },
        headers,
      });
    if (method === "DELETE") {
      chats.splice(chats.indexOf(chat), 1);
      return route.fulfill({ status: 204, headers });
    }
    if (method === "POST") {
      if (options.holdReply) await replyGate;
      const turn = [
        {
          id: "new-user",
          role: "user",
          content: request.postDataJSON().content,
        },
        { id: "new-assistant", role: "assistant", content: "New saved answer" },
      ];
      messages.set(id, turn);
      chat.version++;
      return route.fulfill({
        status: 201,
        json: { conversation: chat, messages: turn, mode: "agent" },
        headers,
      });
    }
    return route.fulfill({
      json: parts[4] === "messages" ? paginated(messages.get(id) ?? []) : chat,
      headers,
    });
  });
  return { releaseReply };
}

const activeChat = (page: Page) =>
  page.locator('.desktop-sidebar .history-link[aria-current="page"]');
const selectedId = (page: Page) =>
  new URL(page.url()).searchParams.get("conversation");

test("a direct link and reload restore the older selected chat", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mockChats(page);
  await page.goto(`/?conversation=${olderId}`);
  await expect(
    page.getByText("Saved answer in Older chat", { exact: true }),
  ).toBeVisible();
  await expect(activeChat(page)).toHaveText("Older chat");
  await page.reload();
  await expect(
    page.getByText("Saved answer in Older chat", { exact: true }),
  ).toBeVisible();
  expect(selectedId(page)).toBe(olderId);
  expect(errors).toEqual([]);
});

test("sidebar selection, back and forward follow the URL", async ({ page }) => {
  await mockChats(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Older chat", exact: true }).click();
  await expect.poll(() => selectedId(page)).toBe(olderId);
  await page.getByRole("button", { name: "Newer chat", exact: true }).click();
  await expect.poll(() => selectedId(page)).toBe(newerId);
  await page.goBack();
  await expect(activeChat(page)).toHaveText("Older chat");
  await page.goForward();
  await expect(activeChat(page)).toHaveText("Newer chat");
});

test("new chat clears the selection and stays new after refresh", async ({
  page,
}) => {
  await mockChats(page);
  await page.goto(`/?conversation=${olderId}&view=compact#chat`);
  await expect(activeChat(page)).toHaveText("Older chat");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  expect(selectedId(page)).toBeNull();
  expect(new URL(page.url()).searchParams.get("view")).toBe("compact");
  expect(new URL(page.url()).hash).toBe("#chat");
  await page.reload();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toBeEnabled();
  await expect(activeChat(page)).toHaveCount(0);
  await expect(page.locator(".assistant-message")).toHaveCount(0);
});

test("new conversation gets its URL before inference finishes", async ({
  page,
}) => {
  const api = await mockChats(page, { holdReply: true });
  await page.goto("/");
  await page.getByRole("button", { name: "Start a new conversation" }).click();
  await page
    .getByRole("textbox", { name: "Message Operations Copilot" })
    .fill("Start a saved chat");
  const sent = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/messages"),
  );
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await sent;
  try {
    await expect.poll(() => selectedId(page)).toBe(createdId);
  } finally {
    api.releaseReply();
  }
  await expect(
    page.getByText("New saved answer", { exact: true }),
  ).toBeVisible();
});

test("a linked chat can load even when absent from the sidebar page", async ({
  page,
}) => {
  await mockChats(page, { hideOlder: true });
  await page.goto(`/?conversation=${olderId}`);
  await expect(
    page.getByText("Saved answer in Older chat", { exact: true }),
  ).toBeVisible();
  expect(selectedId(page)).toBe(olderId);
});

test("missing and deleted chats clear the URL without opening another chat", async ({
  page,
}) => {
  await mockChats(page, { missing: true });
  await page.goto(`/?conversation=${olderId}`);
  await expect.poll(() => selectedId(page)).toBeNull();
  await expect(page.getByRole("status")).toContainText(
    "Conversation not found",
  );
  await expect(page.locator(".assistant-message")).toHaveCount(0);
  await page.getByRole("button", { name: "Newer chat", exact: true }).click();
  await expect(activeChat(page)).toHaveText("Newer chat");
  await page
    .getByRole("button", { name: "Delete Newer chat", exact: true })
    .click();
  await expect.poll(() => selectedId(page)).toBeNull();
  await page.reload();
  await expect(
    page.getByRole("textbox", { name: "Message Operations Copilot" }),
  ).toBeEnabled();
});

test("invalid conversation IDs do not become API paths", async ({ page }) => {
  await mockChats(page);
  const paths: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) paths.push(request.url());
  });
  await page.goto("/?conversation=..%2Fother");
  await expect.poll(() => selectedId(page)).toBeNull();
  await expect(page.getByRole("status")).toContainText(
    "Invalid conversation link",
  );
  expect(paths.some((path) => path.includes("/other"))).toBe(false);
});
