import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useEffect } from "react";
import type {
  ConnectionState,
  WebSocketOptions,
} from "@/lib/websocket/connection";

// Bundle A: the session detail view had no live subscription — send_message now
// publishes MESSAGE_SENT → bridge → /ws/sessions/{id}, and useSessionStream is
// the panel half that subscribes so an open transcript updates without a manual
// Refresh. Mock the connection so the test can drive onMessage frames.
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

import { useSessionStream } from "../use-websocket";

const resultRef: {
  current: ReturnType<typeof useSessionStream> | null;
} = { current: null };

function Harness({ sessionId }: { sessionId: string | null }) {
  const ws = useSessionStream(sessionId);
  useEffect(() => {
    resultRef.current = ws;
  });
  return null;
}

describe("useSessionStream", () => {
  beforeEach(() => {
    hoisted.instances.length = 0;
    resultRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("connects to the session endpoint with the CEO agent_id", () => {
    render(<Harness sessionId="sess-1" />);
    expect(hoisted.instances).toHaveLength(1);
    expect(hoisted.instances[0].url).toContain("/sessions/sess-1");
    expect(hoisted.instances[0].url).toContain(
      "agent_id=00000000-0000-0000-0000-000000000001",
    );
    expect(resultRef.current?.isConnected).toBe(true);
  });

  it("does not connect when sessionId is null", () => {
    render(<Harness sessionId={null} />);
    expect(hoisted.instances).toHaveLength(0);
  });

  it("surfaces a message.new frame in sessionMessages and lastMessage", () => {
    render(<Harness sessionId="sess-1" />);
    const conn = hoisted.instances[0];
    act(() => {
      conn.onMessage?.({
        type: "message.new",
        message_id: "m1",
        session_id: "sess-1",
        agent_id: "a1",
        content: "hello",
        message_type: "dialogue",
      });
    });
    expect(resultRef.current?.sessionMessages).toHaveLength(1);
    expect(resultRef.current?.sessionMessages[0].message_id).toBe("m1");
    expect(resultRef.current?.lastMessage?.type).toBe("message.new");
  });

  it("ignores the initial connected frame (not a real message)", () => {
    render(<Harness sessionId="sess-1" />);
    const conn = hoisted.instances[0];
    act(() => {
      conn.onMessage?.({ type: "connected", session_id: "sess-1" });
    });
    expect(resultRef.current?.sessionMessages).toHaveLength(0);
  });
});
