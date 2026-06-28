import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import type { ConnectionState } from "@/lib/websocket/connection";

// `useWebSocket` is the seam: drive its `state` + `lastMessage` from the test
// by mutating a shared ref and re-rendering. The hook under test only reads
// `state` + `lastMessage` off the return, so a plain mutable object suffices.
const wsRef = {
  state: "disconnected" as ConnectionState,
  lastMessage: null as {
    type: string;
    totals?: { input_tokens?: number; output_tokens?: number };
    cost_estimate?: number;
    period?: string;
    timestamp?: string;
  } | null,
};

vi.mock("@/hooks/use-websocket", () => ({
  useWebSocket: () => wsRef,
}));

import { useRateLimitWebSocket } from "../use-rate-limit-websocket";
import { useUsageStore } from "@/store/usage-store";

// Harness: a component that mounts the hook under test. Re-rendering it (via
// the `rerender` returned by `render()`) after mutating `wsRef` makes the
// hook's `state`/`lastMessage` effects re-run with the new values.
function Harness() {
  useRateLimitWebSocket();
  return null;
}

function snapshot(n: number) {
  return {
    type: "USAGE_SNAPSHOT",
    totals: { input_tokens: n, output_tokens: n * 2 },
    cost_estimate: n / 100,
    period: "live",
    timestamp: new Date(n).toISOString(),
  };
}

describe("useRateLimitWebSocket — stale-snapshot-on-reconnect (F083)", () => {
  beforeEach(() => {
    wsRef.state = "disconnected";
    wsRef.lastMessage = null;
    useUsageStore.setState({ usageData: null, wsState: "disconnected" });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("clears the stale snapshot on disconnect so a reconnect can't show it as live", () => {
    // 1. Connect + receive a snapshot → store holds live data.
    wsRef.state = "connected";
    wsRef.lastMessage = snapshot(100);
    const { rerender } = render(<Harness />);
    expect(useUsageStore.getState().usageData).not.toBeNull();
    expect(useUsageStore.getState().usageData?.tokens_input).toBe(100);

    // 2. Stream drops → the stale snapshot MUST be cleared. Before the fix the
    //    hook only synced wsState and left usageData untouched.
    wsRef.state = "reconnecting";
    wsRef.lastMessage = null;
    rerender(<Harness />);
    expect(useUsageStore.getState().usageData).toBeNull();
    expect(useUsageStore.getState().wsState).toBe("reconnecting");

    // 3. Reconnect (state flips to connected) BEFORE any new snapshot arrives.
    //    This is the regression: the panel would render the prior session's
    //    totals/cost as "live". usageData must still be null so the panel falls
    //    back to the polling summary until a fresh USAGE_SNAPSHOT lands.
    wsRef.state = "connected";
    rerender(<Harness />);
    expect(useUsageStore.getState().wsState).toBe("connected");
    expect(useUsageStore.getState().usageData).toBeNull();

    // 4. A fresh snapshot arrives on the new connection → live data restored.
    wsRef.lastMessage = snapshot(500);
    rerender(<Harness />);
    expect(useUsageStore.getState().usageData?.tokens_input).toBe(500);
  });

  it("does not clear live data while the stream stays connected (no false drop)", () => {
    // Regression guard: clearing is gated on state !== "connected", so a
    // connected → connected re-render (e.g. an unrelated parent re-render) must
    // NOT wipe a valid live snapshot.
    wsRef.state = "connected";
    wsRef.lastMessage = snapshot(250);
    const { rerender } = render(<Harness />);
    expect(useUsageStore.getState().usageData?.tokens_input).toBe(250);

    rerender(<Harness />);
    rerender(<Harness />);
    expect(useUsageStore.getState().usageData?.tokens_input).toBe(250);
    expect(useUsageStore.getState().wsState).toBe("connected");
  });
});
