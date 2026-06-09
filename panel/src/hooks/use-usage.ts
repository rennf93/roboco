"use client";

import { useQuery } from "@tanstack/react-query";
import { usageApi } from "@/lib/api/usage";
import type {
  TokenUsageSnapshot,
  AgentUsageRow,
  UsageSession,
  UsageTimePoint,
  ModelUsageSlice,
} from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const usageKeys = {
  all: ["usage"] as const,
  snapshot: () => [...usageKeys.all, "snapshot"] as const,
  timeSeries: (hours: number) => [...usageKeys.all, "time-series", hours] as const,
  agentUsage: () => [...usageKeys.all, "agents"] as const,
  sessions: (limit: number) => [...usageKeys.all, "sessions", limit] as const,
  modelUsage: () => [...usageKeys.all, "models"] as const,
};

// =============================================================================
// HOOKS
// =============================================================================

/** Snapshot of current-day usage totals (tokens, cost, sessions, cache) */
export function useUsageSnapshot() {
  return useQuery<TokenUsageSnapshot>({
    queryKey: usageKeys.snapshot(),
    queryFn: () => usageApi.getUsageSnapshot(),
    refetchInterval: 60_000,
  });
}

/** Hourly time-series data for the stacked area chart */
export function useUsageTimeSeries(hours: number = 24) {
  return useQuery<UsageTimePoint[]>({
    queryKey: usageKeys.timeSeries(hours),
    queryFn: () => usageApi.getUsageTimeSeries(hours),
    refetchInterval: 120_000,
  });
}

/** Per-agent usage rows for bar chart and agent card mini-bars */
export function useAgentUsage() {
  return useQuery<AgentUsageRow[]>({
    queryKey: usageKeys.agentUsage(),
    queryFn: () => usageApi.getAgentUsage(),
    refetchInterval: 60_000,
  });
}

/** Recent inference sessions for the sessions table */
export function useUsageSessions(limit: number = 100) {
  return useQuery<UsageSession[]>({
    queryKey: usageKeys.sessions(limit),
    queryFn: () => usageApi.getUsageSessions(limit),
    refetchInterval: 30_000,
  });
}

/** Per-model slices for the donut chart */
export function useModelUsage() {
  return useQuery<ModelUsageSlice[]>({
    queryKey: usageKeys.modelUsage(),
    queryFn: () => usageApi.getModelUsage(),
    refetchInterval: 120_000,
  });
}
