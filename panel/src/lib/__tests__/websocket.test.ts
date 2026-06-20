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
    WS_MAX_RECONNECT_ATTEMPTS: 3,
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
