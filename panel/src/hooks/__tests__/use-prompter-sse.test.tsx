import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// `prompter-live` is mocked so `start` resolves immediately with a fixed session
// id (no network); `streamUrl` + `LIVE_EVENT_KINDS` stay real so the hook wires
// the EventSource exactly as in production.
vi.mock("@/lib/api/prompter-live", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/api/prompter-live")>();
  return {
    ...actual,
    prompterLiveApi: {
      ...actual.prompterLiveApi,
      start: vi.fn(async () => ({ session_id: "sess-1" })),
      stop: vi.fn(async () => undefined),
      status: vi.fn(async () => ({ alive: false })),
      sendMessage: vi.fn(async () => undefined),
    },
  };
});

// `useProjects` would otherwise fire a real fetch on mount.
vi.mock("@/hooks/use-projects", () => ({
  useProjects: () => ({ data: [] }),
}));

import { usePrompter } from "@/hooks/use-prompter";

// ---------------------------------------------------------------------------
// A minimal EventSource double. jsdom has no native EventSource. The double
// records listeners per event kind (as the hook registers them) and lets the
// test dispatch a TRANSPORT-level error — a plain `Event` with NO `data`
// (exactly what a dropped connection / dead session fires in a real browser,
// distinct from a server-sent `event: error` MessageEvent which carries JSON).
// ---------------------------------------------------------------------------
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 1; // OPEN
  private listeners: Record<string, Array<(e: Event) => void>> = {};
  onerror: ((e: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    this.readyState = 1;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  removeEventListener(type: string, fn: (e: Event) => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
  }
  close() {
    this.readyState = 2; // CLOSED
  }
  /** Fire a transport-level error: a plain Event with no `data` (no JSON). */
  fireTransportError() {
    this.readyState = 2;
    const ev = new Event("error");
    for (const fn of this.listeners["error"] ?? []) fn(ev);
    if (this.onerror) this.onerror(ev);
  }
  /** Fire a server-sent error event: a MessageEvent carrying JSON data. */
  fireServerSentError(text: string) {
    const ev = new MessageEvent("error", {
      data: JSON.stringify({ kind: "error", text }),
    });
    for (const fn of this.listeners["error"] ?? []) fn(ev);
  }
}

const ORIGINAL_EVENT_SOURCE = globalThis.EventSource;

describe("usePrompter — SSE transport-error handling (F021)", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
    localStorage.clear();
  });
  afterEach(() => {
    globalThis.EventSource = ORIGINAL_EVENT_SOURCE;
  });

  it("isSending is true once a turn is streaming over SSE", async () => {
    const { result } = renderHook(() => usePrompter());
    // Scope the chat to a project + opening message so isFormValid() is true.
    act(() => {
      result.current.setProjectId("proj-1");
      result.current.setInitialMessage("Build me a timestamp footer");
    });
    await act(async () => {
      await result.current.start();
    });
    expect(MockEventSource.instances).toHaveLength(1);
    await waitFor(() => {
      expect(result.current.isSending).toBe(true);
    });
  });

  it("a dropped/dead SSE connection resets isSending so the composer is not stuck", async () => {
    const { result } = renderHook(() => usePrompter());
    act(() => {
      result.current.setProjectId("proj-1");
      result.current.setInitialMessage("Build me a timestamp footer");
    });
    await act(async () => {
      await result.current.start();
    });
    await waitFor(() => {
      expect(result.current.isSending).toBe(true);
    });

    // The SSE connection drops (or the server closed the dead session): the
    // EventSource fires a transport-level error with no JSON data. Before the
    // fix this was swallowed by the JSON-parse try/catch and isSending stayed
    // true — the composer was permanently disabled.
    act(() => {
      MockEventSource.instances[0]!.fireTransportError();
    });

    await waitFor(() => {
      expect(result.current.isSending).toBe(false);
    });
    // The user is told the connection died (not a silent hang).
    expect(result.current.messages.some((m) => m.role === "error")).toBe(true);
    // The dead stream was closed so EventSource doesn't loop-reconnect a
    // session that no longer exists.
    expect(MockEventSource.instances[0]!.readyState).toBe(2);
  });

  it("a server-sent error event (JSON data) is still handled as before", async () => {
    const { result } = renderHook(() => usePrompter());
    act(() => {
      result.current.setProjectId("proj-1");
      result.current.setInitialMessage("Build me a timestamp footer");
    });
    await act(async () => {
      await result.current.start();
    });
    await waitFor(() => {
      expect(result.current.isSending).toBe(true);
    });

    // A server-sent `event: error` carries JSON — the existing handler path.
    act(() => {
      MockEventSource.instances[0]!.fireServerSentError("the agent blew up");
    });

    await waitFor(() => {
      expect(result.current.isSending).toBe(false);
    });
    expect(
      result.current.messages.some((m) =>
        m.content.includes("the agent blew up"),
      ),
    ).toBe(true);
  });
});
