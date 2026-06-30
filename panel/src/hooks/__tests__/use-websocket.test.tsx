import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useEffect } from "react";
import type {
  ConnectionState,
  WebSocketOptions,
} from "@/lib/websocket/connection";

// #79: useWebSocket's effect cleanup only disconnected the socket — it left the
// `messages` / `lastMessage` / `state` snapshot behind. On a dependency change
// (navigating from one agent/channel stream to another) the new subscription
// inherited the previous stream's stale buffer until a fresh frame arrived.
// Mock the connection so the test can drive onMessage/onStateChange and observe
// the cleanup.
const hoisted = vi.hoisted(() => {
  const instances: MockConnection[] = [];
  class MockConnection {
    url: string;
    onMessage?: (data: unknown) => void;
    onStateChange?: (state: ConnectionState) => void;
    didConnect = false;
    didDisconnect = false;
    constructor(opts: WebSocketOptions) {
      this.url = opts.url;
      this.onMessage = opts.onMessage;
      this.onStateChange = opts.onStateChange;
      instances.push(this);
    }
    connect() {
      this.didConnect = true;
      this.onStateChange?.("connecting");
      this.onStateChange?.("connected");
    }
    disconnect() {
      this.didDisconnect = true;
      this.onStateChange?.("disconnected");
    }
  }
  return { instances, MockConnection };
});

vi.mock("@/lib/websocket/connection", () => ({
  getWebSocketUrl: () => "ws://test/ws",
  WebSocketConnection: hoisted.MockConnection,
}));

vi.mock("@/lib/constants", () => ({
  CEO_AGENT_ID: "00000000-0000-0000-0000-000000000001",
  STREAM_MAX_MESSAGES: 100,
}));

import { useWebSocket } from "../use-websocket";

interface Frame {
  type: "agent.stream";
  agent_id: string;
  chunk: string;
}

// Capture the hook's latest return into a shared ref object (mutation, not
// reassignment — the react-hooks/globals rule forbids the latter in render).
const resultRef: {
  current: ReturnType<typeof useWebSocket<Frame>> | null;
} = { current: null };

function Harness({ endpoint }: { endpoint: string }) {
  const ws = useWebSocket<Frame>(endpoint, undefined, true);
  // Capture the latest return after each render via a passive effect (keeps
  // render pure — the react-hooks rules forbid mutating shared state in render).
  useEffect(() => {
    resultRef.current = ws;
  });
  return null;
}

describe("useWebSocket — clears snapshot on cleanup (#79)", () => {
  beforeEach(() => {
    hoisted.instances.length = 0;
    resultRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("clears messages/lastMessage/state when the endpoint changes (no stale leak)", () => {
    // 1. Subscribe to stream A and receive a frame.
    const { rerender } = render(<Harness endpoint="/agents/a" />);
    const connA = hoisted.instances[0];
    expect(connA.didConnect).toBe(true);
    act(() => {
      connA.onMessage?.({
        type: "agent.stream",
        agent_id: "a",
        chunk: "hello",
      });
    });
    expect(resultRef.current?.messages).toHaveLength(1);
    expect(resultRef.current?.lastMessage?.chunk).toBe("hello");
    expect(resultRef.current?.isConnected).toBe(true);

    // 2. Navigate to stream B — the cleanup for A runs, then B connects. Before
    //    the fix the buffer from A survived into B's subscription.
    act(() => {
      rerender(<Harness endpoint="/agents/b" />);
    });
    expect(connA.didDisconnect).toBe(true);
    expect(hoisted.instances).toHaveLength(2);
    expect(hoisted.instances[1].url).toContain("/agents/b");
    // The stale snapshot MUST be cleared.
    expect(resultRef.current?.messages).toEqual([]);
    expect(resultRef.current?.lastMessage).toBeNull();
  });

  it("disconnects the connection on unmount", () => {
    const { unmount } = render(<Harness endpoint="/agents/a" />);
    const conn = hoisted.instances[0];
    unmount();
    expect(conn.didDisconnect).toBe(true);
  });
});
