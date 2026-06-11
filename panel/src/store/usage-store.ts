import { create } from "zustand";
import type { ConnectionState } from "@/lib/websocket/connection";

/**
 * Usage data received from USAGE_UPDATE or USAGE_SNAPSHOT WebSocket messages
 * on the /ws/system endpoint.
 */
export interface UsageData {
  /** Key performance metrics (velocity, completion rate, active agents, etc.) */
  key_metrics: Record<string, unknown>;
  /** ISO timestamp of the snapshot */
  timestamp?: string;
}

interface UsageState {
  /** Most-recent usage data received over WebSocket; null until first message arrives */
  usageData: UsageData | null;
  /** Current /ws/system WebSocket connection state, synced by useRateLimitWebSocket */
  wsState: ConnectionState;

  // Actions
  /** Overwrite usageData with the latest payload from the WebSocket */
  setUsageData: (data: UsageData) => void;
  /** Clear usageData (e.g. on intentional disconnect or reset) */
  clearUsageData: () => void;
  /** Update the cached WebSocket connection state */
  setWsState: (state: ConnectionState) => void;
}

export const useUsageStore = create<UsageState>((set) => ({
  usageData: null,
  wsState: "disconnected",

  setUsageData: (data) => set({ usageData: data }),
  clearUsageData: () => set({ usageData: null }),
  setWsState: (state) => set({ wsState: state }),
}));
