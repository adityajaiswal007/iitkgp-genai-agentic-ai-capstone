"use client";

import { MessageSquare, Plus, Search, Sparkles, Trash2 } from "lucide-react";
import type { ConversationSummary } from "@/lib/chat";

type Props = {
  conversations: ConversationSummary[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  search: string;
  onSearch: (value: string) => void;
  hasMore: boolean;
  loadingHistory: boolean;
  onLoadMore: () => void;
};

export function Sidebar({
  conversations,
  activeId,
  onNew,
  onSelect,
  onDelete,
  search,
  onSearch,
  hasMore,
  loadingHistory,
  onLoadMore,
}: Props) {
  const filtered = conversations.filter((chat) =>
    chat.title.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="sidebar-content">
      <div className="brand">
        <span className="brand-symbol">
          <Sparkles size={19} />
        </span>
        <span>
          RBI/SEBI{" "}<span className="brand-secondary">Copilot</span>
        </span>
      </div>
      <button className="new-chat-button" onClick={onNew}>
        <Plus size={17} />
        New chat
      </button>
      <label className="search-field">
        <Search size={15} />
        <input
          aria-label="Search conversations"
          placeholder="Search conversations"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      <nav className="chat-history" aria-label="Chat history">
        <p className="section-label">
          CONVERSATIONS{" "}
          <span>{conversations.length.toString().padStart(2, "0")}</span>
        </p>
        {filtered.length ? (
          filtered.map((chat) => (
            <div
              className={`history-item ${activeId === chat.id ? "is-active" : ""}`}
              key={chat.id}
            >
              <button
                className="history-link"
                onClick={() => onSelect(chat.id)}
                aria-current={activeId === chat.id ? "page" : undefined}
                title={chat.title}
              >
                <MessageSquare size={15} />
                <span>
                  {chat.title}
                  <small className="history-token-count">
                    {chat.tokensCharged == null
                      ? "Historical usage unavailable"
                      : `${chat.tokensCharged.toLocaleString()} tokens${chat.estimatedTokens ? " · includes estimates" : ""}`}
                  </small>
                </span>
              </button>
              <button
                className="delete-chat icon-button"
                onClick={() => onDelete(chat.id)}
                aria-label={`Delete ${chat.title}`}
                title="Delete conversation"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))
        ) : (
          <p className="history-empty">
            {search
              ? "No matching conversations."
              : "Your ideas start here.\nBegin a chat to build your history."}
          </p>
        )}
        {hasMore && (
          <button
            className="load-more-button"
            onClick={onLoadMore}
            disabled={loadingHistory}
          >
            {loadingHistory ? "Loading…" : "Load more conversations"}
          </button>
        )}
      </nav>
      <div className="sidebar-footer">
        <div className="local-note">
          <span className="status-dot" />
          Saved on the server
        </div>
        <p>
          Chat history is stored on the server. Review sources in assistant
          replies.
        </p>
        <div className="workspace-label">
          <span className="workspace-avatar">RS</span>
          <div>
            Personal workspace<small>Local workspace</small>
          </div>
        </div>
      </div>
    </div>
  );
}
