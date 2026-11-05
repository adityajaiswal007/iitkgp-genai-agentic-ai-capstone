"use client";

import { ArrowUp, FileText, Paperclip, Square, X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import {
  ATTACHMENT_ACCEPT,
  MAX_ATTACHMENTS,
  attachmentError,
  fileSize,
} from "@/lib/attachments";

type Props = {
  busy: boolean;
  ready: boolean;
  onSend: (text: string, files?: File[]) => Promise<boolean>;
  onStop: () => void;
};

export function Composer({ busy, ready, onSend, onStop }: Props) {
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 180)}px`;
  }, [draft]);

  function addFiles(incoming: File[]) {
    const next = [...files];
    const errors: string[] = [];
    for (const file of incoming) {
      if (
        next.some(
          (current) =>
            current.name === file.name &&
            current.size === file.size &&
            current.lastModified === file.lastModified,
        )
      )
        continue;
      const error = attachmentError(file);
      if (error) {
        errors.push(error);
        continue;
      }
      if (next.length >= MAX_ATTACHMENTS) {
        errors.push("Attach up to 5 files per message.");
        break;
      }
      next.push(file);
    }
    setFiles(next);
    setFileError(errors.join(" "));
  }

  function submit() {
    if ((!draft.trim() && !files.length) || busy || !ready) return;
    const submitted = draft;
    const submittedFiles = files;
    setFileError("");
    void onSend(submitted, files).then((sent) => {
      if (sent) {
        setDraft((current) => (current === submitted ? "" : current));
        setFiles((current) => (current === submittedFiles ? [] : current));
      }
      input.current?.focus();
    });
  }

  return (
    <form
      className={`composer ${dragging ? "is-dragging" : ""}`}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) {
          event.preventDefault();
          if (ready && !busy) setDragging(true);
        }
      }}
      onDragLeave={(event) => {
        if (
          !(event.relatedTarget instanceof Node) ||
          !event.currentTarget.contains(event.relatedTarget)
        )
          setDragging(false);
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.files.length) return;
        event.preventDefault();
        setDragging(false);
        if (ready && !busy) addFiles(Array.from(event.dataTransfer.files));
      }}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <input
        ref={fileInput}
        type="file"
        tabIndex={-1}
        multiple
        accept={ATTACHMENT_ACCEPT}
        aria-label="Choose attachments"
        className="sr-only"
        disabled={!ready || busy}
        onChange={(event) => {
          addFiles(Array.from(event.currentTarget.files ?? []));
          event.currentTarget.value = "";
        }}
      />
      {files.length > 0 && (
        <div className="attachment-list" aria-label="Selected attachments">
          {files.map((file, index) => (
            <div
              className="attachment-chip"
              key={`${file.name}-${file.size}-${file.lastModified}`}
            >
              <FileText size={17} />
              <span>
                <strong title={file.name}>{file.name}</strong>
                <small>{fileSize(file.size)}</small>
              </span>
              <button
                type="button"
                className="icon-button remove-attachment"
                aria-label={`Remove ${file.name}`}
                disabled={busy}
                onClick={() => {
                  setFiles((current) =>
                    current.filter((_, position) => position !== index),
                  );
                  setFileError("");
                }}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      {fileError && (
        <p className="attachment-error" role="alert">
          {fileError}
        </p>
      )}
      <textarea
        ref={input}
        autoFocus={ready}
        aria-label="Message Operations Copilot"
        placeholder="Message Operations Copilot…"
        value={draft}
        maxLength={8000}
        rows={1}
        disabled={!ready || busy}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-toolbar">
        <div className="composer-tools">
          <button
            type="button"
            className="icon-button attach-button"
            aria-label="Attach files"
            title="Attach up to 5 files, 5 MB each"
            disabled={!ready || busy || files.length >= MAX_ATTACHMENTS}
            onClick={() => fileInput.current?.click()}
          >
            <Paperclip size={18} />
          </button>
          <span className="composer-context">
            <span className="status-dot" />
            General assistant
          </span>
        </div>
        <div className="composer-actions">
          <span className="enter-hint">Enter to send</span>
          {busy ? (
            <button
              type="button"
              className="send-button"
              aria-label="Stop response"
              onClick={onStop}
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={(!draft.trim() && !files.length) || !ready}
            >
              <ArrowUp size={19} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
