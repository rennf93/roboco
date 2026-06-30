import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// Bundle B: harden the Secretary live chat — (a) a dropped SSE connection must
// reset `streaming` (no permanent "thinking…" spinner), (b) sending mid-reply
// must not clobber the in-flight turn, (c) the chat must survive a reload.
// `secretary` is mocked so start/send/stop/status resolve without network;
// `streamUrl` + `LIVE_EVENT_KINDS` stay real so the EventSource is wired as in
// production.
const { startLive, sendMessage, stop, status } = vi.hoisted(() => ({
  startLive: vi.fn(async () => ({ session_id: "sess-1" })),
  sendMessage: vi.fn(async () => undefined),
  stop: vi.fn(async () => undefined),
  status: vi.fn(async () => ({ alive: true })),
}));

vi.mock("@/lib/api/secretary", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/secretary")>();
  return {
    ...actual,
    secretaryApi: {
      ...actual.secretaryApi,
      startLive,
      sendMessage,
      stop,
      status,
    },
  };
});

import { useSecretary } from "@/hooks/use-secretary";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 1;
  private listeners: Record<string, Array<(e: Event) => void>> = {};
  onerror: ((e: Event) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  removeEventListener(type: string, fn: (e: Event) => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
  }
  close() {
    this.readyState = 2;
  }
  /** Emit a named server-sent event carrying a JSON LiveEvent. */
  emit(kind: string, payload: unknown) {
    const ev = new MessageEvent(kind, { data: JSON.stringify(payload) });
    for (const fn of this.listeners[kind] ?? []) fn(ev);
  }
  /** Transport-level error: a plain Event with no `data`. */
  fireTransportError() {
    this.readyState = 2;
    const ev = new Event("error");
    for (const fn of this.listeners["error"] ?? []) fn(ev);
    if (this.onerror) this.onerror(ev);
  }
}

const ORIGINAL = globalThis.EventSource;

describe("useSecretary — live-chat hardening", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
    localStorage.clear();
    startLive.mockClear();
    sendMessage.mockClear();
    stop.mockClear();
    status.mockClear();
  });
  afterEach(() => {
    globalThis.EventSource = ORIGINAL;
  });

  it("(a) resets streaming and surfaces an error when the SSE connection drops", async () => {
    const { result } = renderHook(() => useSecretary());
    await act(async () => {
      await result.current.start("hi");
    });
    act(() => {
      MockEventSource.instances[0].emit("text", {
        kind: "text",
        text: "thinking",
      });
    });
    await waitFor(() => expect(result.current.streaming).toBe(true));

    act(() => {
      MockEventSource.instances[0].fireTransportError();
    });

    await waitFor(() => expect(result.current.streaming).toBe(false));
    expect(result.current.messages.some((m) => m.text.includes("⚠️"))).toBe(
      true,
    );
    expect(MockEventSource.instances[0].readyState).toBe(2);
  });

  it("(b) ignores a send issued while a reply is still streaming", async () => {
    const { result } = renderHook(() => useSecretary());
    await act(async () => {
      await result.current.start("hi");
    });
    act(() => {
      MockEventSource.instances[0].emit("text", {
        kind: "text",
        text: "partial reply",
      });
    });
    await waitFor(() => expect(result.current.streaming).toBe(true));

    const beforeCount = result.current.messages.length;
    await act(async () => {
      await result.current.send("interrupting");
    });

    expect(sendMessage).not.toHaveBeenCalled();
    // No user bubble was appended and the in-flight buffer was not wiped.
    expect(result.current.messages.length).toBe(beforeCount);
    expect(
      result.current.messages.some((m) => m.text === "partial reply"),
    ).toBe(true);
  });

  it("(c) restores the chat from localStorage on remount (reload durability)", async () => {
    const first = renderHook(() => useSecretary());
    await act(async () => {
      await first.result.current.start("hello there");
    });
    act(() => {
      MockEventSource.instances[0].emit("text", {
        kind: "text",
        text: "hi back",
      });
      MockEventSource.instances[0].emit("turn_end", { kind: "turn_end" });
    });
    await waitFor(() => expect(first.result.current.streaming).toBe(false));
    // Simulate a reload: a fresh hook with no in-memory state.
    first.unmount();

    const second = renderHook(() => useSecretary());
    await waitFor(() => expect(second.result.current.sessionId).toBe("sess-1"));
    expect(
      second.result.current.messages.some((m) => m.text === "hello there"),
    ).toBe(true);
    expect(status).toHaveBeenCalledWith("sess-1");
  });
});
