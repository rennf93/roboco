/**
 * Tests for getWebSocketUrl() from @/lib/websocket/connection.
 *
 * Because `WS_URL` is a named import captured at module-load time, we use
 * vi.resetModules() + vi.doMock() + dynamic import() in each test so that
 * every test gets a fresh module binding with the desired constant value.
 *
 * window.location is stubbed via vi.stubGlobal() and cleaned up in
 * afterEach with vi.unstubAllGlobals().
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// Shared constants mock factory
// ---------------------------------------------------------------------------

function mockConstants(wsUrl: string) {
  vi.doMock("@/lib/constants", () => ({
    WS_URL: wsUrl,
    API_URL: "/api",
    CEO_AGENT_ID: "00000000-0000-0000-0000-000000000001",
    CEO_ROLE: "ceo",
    DEFAULT_PAGE_SIZE: 20,
    MAX_PAGE_SIZE: 100,
    WS_RECONNECT_INTERVAL: 5000,
    WS_RECONNECT_MAX_INTERVAL: 30000,
    WS_PONG_TIMEOUT_MS: 60000,
    WS_HEARTBEAT_INTERVAL: 30000,
    STREAM_MAX_MESSAGES: 100,
    NOTIFICATION_MAX_DISPLAY: 10,
  }));
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Absolute URL passthrough
// ---------------------------------------------------------------------------

describe("getWebSocketUrl — absolute ws:// passthrough", () => {
  it("returns the absolute ws:// URL unchanged", async () => {
    const absoluteUrl = "ws://direct.example.com/ws";
    mockConstants(absoluteUrl);
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    expect(getWebSocketUrl()).toBe(absoluteUrl);
  });
});

describe("getWebSocketUrl — absolute wss:// passthrough", () => {
  it("returns the absolute wss:// URL unchanged", async () => {
    const absoluteUrl = "wss://secure.example.com/ws";
    mockConstants(absoluteUrl);
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    expect(getWebSocketUrl()).toBe(absoluteUrl);
  });
});

// ---------------------------------------------------------------------------
// Relative-path construction via window.location
// ---------------------------------------------------------------------------

describe("getWebSocketUrl — relative-path http → ws:", () => {
  it("maps http: protocol to ws: and appends the relative WS_URL", async () => {
    mockConstants("/ws");
    vi.stubGlobal("location", {
      protocol: "http:",
      host: "myhost.local:3000",
    });
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    expect(getWebSocketUrl()).toBe("ws://myhost.local:3000/ws");
  });
});

describe("getWebSocketUrl — relative-path https → wss:", () => {
  it("maps https: protocol to wss: and appends the relative WS_URL", async () => {
    mockConstants("/ws");
    vi.stubGlobal("location", {
      protocol: "https:",
      host: "secure.example.com",
    });
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    expect(getWebSocketUrl()).toBe("wss://secure.example.com/ws");
  });

  it("prepends a leading slash when WS_URL does not start with /", async () => {
    mockConstants("ws");
    vi.stubGlobal("location", {
      protocol: "https:",
      host: "secure.example.com",
    });
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    // The function does: `/${WS_URL}` when WS_URL doesn't start with /
    expect(getWebSocketUrl()).toBe("wss://secure.example.com/ws");
  });
});

// ---------------------------------------------------------------------------
// SSR fallback (window undefined)
// ---------------------------------------------------------------------------

describe("getWebSocketUrl — SSR fallback", () => {
  it("returns the raw WS_URL when window is not available", async () => {
    mockConstants("/ws");
    // Remove window to simulate server-side rendering
    vi.stubGlobal("window", undefined);
    const { getWebSocketUrl } = await import("@/lib/websocket/connection");
    expect(getWebSocketUrl()).toBe("/ws");
  });
});

// ---------------------------------------------------------------------------
// WebSocketConnection — pong watchdog + long-tail reconnect (C4)
//
// jsdom doesn't ship a real WebSocket. Stub the constructor with a minimal
// class that records handlers and lets the test drive open/close/message.
// ---------------------------------------------------------------------------

class MockWebSocket {
  // WebSocket ready-state constants — `connection.ts` references WebSocket.OPEN
  // for the early-return guard, so the stub must define them.
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];
  static last(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send() {}
  close(code = 1006, reason = "") {
    if (this.closed) return;
    this.closed = true;
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code, reason, wasClean: false }));
  }
  fireOpen() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  fireMessage(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

describe("WebSocketConnection — pong watchdog (C4)", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", MockWebSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("tracks lastPongAt when a 'pong' frame arrives", async () => {
    mockConstants("/ws");
    const { WebSocketConnection } = await import("@/lib/websocket/connection");
    const onStateChange = vi.fn();
    const conn = new WebSocketConnection({
      url: "ws://test/ws",
      onStateChange,
      heartbeatInterval: 30000,
    });
    conn.connect();
    const ws = MockWebSocket.last();
    ws.fireOpen();

    const before = conn.getLastPongAt();
    // A pong frame should refresh lastPongAt.
    vi.advanceTimersByTime(1000);
    ws.fireMessage("pong");
    const after = conn.getLastPongAt();
    expect(after).toBeGreaterThan(before);
    // A data frame must NOT touch lastPongAt.
    const dataBefore = conn.getLastPongAt();
    ws.fireMessage(JSON.stringify({ type: "ping" }));
    expect(conn.getLastPongAt()).toBe(dataBefore);
  });

  it("force-closes when no pong arrives within 2× heartbeat interval", async () => {
    mockConstants("/ws");
    const { WebSocketConnection } = await import("@/lib/websocket/connection");
    const onStateChange = vi.fn();
    const conn = new WebSocketConnection({
      url: "ws://test/ws",
      onStateChange,
      heartbeatInterval: 30000,
      // Use the default WS_PONG_TIMEOUT_MS (60000) — 2× heartbeat.
    });
    conn.connect();
    const ws = MockWebSocket.last();
    ws.fireOpen();

    // No pong for 61s. The heartbeat tick checks the watchdog BEFORE sending
    // ping; advancing past the 60s timeout (2× heartbeat) should force-close
    // → onclose → the reconnect path fires (state → reconnecting). The tick at
    // 30s passes (only 30s elapsed); the tick at 60s trips the watchdog.
    vi.advanceTimersByTime(61000);
    expect(ws.closed).toBe(true);
    expect(onStateChange).toHaveBeenLastCalledWith("reconnecting");
  });

  it("does not force-close when pongs keep arriving", async () => {
    mockConstants("/ws");
    const { WebSocketConnection } = await import("@/lib/websocket/connection");
    const conn = new WebSocketConnection({
      url: "ws://test/ws",
      onStateChange: vi.fn(),
      heartbeatInterval: 30000,
    });
    conn.connect();
    const ws = MockWebSocket.last();
    ws.fireOpen();

    // Each heartbeat tick: send ping, then a pong comes back well within the
    // 60s watchdog window. Advance 3 ticks; socket must stay open.
    for (let i = 0; i < 3; i++) {
      vi.advanceTimersByTime(29000);
      ws.fireMessage("pong");
    }
    expect(ws.closed).toBe(false);
  });
});

describe("WebSocketConnection — long-tail reconnect (C4)", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", MockWebSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps reconnecting past the old 3-attempt cap (no terminal 'disconnected')", async () => {
    mockConstants("/ws");
    const { WebSocketConnection } = await import("@/lib/websocket/connection");
    const onStateChange = vi.fn();
    const conn = new WebSocketConnection({
      url: "ws://test/ws",
      onStateChange,
      reconnectInterval: 5000,
    });
    conn.connect();
    // Drive 5 close→reconnect cycles WITHOUT ever firing onopen. The old code
    // reset reconnectAttempts to 0 inside onopen, so tests that fired open
    // between closes never accumulated attempts and passed under the pre-fix
    // `attempts < maxReconnectAttempts` gate. Here attempts accumulates, so
    // past attempt 3 the old gate would have flipped shouldReconnect false →
    // terminal 'disconnected' and no new socket. The fixed code has no cap.
    for (let i = 0; i < 5; i++) {
      const before = MockWebSocket.instances.length;
      const ws = MockWebSocket.last();
      actClose(ws, 1006);
      // State must be 'reconnecting', never the terminal 'disconnected'.
      expect(conn.getState()).toBe("reconnecting");
      // Advance past the backoff so connect() runs and a fresh socket is
      // constructed. Delay grows each cycle but stays ≤ 30s (cap tested below);
      // 30s covers cycles 0-4 (5000*1.5^4 = 25312 < 30000).
      vi.advanceTimersByTime(30000);
      expect(MockWebSocket.instances.length).toBe(before + 1);
      // Intentionally do NOT fire open — attempts must keep accumulating.
    }
    // A 6th reconnect is still scheduled — never gave up.
    expect(conn.getState()).not.toBe("disconnected");
  });

  it("caps the backoff delay at WS_RECONNECT_MAX_INTERVAL", async () => {
    mockConstants("/ws");
    const { WebSocketConnection } = await import("@/lib/websocket/connection");
    const conn = new WebSocketConnection({
      url: "ws://test/ws",
      onStateChange: vi.fn(),
      reconnectInterval: 5000,
    });
    conn.connect();

    // Close→reconnect without ever firing onopen so reconnectAttempts climbs
    // past the point where the uncapped delay 5000*1.5^N vastly exceeds 30s.
    // After 7 cycles attempts=7 → uncapped delay ≈ 85422ms ≫ 30000ms cap.
    for (let i = 0; i < 7; i++) {
      const ws = MockWebSocket.last();
      actClose(ws, 1006);
      // Delay is capped at 30s, so advancing 30s always fires the reconnect.
      vi.advanceTimersByTime(30000);
      expect(MockWebSocket.instances.length).toBe(i + 2);
    }
    // Now at attempt 7: uncapped delay would be ~85s. Under the old uncapped
    // code, advancing 30s would leave the timer unexpired → no new socket.
    // Under the capped code, delay = min(85422, 30000) = 30000 → reconnect
    // fires within 30s and a new socket is constructed.
    const before = MockWebSocket.instances.length;
    const ws = MockWebSocket.last();
    actClose(ws, 1006);
    vi.advanceTimersByTime(30000);
    expect(MockWebSocket.instances.length).toBe(before + 1);
  });
});

function actClose(ws: MockWebSocket, code: number) {
  ws.close(code, "");
}
