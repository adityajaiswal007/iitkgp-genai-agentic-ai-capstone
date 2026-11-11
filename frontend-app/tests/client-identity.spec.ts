import { expect, test, type Route } from "@playwright/test";

const storageKey = "operations_copilot_client_id";
const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

test("persists a browser UUID across reloads and tabs and sends it on requests", async ({
  page,
  context,
  browser,
}) => {
  const identities: string[] = [];
  const handleList = async (route: Route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "content-type,x-client-id",
          "access-control-allow-methods": "GET,POST,OPTIONS",
        },
      });
      return;
    }
    identities.push(route.request().headers()["x-client-id"] ?? "");
    await route.fulfill({
      json: {
        data: [],
        pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 },
      },
      headers: { "access-control-allow-origin": "*" },
    });
  };
  await context.route("**/api/conversations**", handleList);
  const secondTab = await context.newPage();
  await Promise.all([page.goto("/"), secondTab.goto("/")]);
  await expect.poll(() => identities.length).toBeGreaterThanOrEqual(2);
  expect(identities[0]).toMatch(uuid);
  const identity = identities[0];
  expect(
    await page.evaluate((key) => localStorage.getItem(key), storageKey),
  ).toBe(identity);
  await page.reload();
  await expect.poll(() => identities.length).toBeGreaterThanOrEqual(3);
  expect(new Set(identities)).toEqual(new Set([identity]));
  const separateBrowser = await browser.newContext();
  try {
    await separateBrowser.route("**/api/conversations**", handleList);
    const separatePage = await separateBrowser.newPage();
    await separatePage.goto(page.url());
    await expect
      .poll(() => identities.some((id) => id !== identity))
      .toBe(true);
    const separateId = await separatePage.evaluate(
      (key) => localStorage.getItem(key),
      storageKey,
    );
    expect(separateId).toMatch(uuid);
    expect(separateId).not.toBe(identity);
  } finally {
    await separateBrowser.close();
  }
});

test("shows the server rate-limit message", async ({ page }) => {
  let requests = 0;
  await page.route("**/api/conversations**", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "x-client-id",
        },
      });
      return;
    }
    requests += 1;
    await route.fulfill({
      status: 429,
      json: {
        error: {
          code: "RATE_LIMITED",
          message: "Too many requests. Please try again in 42 seconds.",
          retryAfterSeconds: 42,
        },
      },
      headers: { "access-control-allow-origin": "*", "retry-after": "42" },
    });
  });
  await page.goto("/");
  await expect(
    page.getByText("Too many requests. Please try again in 42 seconds."),
  ).toBeVisible();
  // React Strict Mode may start an extra initial request in development.
  expect(requests).toBeLessThanOrEqual(2);
});
