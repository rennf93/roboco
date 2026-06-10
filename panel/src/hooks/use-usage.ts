"use client";

import { useQuery } from "@tanstack/react-query";
import { usageApi } from "@/lib/api/usage";
import type { UsagePeriod } from "@/lib/api/usage";
import type {
  UsageSummary,
  AgentUsageRow,
  TeamUsageRow,
  ModelUsageSlice,
  UsageTimePoint,
  UsageProjection,
  CacheEfficiencyResponse,
  UsageSession,
} from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const usageKeys = {
  all: ["usage"] as const,
  summary: (period: UsagePeriod) => [...usageKeys.all, "summary", period] as const,
  timeSeries: (period: UsagePeriod) => [...usageKeys.all, "time-series", period] as const,
  agentUsage: (period: UsagePeriod) => [...usageKeys.all, "by-agent", period] as const,
  teamUsage: (period: UsagePeriod) => [...usageKeys.all, "by-team", period] as const,
  modelUsage: (period: UsagePeriod) => [...usageKeys.all, "by-model", period] as const,
  projection: () => [...usageKeys.all, "projection"] as const,
  cacheEfficiency: (period: UsagePeriod) => [...usageKeys.all, "cache-efficiency", period] as const,
  sessions: (limit: number) => [...usageKeys.all, "sessions", limit] as const,
};

// =============================================================================
// HOOKS
// =============================================================================

/** Aggregated usage summary (tokens_input, tokens_output, total_cost_usd, …) */
export function useUsageSummary(period: UsagePeriod = "24h") {
  return useQuery<UsageSummary>({
    queryKey: usageKeys.summary(period),
    queryFn: () => usageApi.getUsageSummary(period),
    refetchInterval: 60_000,
  });
}

/** Bucketed time-series data for the stacked area chart */
export function useUsageTimeSeries(period: UsagePeriod = "24h") {
  return useQuery<UsageTimePoint[]>({
    queryKey: usageKeys.timeSeries(period),
    queryFn: () => usageApi.getUsageTimeSeries(period),
    refetchInterval: 120_000,
  });
}

/** Per-agent usage rows for bar chart and agent card mini-bars */
export function useAgentUsage(period: UsagePeriod = "24h") {
  return useQuery<AgentUsageRow[]>({
    queryKey: usageKeys.agentUsage(period),
    queryFn: () => usageApi.getAgentUsage(period),
    refetchInterval: 60_000,
  });
}

/** Per-team usage rows from the dedicated by-team endpoint */
export function useTeamUsage(period: UsagePeriod = "24h") {
  return useQuery<TeamUsageRow[]>({
    queryKey: usageKeys.teamUsage(period),
    queryFn: () => usageApi.getTeamUsage(period),
    refetchInterval: 60_000,
  });
}

/** Per-model slices for the donut chart */
export function useModelUsage(period: UsagePeriod = "24h") {
  return useQuery<ModelUsageSlice[]>({
    queryKey: usageKeys.modelUsage(period),
    queryFn: () => usageApi.getModelUsage(period),
    refetchInterval: 120_000,
  });
}

/** Monthly cost projection based on 7-day rolling average */
export function useUsageProjection() {
  return useQuery<UsageProjection>({
    queryKey: usageKeys.projection(),
    queryFn: () => usageApi.getUsageProjection(),
    refetchInterval: 300_000,
  });
}

/** Cache efficiency stats */
export function useCacheEfficiency(period: UsagePeriod = "24h") {
  return useQuery<CacheEfficiencyResponse>({
    queryKey: usageKeys.cacheEfficiency(period),
    queryFn: () => usageApi.getCacheEfficiency(period),
    refetchInterval: 120_000,
  });
}

/**
 * Recent inference sessions — mock-mode only.
 *
 * Returns an empty array in production (no real backend endpoint for sessions).
 * The SessionsTable will display "No sessions recorded yet" gracefully.
 */
export function useUsageSessions(limit: number = 100) {
  return useQuery<UsageSession[]>({
    queryKey: usageKeys.sessions(limit),
    queryFn: () => usageApi.getUsageSessions(limit),
    refetchInterval: 30_000,
  });
}
