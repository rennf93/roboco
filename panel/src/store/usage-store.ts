import { create } from "zustand";
import type { ConnectionState } from "@/lib/websocket/connection";

/**
 * Live token/cost usage pushed over the /ws/system stream via USAGE_SNAPSHOT
 * events. Mirrors the fields the UsageOverviewPanel renders, so the panel can
 * swap polling for live data without reshaping anything.
 */
export interface UsageData {
  /** Cumulative input tokens across currently-active agents. */
  tokens_input: number;
  /** Cumulative output tokens across currently-active agents. */
  tokens_output: number;
  /** Estimated USD cost for the snapshot. */
  total_cost_usd: number;
  /** Period label for the snapshot (e.g. "live"). */
  period: string;
  /** ISO timestamp of the snapshot. */
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
