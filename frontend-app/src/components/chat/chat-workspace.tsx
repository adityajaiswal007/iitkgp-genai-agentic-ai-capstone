"use client";

import {
  ArrowUpRight,
  ChartNoAxesCombined,
  FileText,
  Info,
  ListChecks,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Sparkles,
  SquarePen,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import { Composer } from "./composer";
import { MessageList } from "./message-list";
import { Sidebar } from "./sidebar";
import { useConversations } from "./use-conversations";

const starters = [
  {
    icon: FileText,
    title: "Find the key takeaways",
    detail: "Turn a long report into a clear summary",
    prompt: "Help me summarize a report into key takeaways.",
  },
  {
    icon: ChartNoAxesCombined,
    title: "Compare the options",
    detail: "Bring structure to your next decision",
    prompt: "Help me plan a vendor review and compare the options.",
  },
  {
    icon: Search,
    title: "Explore a question",
    detail: "Make a research plan with useful sources",
    prompt: "Help me create a research plan for a competitor analysis.",
  },
  {
    icon: ListChecks,
    title: "Make a plan",
    detail: "Break a complex task into practical steps",
    prompt: "Help me plan an operations project with clear tasks and owners.",
  },
];

export function ChatWorkspace() {
  const chat = useConversations();
  const [collapsed, setCollapsed] = useState(false);
  const mobileSidebar = useRef<HTMLDialogElement>(null);
  const about = useRef<HTMLDialogElement>(null);

  const sidebarProps = {
    conversations: chat.conversations,
    activeId: chat.activeId,
    onNew: () => {
      chat.newChat();
      mobileSidebar.current?.close();
    },
    onSelect: (id: string) => {
      chat.selectChat(id);
      mobileSidebar.current?.close();
    },
    onDelete: chat.deleteChat,
    search: chat.search,
    onSearch: chat.setSearch,
    hasMore: chat.hasMore,
    loadingHistory: chat.loadingHistory,
    onLoadMore: chat.loadMore,
  };

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <a className="skip-link" href="#main-content">
        Skip to chat
      </a>
      <aside className="desktop-sidebar" aria-label="Workspace sidebar">
        <Sidebar {...sidebarProps} />
      </aside>
      <dialog
        className="mobile-sidebar"
        ref={mobileSidebar}
        aria-label="Workspace navigation"
      >
        <button
          className="close-navigation icon-button"
          aria-label="Close sidebar"
          onClick={() => mobileSidebar.current?.close()}
        >
          <X size={18} />
        </button>
        <Sidebar {...sidebarProps} />
      </dialog>
      <div className="main-panel">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button desktop-toggle"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? (
                <PanelLeftOpen size={19} />
              ) : (
                <PanelLeftClose size={19} />
              )}
            </button>
            <button
              className="icon-button mobile-toggle"
              aria-label="Open sidebar"
              onClick={() => mobileSidebar.current?.showModal()}
            >
              <PanelLeftOpen size={19} />
            </button>
            <span className="breadcrumb">
              Workspace<span>/</span>
              <strong>
                {chat.active ? "Conversation" : "New conversation"}
              </strong>
            </span>
          </div>
          <div className="topbar-right">
            <button
              className="demo-pill"
              onClick={() => about.current?.showModal()}
            >
              <span className="status-dot" />
              About replies
              <Info size={13} />
            </button>
            <button
              className="icon-button header-new-chat"
              aria-label="Start a new conversation"
              onClick={chat.newChat}
            >
              <SquarePen size={18} />
            </button>
          </div>
        </header>
        <main
          id="main-content"
          className={`chat-main ${chat.active ? "has-conversation" : ""}`}
        >
          {chat.active ? (
            <>
              <h1 className="sr-only">{chat.active.title}</h1>
              <div className="conversation-scroll">
                {chat.active.firstLoadedPage > 1 && (
                  <button
                    className="load-more-button earlier-messages"
                    disabled={chat.busy}
                    onClick={() => void chat.loadEarlier()}
                  >
                    Load earlier messages
                  </button>
                )}
                <MessageList
                  key={chat.active.id}
                  messages={chat.active.messages}
                  conversationId={chat.active.id}
                  waiting={chat.waiting}
                  streamingMessageId={chat.streamingMessageId}
                />
              </div>
            </>
          ) : (
            <div className="welcome">
              <div className="welcome-symbol">
                <Sparkles size={29} strokeWidth={1.5} />
              </div>
              <p className="eyebrow">A LITTLE HELP FOR THE WORK AHEAD</p>
              <h1>
                What are we
                <br className="mobile-break" /> working on?
              </h1>
              <p className="welcome-description">
                A space to think clearly, explore ideas,
                <br className="desktop-break" /> and move your work forward.
              </p>
              <div className="starters">
                {starters.map(({ icon: Icon, title, detail, prompt }) => (
                  <button
                    key={title}
                    className="starter"
                    disabled={!chat.ready || chat.busy}
                    onClick={() => void chat.send(prompt)}
                  >
                    <Icon size={19} strokeWidth={1.6} />
                    <span>
                      <strong>{title}</strong>
                      <small>{detail}</small>
                    </span>
                    <ArrowUpRight className="starter-arrow" size={16} />
                  </button>
                ))}
              </div>
              <div className="welcome-footnote">
                <span />
                Your next good idea starts with a conversation.
                <span />
              </div>
            </div>
          )}
          <div className="composer-area">
            {chat.notice && (
              <p className="inline-notice" role="status">
                {chat.notice}
                {!chat.ready && (
                  <button
                    className="retry-button"
                    onClick={() => void chat.retryLoad()}
                  >
                    Retry connection
                  </button>
                )}
              </p>
            )}
            <Composer
              key={chat.activeId ?? "new"}
              busy={chat.busy}
              ready={chat.ready}
              onSend={chat.send}
              onStop={chat.cancel}
            />
            <p className="composer-disclaimer">
              AI answers can contain mistakes. Review sources and important
              information.
            </p>
          </div>
        </main>
      </div>
      <dialog
        className="about-dialog"
        ref={about}
        aria-labelledby="about-title"
      >
        <div className="about-dialog-header">
          <span className="assistant-symbol">
            <Sparkles size={20} />
          </span>
          <button
            className="icon-button"
            aria-label="Close workspace information"
            onClick={() => about.current?.close()}
          >
            <X size={18} />
          </button>
        </div>
        <h2 id="about-title">About your workspace</h2>
        <p>
          Queries run through the agent service using its configured model and
          document corpus. Demo inference is labelled in the answer when
          enabled. Attachments provide text sources for this conversation, with
          source references in replies. Images and scanned pages use OCR.
        </p>
        <p>
          Conversations are saved on the server until you delete them. This
          local workspace is shared; use sample content while exploring.
        </p>
        <button
          className="primary-button"
          onClick={() => about.current?.close()}
        >
          Got it
        </button>
      </dialog>
    </div>
  );
}
