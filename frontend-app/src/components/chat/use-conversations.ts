"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation, ConversationSummary } from "@/lib/chat";
import { ChatApiError, chatApi } from "@/lib/chat-client";
import { useReplyPlayback } from "./use-reply-playback";

export function useConversations() {
  const playback = useReplyPlayback();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [search, setSearch] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const navigation = useRef(0);
  const searchRef = useRef("");
  const historyRequest = useRef(0);
  const pendingTurn = useRef<{
    id: string;
    content: string;
    requestId: string;
    version: number;
    fileKey: string;
  } | null>(null);

  const reportError = (error: unknown) =>
    setNotice(
      error instanceof Error
        ? error.message
        : "The chat request failed. Please try again.",
    );
  const refreshHistory = useCallback(
    async (signal?: AbortSignal, query = "") => {
      const requestId = ++historyRequest.current;
      const result = await chatApi.list(1, query, signal);
      if (requestId !== historyRequest.current || query !== searchRef.current)
        return result.data;
      setConversations(result.data);
      setHistoryPage(1);
      setHasMore(result.pagination.totalPages > 1);
      return result.data;
    },
    [],
  );

  useEffect(() => {
    const request = new AbortController();
    const selectedAtStart = navigation.current;
    async function load() {
      try {
        const saved = await refreshHistory(request.signal);
        if (saved[0] && navigation.current === selectedAtStart) {
          const current = await chatApi.get(saved[0].id, request.signal);
          if (navigation.current === selectedAtStart) setActive(current);
        }
        setReady(true);
      } catch (error) {
        if (!request.signal.aborted) reportError(error);
      }
    }
    void load();
    return () => {
      request.abort();
      controller.current?.abort();
    };
  }, [refreshHistory]);

  useEffect(() => {
    if (!ready) return;
    const request = new AbortController();
    const timer = setTimeout(() => {
      setLoadingHistory(true);
      void refreshHistory(request.signal, searchRef.current)
        .catch((error: unknown) => {
          if (!request.signal.aborted) reportError(error);
        })
        .finally(() => {
          if (!request.signal.aborted) setLoadingHistory(false);
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      request.abort();
    };
  }, [search, ready, refreshHistory]);

  function abortRequest(clearPlayback = true) {
    navigation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    if (clearPlayback) playback.reset();
  }
  function cancel() {
    const saved = playback.stop();
    abortRequest(false);
    if (saved) setActive(saved);
    setNotice(
      saved
        ? "Response stopped. Reopen this conversation to see the full saved reply."
        : "Stopped waiting. A request already accepted by the server may still be saved. Reopen the conversation to refresh it.",
    );
    void refreshHistory(undefined, searchRef.current).catch(reportError);
  }
  function newChat() {
    abortRequest();
    pendingTurn.current = null;
    setActive(null);
    setNotice("");
  }
  async function selectChat(id: string) {
    abortRequest();
    pendingTurn.current = null;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setNotice("");
    try {
      const current = await chatApi.get(id, request.signal);
      if (!request.signal.aborted) setActive(current);
    } catch (error) {
      if (!request.signal.aborted) reportError(error);
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
      }
    }
  }
  async function deleteChat(id: string) {
    try {
      await chatApi.remove(id);
      if (active?.id === id || playback.preview?.id === id) newChat();
      setConversations((previous) => previous.filter((chat) => chat.id !== id));
    } catch (error) {
      reportError(error);
    }
  }
  async function loadMore() {
    if (loadingHistory || !hasMore) return;
    setLoadingHistory(true);
    try {
      const result = await chatApi.list(historyPage + 1, search);
      if (search !== searchRef.current) return;
      setConversations((previous) => [
        ...previous,
        ...result.data.filter(
          (row) => !previous.some((chat) => chat.id === row.id),
        ),
      ]);
      setHistoryPage(result.pagination.page);
      setHasMore(result.pagination.page < result.pagination.totalPages);
    } catch (error) {
      reportError(error);
    } finally {
      setLoadingHistory(false);
    }
  }
  async function loadEarlier() {
    if (!active || active.firstLoadedPage <= 1 || busy) return;
    const id = active.id;
    setBusy(true);
    try {
      const previous = await chatApi.messages(id, active.firstLoadedPage - 1);
      setActive((current) =>
        current?.id === id
          ? {
              ...current,
              messages: [...previous.data, ...current.messages],
              firstLoadedPage: previous.pagination.page,
            }
          : current,
      );
    } catch (error) {
      reportError(error);
    } finally {
      setBusy(false);
    }
  }
  async function send(content: string, files: File[] = []): Promise<boolean> {
    const text = content.trim();
    if (
      (!text && !files.length) ||
      text.length > 8000 ||
      controller.current ||
      busy ||
      !ready
    )
      return false;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setNotice("");
    playback.begin(active, text || "Attached files", files);
    const fileKey = files
      .map((file) => `${file.name}:${file.size}:${file.lastModified}`)
      .join("\0");
    let saved: Conversation | null = null;
    try {
      let target = active;
      const retry = pendingTurn.current;
      if (!target && retry && retry.content === text)
        target = await chatApi.get(retry.id, request.signal);
      if (!target) {
        const created = await chatApi.create(
          (text || files[0].name).slice(0, 60),
          request.signal,
        );
        target = { ...created, messages: [], firstLoadedPage: 1 };
      }
      const turn =
        retry?.id === target.id &&
        retry.content === text &&
        retry.fileKey === fileKey
          ? retry
          : {
              id: target.id,
              content: text,
              requestId: crypto.randomUUID(),
              version: target.version,
              fileKey,
            };
      pendingTurn.current = turn;
      const reply = await chatApi.send(
        target.id,
        text,
        turn.version,
        turn.requestId,
        request.signal,
        files,
      );
      const current = await chatApi.get(reply.conversation.id, request.signal);
      if (request.signal.aborted) return false;
      await refreshHistory(request.signal, searchRef.current);
      if (request.signal.aborted) return false;
      const assistant = reply.messages.find(
        (message) => message.role === "assistant",
      );
      if (!assistant)
        throw new Error("The server did not return an assistant reply.");
      saved = current;
      pendingTurn.current = null;
      await playback.play(current, assistant.id, request.signal);
      request.signal.throwIfAborted();
      setActive(current);
      playback.reset();
      return true;
    } catch (error) {
      if (!request.signal.aborted) {
        playback.reset();
        if (error instanceof ChatApiError && error.status === 409) {
          pendingTurn.current = null;
          if (active) void selectChat(active.id);
        }
        reportError(error);
      }
      return request.signal.aborted && saved !== null;
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
      }
    }
  }
  async function retryLoad() {
    try {
      await refreshHistory();
      setNotice("");
      setReady(true);
    } catch (error) {
      reportError(error);
    }
  }
  return {
    conversations,
    active: playback.preview ?? active,
    waiting: playback.waiting,
    streamingMessageId: playback.streamingMessageId,
    activeId: active?.id ?? null,
    ready,
    busy,
    notice,
    newChat,
    selectChat,
    deleteChat,
    send,
    cancel,
    hasMore,
    loadingHistory,
    search,
    setSearch: (value: string) => {
      searchRef.current = value;
      setSearch(value);
    },
    loadMore,
    loadEarlier,
    retryLoad,
  };
}
