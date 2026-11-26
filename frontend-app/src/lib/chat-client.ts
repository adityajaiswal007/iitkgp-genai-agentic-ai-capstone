import type {
  Attachment,
  DailyUsage,
  Conversation,
  ConversationSummary,
  Message,
  Page,
} from "./chat";
import { browserClientId } from "./client-identity";

const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:3001/api"
).replace(/\/$/, "");

export class ChatApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
  }
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid API response.");
  return value as Record<string, unknown>;
}
function conversation(value: unknown): ConversationSummary {
  const item = object(value);
  if (
    typeof item.id !== "string" ||
    typeof item.title !== "string" ||
    !Number.isInteger(item.version) ||
    typeof item.createdAt !== "string" ||
    typeof item.updatedAt !== "string"
  )
    throw new Error("Invalid conversation response.");
  for (const key of ["tokensCharged", "estimatedTokens"]) {
    if (
      item[key] != null &&
      (!Number.isSafeInteger(item[key]) || Number(item[key]) < 0)
    )
      throw new Error("Invalid token usage response.");
  }
  return item as ConversationSummary;
}
function attachment(value: unknown): Attachment {
  const item = object(value);
  if (
    typeof item.id !== "string" ||
    typeof item.name !== "string" ||
    typeof item.mimeType !== "string" ||
    !Number.isInteger(item.size) ||
    Number(item.size) <= 0
  )
    throw new Error("Invalid attachment response.");
  return item as Attachment;
}
function message(value: unknown): Message {
  const item = object(value);
  if (
    typeof item.id !== "string" ||
    typeof item.content !== "string" ||
    !["user", "assistant"].includes(String(item.role))
  )
    throw new Error("Invalid message response.");
  if (item.attachments !== undefined && !Array.isArray(item.attachments))
    throw new Error("Invalid attachments response.");
  return {
    ...item,
    attachments: Array.isArray(item.attachments)
      ? item.attachments.map(attachment)
      : [],
  } as Message;
}
function page<T>(value: unknown, parse: (item: unknown) => T): Page<T> {
  const item = object(value);
  const pagination = object(item.pagination);
  if (
    !Array.isArray(item.data) ||
    ![
      pagination.page,
      pagination.pageSize,
      pagination.totalItems,
      pagination.totalPages,
    ].every((number) => Number.isInteger(number) && Number(number) >= 0)
  )
    throw new Error("Invalid pagination response.");
  return {
    data: item.data.map(parse),
    pagination: pagination as Page<T>["pagination"],
  };
}
async function fetchResponse(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);
  headers.set("X-Client-ID", await browserClientId());
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      cache: "no-store",
      headers,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error(
      "Cannot reach the chat server. Check that the backend, PostgreSQL, and Redis are running.",
    );
  }
  if (!response.ok) {
    const body: unknown = await response.json();
    const error = object(object(body).error);
    throw new ChatApiError(
      typeof error.message === "string" ? error.message : "The request failed.",
      response.status,
      typeof error.retryAfterSeconds === "number" &&
        Number.isFinite(error.retryAfterSeconds)
        ? error.retryAfterSeconds
        : undefined,
    );
  }
  return response;
}
async function request<T>(
  path: string,
  parse: (value: unknown) => T,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetchResponse(path, options);
  const body: unknown = response.status === 204 ? null : await response.json();
  return parse(body);
}

export async function downloadAttachment(
  conversationId: string,
  attachment: Attachment,
) {
  const path = `/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(attachment.id)}`;
  const response = await fetchResponse(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = attachment.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const chatApi = {
  dailyUsage: (signal?: AbortSignal) =>
    request(
      "/conversations/usage/daily",
      (value) => {
        const item = object(value);
        if (
          typeof item.day !== "string" ||
          typeof item.timezone !== "string" ||
          !["limit", "used", "estimated", "remaining"].every(
            (key) => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0,
          )
        )
          throw new Error("Invalid daily usage response.");
        return item as DailyUsage;
      },
      { signal },
    ),
  list: (pageNumber = 1, search = "", signal?: AbortSignal) =>
    request(
      `/conversations?page=${pageNumber}&pageSize=100&search=${encodeURIComponent(search)}`,
      (value) => page(value, conversation),
      { signal },
    ),
  create: (title: string, signal?: AbortSignal) =>
    request("/conversations", conversation, {
      method: "POST",
      body: JSON.stringify({ title }),
      signal,
    }),
  rename: (id: string, title: string) =>
    request(`/conversations/${id}`, conversation, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  remove: (id: string) =>
    request(`/conversations/${id}`, () => undefined, { method: "DELETE" }),
  messages: (id: string, pageNumber: number, signal?: AbortSignal) =>
    request(
      `/conversations/${id}/messages?page=${pageNumber}&pageSize=100`,
      (value) => page(value, message),
      { signal },
    ),
  async get(id: string, signal?: AbortSignal): Promise<Conversation> {
    let messages = await this.messages(id, 1, signal);
    const lastPage = Math.max(1, messages.pagination.totalPages);
    if (lastPage > 1) messages = await this.messages(id, lastPage, signal);
    const metadata = await request(`/conversations/${id}`, conversation, {
      signal,
    });
    return { ...metadata, messages: messages.data, firstLoadedPage: lastPage };
  },
  send: (
    id: string,
    content: string,
    expectedVersion: number,
    requestId: string,
    signal?: AbortSignal,
    files: File[] = [],
  ) => {
    let body: string | FormData = JSON.stringify({
      content,
      expectedVersion,
      requestId,
    });
    if (files.length) {
      body = new FormData();
      body.append("content", content);
      body.append("expectedVersion", String(expectedVersion));
      body.append("requestId", requestId);
      for (const file of files) body.append("files", file);
    }
    return request(
      `/conversations/${id}/messages`,
      (value) => {
        const body = object(value);
        if (
          !Array.isArray(body.messages) ||
          !["agent", "qwen", "demo"].includes(String(body.mode))
        )
          throw new Error("Invalid chat turn response.");
        return {
          conversation: conversation(body.conversation),
          messages: body.messages.map(message),
          mode: body.mode,
        };
      },
      {
        method: "POST",
        body,
        signal,
      },
    );
  },
};
