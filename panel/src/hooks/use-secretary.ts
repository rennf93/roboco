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

// ---------------------------------------------------------------------------
// Reload durability
//
// The chat lived entirely in React state, so a browser reload wiped it. The
// secretary agent container outlives the page, so persist a small slice to
// localStorage and, on mount, restore it once the backend confirms the session
// is still alive (a full reload doesn't run effect cleanup, so the stream just
// re-attaches). TTL'd so a stale id is never reconnected.
// ---------------------------------------------------------------------------

const PERSIST_KEY = "roboco:secretary:live";
const PERSIST_TTL_MS = 30 * 60 * 1000; // 30 minutes

interface PersistedChat {
  sessionId: string;
  messages: ChatMessage[];
  savedAt: number;
}

function loadPersisted(): PersistedChat | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PERSIST_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedChat;
    if (!parsed.sessionId || Date.now() - parsed.savedAt > PERSIST_TTL_MS) {
      window.localStorage.removeItem(PERSIST_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function savePersisted(slice: PersistedChat): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PERSIST_KEY, JSON.stringify(slice));
  } catch {
    // localStorage full / unavailable — durability is best-effort.
  }
}

function clearPersisted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(PERSIST_KEY);
  } catch {
    // ignore
  }
}

/**
 * Drives one live Secretary chat: starts the session, opens the SSE stream,
 * accumulates the assistant's token deltas into the last message, and sends the
 * CEO's messages. Hardened so a dropped stream never leaves a stuck spinner, a
 * send mid-reply can't clobber the in-flight turn, and a reload resumes the chat.
 */
export function useSecretary() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const bufRef = useRef<string>("");
  // Mirror state in refs so SSE callbacks / send never see a stale closure.
  const sessionIdRef = useRef<string | null>(null);
  const streamingRef = useRef(false);

  const markStreaming = useCallback((v: boolean) => {
    streamingRef.current = v;
    setStreaming(v);
  }, []);

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const handleEvent = useCallback(
    (raw: string) => {
      let event: LiveEvent;
      try {
        event = JSON.parse(raw) as LiveEvent;
      } catch {
        return;
      }
      if (event.kind === "text" && event.text) {
        bufRef.current += event.text;
        const text = bufRef.current;
        markStreaming(true);
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
        markStreaming(false);
      } else if (event.kind === "error" && event.text) {
        markStreaming(false);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: `⚠️ ${event.text}` },
        ]);
      }
    },
    [markStreaming],
  );

  // A dropped connection or a session the server already tore down fires a
  // transport-level `error` Event with NO JSON data — distinct from a
  // server-sent `event: error` frame (a MessageEvent carrying JSON). Without
  // this the transport error was swallowed by the JSON-parse try/catch and
  // `streaming` stayed true — the "thinking…" spinner hung forever.
  const handleTransportError = useCallback(() => {
    bufRef.current = "";
    markStreaming(false);
    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        text: "⚠️ Live connection lost — start a new chat to continue.",
      },
    ]);
    closeStream();
  }, [markStreaming, closeStream]);

  const openStream = useCallback(
    (sid: string) => {
      closeStream();
      const source = new EventSource(secretaryApi.streamUrl(sid));
      esRef.current = source;
      for (const kind of LIVE_EVENT_KINDS) {
        if (kind === "error") continue; // error is dual-purpose — handled below
        source.addEventListener(kind, (e: MessageEvent) => handleEvent(e.data));
      }
      // `error` is dual-purpose: a server-sent error frame carries JSON `data`
      // → handle it like any kind; a transport error has no `data` → reset.
      source.addEventListener("error", (e: Event) => {
        const data = (e as MessageEvent).data;
        if (typeof data === "string") handleEvent(data);
        else handleTransportError();
      });
    },
    [closeStream, handleEvent, handleTransportError],
  );

  const start = useCallback(
    async (initialMessage?: string): Promise<string> => {
      const { session_id } = await secretaryApi.startLive(initialMessage);
      sessionIdRef.current = session_id;
      setSessionId(session_id);
      bufRef.current = "";
      setMessages(
        initialMessage ? [{ role: "user", text: initialMessage }] : [],
      );
      openStream(session_id);
      return session_id;
    },
    [openStream],
  );

  const send = useCallback(async (text: string): Promise<void> => {
    const sid = sessionIdRef.current;
    // Ignore a send while a reply is still streaming — wiping the buffer and
    // posting now would abandon / duplicate the in-flight turn.
    if (!sid || streamingRef.current) return;
    bufRef.current = "";
    setMessages((prev) => [...prev, { role: "user", text }]);
    await secretaryApi.sendMessage(sid, text);
  }, []);

  const stop = useCallback(async (): Promise<void> => {
    const sid = sessionIdRef.current;
    if (sid) {
      try {
        await secretaryApi.stop(sid);
      } catch {
        // Best-effort reap; closing the stream is what matters for the UI.
      }
    }
    closeStream();
    clearPersisted();
    sessionIdRef.current = null;
    markStreaming(false);
    setSessionId(null);
    setMessages([]);
  }, [closeStream, markStreaming]);

  // Persist the chat whenever it changes so a reload can resume it.
  useEffect(() => {
    if (sessionId) {
      savePersisted({ sessionId, messages, savedAt: Date.now() });
    }
  }, [sessionId, messages]);

  // On mount, reconnect to a still-running session left behind by a reload.
  const didRestoreRef = useRef(false);
  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;
    const persisted = loadPersisted();
    if (!persisted) return;
    let cancelled = false;
    void (async () => {
      try {
        const { alive } = await secretaryApi.status(persisted.sessionId);
        if (cancelled) return;
        if (!alive) {
          clearPersisted();
          return;
        }
        // Restore history + re-attach the stream. Any tokens from a turn that
        // was mid-flight at reload are gone, so land in a non-streaming state.
        sessionIdRef.current = persisted.sessionId;
        bufRef.current = "";
        setSessionId(persisted.sessionId);
        setMessages(persisted.messages);
        markStreaming(false);
        openStream(persisted.sessionId);
      } catch {
        // Status check failed (server unreachable) — keep the slice for a later
        // retry within its TTL; the user stays on a fresh chat for now.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [openStream, markStreaming]);

  useEffect(() => () => closeStream(), [closeStream]);

  return { sessionId, messages, streaming, start, send, stop };
}
