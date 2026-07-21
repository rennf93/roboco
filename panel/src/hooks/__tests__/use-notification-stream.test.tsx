import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import type {
  ConnectionState,
  WebSocketOptions,
} from "@/lib/websocket/connection";

// connection.ts has no message buffering/replay across a reconnect (audited
// lines 91-119): drive the WS state machine from the test via a mocked
// connection and assert useNotificationStream's REST catch-up covers the gap
// instead of silently losing whatever was published while disconnected.
const hoisted = vi.hoisted(() => {
  const instances: MockConnection[] = [];
  class MockConnection {
    url: string;
    onMessage?: (data: unknown) => void;
    onStateChange?: (state: ConnectionState) => void;
    constructor(opts: WebSocketOptions) {
      this.url = opts.url;
      this.onMessage = opts.onMessage;
      this.onStateChange = opts.onStateChange;
      instances.push(this);
    }
    connect() {
      this.onStateChange?.("connecting");
      this.onStateChange?.("connected");
    }
    disconnect() {
      this.onStateChange?.("disconnected");
    }
    getState() {
      return "connected";
    }
    getLastPongAt() {
      return Date.now();
    }
    checkPong() {}
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

const listMock = vi.fn();
vi.mock("@/lib/api/notifications", () => ({
  notificationsApi: { list: (...args: unknown[]) => listMock(...args) },
}));

import {
  useNotificationStream,
  _resetSharedSocketsForTest,
} from "../use-websocket";

const resultRef: {
  current: ReturnType<typeof useNotificationStream> | null;
} = { current: null };

function Harness() {
  const stream = useNotificationStream();
  useEffect(() => {
    resultRef.current = stream;
  });
  return null;
}

function emptyList() {
  return { items: [], total: 0, unread_count: 0, pending_ack_count: 0 };
}

describe("useNotificationStream — REST catch-up on reconnect", () => {
  beforeEach(() => {
    hoisted.instances.length = 0;
    resultRef.current = null;
    listMock.mockReset();
    listMock.mockResolvedValue(emptyList());
    _resetSharedSocketsForTest();
  });
  afterEach(() => {
    vi.clearAllMocks();
    _resetSharedSocketsForTest();
  });

  it("does not fetch a catch-up batch on the initial connect", async () => {
    render(<Harness />);
    await act(async () => {});
    expect(listMock).not.toHaveBeenCalled();
  });

  it("fetches unread notifications on reconnect and folds them in", async () => {
    listMock.mockResolvedValue({
      items: [
        {
          id: "n1",
          type: "task_update",
          priority: "normal",
          subject: "Missed while offline",
          timestamp: "2026-07-21T00:00:00Z",
        },
      ],
      total: 1,
      unread_count: 1,
      pending_ack_count: 0,
    });

    render(<Harness />);
    const conn = hoisted.instances[0];
    await act(async () => {});
    expect(listMock).not.toHaveBeenCalled();

    // Drop, then recover — the real sequence connection.ts drives on a
    // watchdog/close event followed by scheduleReconnect().
    act(() => {
      conn.onStateChange?.("reconnecting");
    });
    act(() => {
      conn.onStateChange?.("connecting");
    });
    act(() => {
      conn.onStateChange?.("connected");
    });

    await waitFor(() =>
      expect(listMock).toHaveBeenCalledWith({ unread_only: true }),
    );
    await waitFor(() =>
      expect(resultRef.current?.notifications).toHaveLength(1),
    );
    expect(resultRef.current?.notifications[0].notification_id).toBe("n1");
  });

  it("does not surface the catch-up copy twice when the same notification also arrives live", async () => {
    listMock.mockResolvedValue({
      items: [
        {
          id: "n1",
          type: "task_update",
          priority: "normal",
          subject: "Missed",
          timestamp: "2026-07-21T00:00:00Z",
        },
      ],
      total: 1,
      unread_count: 1,
      pending_ack_count: 0,
    });
    render(<Harness />);
    const conn = hoisted.instances[0];
    await act(async () => {});

    act(() => {
      conn.onStateChange?.("reconnecting");
    });
    act(() => {
      conn.onStateChange?.("connecting");
    });
    act(() => {
      conn.onStateChange?.("connected");
    });
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(resultRef.current?.notifications).toHaveLength(1),
    );

    // The live frame for the same notification arrives right after reconnect.
    act(() => {
      conn.onMessage?.({
        type: "notification",
        notification_id: "n1",
        subject: "Missed",
        priority: "normal",
      });
    });

    expect(resultRef.current?.notifications).toHaveLength(1);
  });

  it("clearMessages also drops the held catch-up batch (no repopulate-after-clear)", async () => {
    listMock.mockResolvedValue({
      items: [
        {
          id: "n1",
          type: "task_update",
          priority: "normal",
          subject: "Missed",
          timestamp: "2026-07-21T00:00:00Z",
        },
      ],
      total: 1,
      unread_count: 1,
      pending_ack_count: 0,
    });
    render(<Harness />);
    const conn = hoisted.instances[0];
    await act(async () => {});
    act(() => {
      conn.onStateChange?.("reconnecting");
    });
    act(() => {
      conn.onStateChange?.("connecting");
    });
    act(() => {
      conn.onStateChange?.("connected");
    });
    await waitFor(() =>
      expect(resultRef.current?.notifications).toHaveLength(1),
    );

    act(() => {
      resultRef.current?.clearMessages();
    });
    expect(resultRef.current?.notifications).toHaveLength(0);
  });
});
