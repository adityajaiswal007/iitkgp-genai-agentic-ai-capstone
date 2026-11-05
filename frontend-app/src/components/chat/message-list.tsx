"use client";

import { Check, Copy, Download, FileText, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { attachmentDownloadUrl } from "@/lib/chat-client";
import { fileSize } from "@/lib/attachments";
import type { Message } from "@/lib/chat";

function AssistantMessage({
  message,
  streaming,
}: {
  message: Message;
  streaming: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setError("");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy. Select the response text to copy it manually.");
    }
  }

  return (
    <article className="assistant-message">
      <div className="assistant-label">
        <span className="assistant-symbol">
          <Sparkles size={16} />
        </span>
        <strong>Operations Copilot</strong>
        <span className="demo-response-label">Demo response</span>
      </div>
      <div className="message-content">
        {message.content}
        {streaming && <span className="typing-cursor" aria-hidden="true" />}
      </div>
      {!streaming && (
        <button
          className="copy-button"
          onClick={copy}
          aria-label={copied ? "Response copied" : "Copy response"}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? "Copied" : "Copy"}
        </button>
      )}
      {error && (
        <p className="inline-notice" role="status">
          {error}
        </p>
      )}
    </article>
  );
}

export function MessageList({
  messages,
  conversationId,
  waiting,
  streamingMessageId,
}: {
  messages: Message[];
  conversationId: string;
  waiting: boolean;
  streamingMessageId: string | null;
}) {
  const end = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const lastMessageId = messages.at(-1)?.id;
  const lastContent = messages.at(-1)?.content;
  useEffect(() => {
    const scroll = end.current?.closest(".conversation-scroll");
    if (!scroll) return;
    const updateFollow = () => {
      follow.current =
        scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    };
    scroll.addEventListener("scroll", updateFollow, { passive: true });
    return () => scroll.removeEventListener("scroll", updateFollow);
  }, []);
  useEffect(() => {
    if (follow.current)
      end.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [lastMessageId, lastContent, waiting]);

  return (
    <div
      className="messages"
      role="log"
      aria-label="Conversation"
      aria-live="polite"
      aria-busy={waiting || Boolean(streamingMessageId)}
      aria-relevant="additions text"
    >
      {messages.map((message) =>
        message.role === "user" ? (
          <article
            className="user-message"
            aria-label="Your message"
            key={message.id}
          >
            {message.content}
            {Boolean(message.attachments?.length) && (
              <div className="attachment-list message-attachments">
                {message.attachments?.map((attachment, index) => {
                  const body = (
                    <>
                      <FileText size={17} />
                      <span>
                        <strong>{attachment.name}</strong>
                        <small>{fileSize(attachment.size)}</small>
                      </span>
                      {attachment.id && <Download size={14} />}
                    </>
                  );
                  return attachment.id ? (
                    <a
                      className="attachment-chip"
                      href={attachmentDownloadUrl(
                        conversationId,
                        attachment.id,
                      )}
                      download={attachment.name}
                      aria-label={`Download ${attachment.name}`}
                      key={attachment.id}
                    >
                      {body}
                    </a>
                  ) : (
                    <div className="attachment-chip" key={index}>
                      {body}
                    </div>
                  );
                })}
              </div>
            )}
          </article>
        ) : (
          <AssistantMessage
            message={message}
            streaming={message.id === streamingMessageId}
            key={message.id}
          />
        ),
      )}
      {waiting && (
        <div
          className="thinking"
          role="status"
          aria-label="Operations Copilot is typing"
        >
          <Sparkles size={16} />
          <span>
            Copilot is typing
            <span className="thinking-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </span>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
