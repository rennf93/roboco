"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  LIVE_EVENT_KINDS,
  secretaryApi,
  type LiveEvent,
} from "@/lib/api/secretary";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

/**
 * Drives one live Secretary chat: starts the session, opens the SSE stream,
 * accumulates the assistant's token deltas into the last message, and sends the
 * CEO's messages. Intentionally simple — no localStorage persistence; a session
 * lives for the visit.
 */
export function useSecretary() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const bufRef = useRef<string>("");

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const handleEvent = useCallback((raw: string) => {
    let event: LiveEvent;
    try {
      event = JSON.parse(raw) as LiveEvent;
    } catch {
      return;
    }
    if (event.kind === "text" && event.text) {
      bufRef.current += event.text;
      const text = bufRef.current;
      setStreaming(true);
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = { role: "assistant", text };
        } else {
          next.push({ role: "assistant", text });
        }
        return next;
      });
    } else if (event.kind === "turn_end") {
      bufRef.current = "";
      setStreaming(false);
    } else if (event.kind === "error" && event.text) {
      setStreaming(false);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: `⚠️ ${event.text}` },
      ]);
    }
  }, []);

  const openStream = useCallback(
    (sid: string) => {
      closeStream();
      const source = new EventSource(secretaryApi.streamUrl(sid));
      esRef.current = source;
      const listener = (e: MessageEvent) => handleEvent(e.data);
      LIVE_EVENT_KINDS.forEach((kind) =>
        source.addEventListener(kind, listener as EventListener)
      );
    },
    [closeStream, handleEvent]
  );

  const start = useCallback(
    async (initialMessage?: string): Promise<string> => {
      const { session_id } = await secretaryApi.startLive(initialMessage);
      setSessionId(session_id);
      bufRef.current = "";
      setMessages(initialMessage ? [{ role: "user", text: initialMessage }] : []);
      openStream(session_id);
      return session_id;
    },
    [openStream]
  );

  const send = useCallback(
    async (text: string): Promise<void> => {
      if (!sessionId) return;
      bufRef.current = "";
      setMessages((prev) => [...prev, { role: "user", text }]);
      await secretaryApi.sendMessage(sessionId, text);
    },
    [sessionId]
  );

  const stop = useCallback(async (): Promise<void> => {
    if (sessionId) {
      try {
        await secretaryApi.stop(sessionId);
      } catch {
        // Best-effort reap; closing the stream is what matters for the UI.
      }
    }
    closeStream();
    setSessionId(null);
    setMessages([]);
  }, [sessionId, closeStream]);

  useEffect(() => () => closeStream(), [closeStream]);

  return { sessionId, messages, streaming, start, send, stop };
}
