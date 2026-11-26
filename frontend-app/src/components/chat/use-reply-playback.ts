"use client";

import { useRef, useState } from "react";
import type { Conversation } from "@/lib/chat";

const TYPING_DELAY_MS = 1000;
const CHUNK_INTERVAL_MS = 45;
const WORDS_PER_CHUNK = 2;

type Playback = {
  preview: Conversation;
  phase: "waiting" | "streaming" | "stopped";
  streamingMessageId: string | null;
};

function delay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export function useReplyPlayback() {
  const [playback, setPlayback] = useState<Playback | null>(null);
  const startedAt = useRef(0);
  const savedConversation = useRef<Conversation | null>(null);

  function begin(
    active: Conversation | null,
    content: string,
    files: File[] = [],
  ) {
    startedAt.current = performance.now();
    savedConversation.current = null;
    const now = new Date().toISOString();
    const preview: Conversation = {
      id: crypto.randomUUID(),
      title: content.slice(0, 60),
      version: 0,
      createdAt: now,
      updatedAt: now,
      firstLoadedPage: 1,
      ...active,
      messages: [
        ...(active?.messages ?? []),
        {
          id: crypto.randomUUID(),
          role: "user",
          content,
          attachments: files.map((file) => ({
            id: "",
            name: file.name,
            size: file.size,
            mimeType: file.type || "application/octet-stream",
          })),
        },
      ],
    };
    setPlayback({ preview, phase: "waiting", streamingMessageId: null });
  }

  async function play(
    current: Conversation,
    assistantId: string,
    signal: AbortSignal,
  ) {
    const reply = current.messages.find(
      (message) => message.id === assistantId,
    );
    if (!reply)
      throw new Error(
        "The saved reply could not be loaded. Reopen the conversation to refresh it.",
      );
    savedConversation.current = current;
    setPlayback({
      preview: {
        ...current,
        messages: current.messages.filter(
          (message) => message.id !== assistantId,
        ),
      },
      phase: "waiting",
      streamingMessageId: null,
    });
    await delay(
      Math.max(0, TYPING_DELAY_MS - (performance.now() - startedAt.current)),
      signal,
    );
    const words = reply.content.match(/\s*\S+\s*/gu) ?? [reply.content];
    let visible = "";
    for (let index = 0; index < words.length; index += WORDS_PER_CHUNK) {
      signal.throwIfAborted();
      visible += words.slice(index, index + WORDS_PER_CHUNK).join("");
      const content = visible;
      setPlayback({
        preview: {
          ...current,
          messages: current.messages.map((message) =>
            message.id === assistantId ? { ...message, content } : message,
          ),
        },
        phase: "streaming",
        streamingMessageId: assistantId,
      });
      if (index + WORDS_PER_CHUNK < words.length)
        await delay(CHUNK_INTERVAL_MS, signal);
    }
  }

  function reset() {
    setPlayback(null);
    savedConversation.current = null;
  }

  function stop() {
    const saved = savedConversation.current;
    if (saved)
      setPlayback((current) =>
        current
          ? { ...current, phase: "stopped", streamingMessageId: null }
          : null,
      );
    else reset();
    return saved;
  }

  return {
    preview: playback?.preview ?? null,
    waiting: playback?.phase === "waiting",
    streamingMessageId: playback?.streamingMessageId ?? null,
    begin,
    play,
    reset,
    stop,
  };
}
