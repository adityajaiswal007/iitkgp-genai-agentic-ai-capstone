"use client";

import { useEffect, useState } from "react";
import type { DailyUsage } from "@/lib/chat";
import { chatApi } from "@/lib/chat-client";

export function useDailyUsage(busy: boolean, activeId: string | null) {
  const [usage, setUsage] = useState<DailyUsage | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let latest = 0;
    const refresh = async () => {
      const request = ++latest;
      try {
        const next = await chatApi.dailyUsage(controller.signal);
        if (!controller.signal.aborted && request === latest) {
          setUsage(next);
          setError("");
        }
      } catch {
        if (!controller.signal.aborted && request === latest)
          setError("Daily token allowance is temporarily unavailable.");
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [busy, activeId]);
  return { usage, error };
}
