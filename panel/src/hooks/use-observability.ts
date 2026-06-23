"use client";

import { useQuery } from "@tanstack/react-query";
import { observabilityApi } from "@/lib/api/observability";
import type {
  StageTiming,
  BottleneckReport,
  ReworkReport,
  Scorecard,
} from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const observabilityKeys = {
  all: ["observability"] as const,
  cycleTime: (days: number, team?: string) =>
    [...observabilityKeys.all, "cycle-time", days, team ?? "all"] as const,
  bottlenecks: (days: number) =>
    [...observabilityKeys.all, "bottlenecks", days] as const,
  rework: (days: number, team?: string) =>
    [...observabilityKeys.all, "rework", days, team ?? "all"] as const,
  teamScorecard: (team: string, days: number) =>
    [...observabilityKeys.all, "scorecard", "team", team, days] as const,
};

// =============================================================================
// HOOKS
// =============================================================================

/** Per-stage cycle time (dwell per lifecycle status). */
export function useCycleTime(days = 30, team?: string) {
  return useQuery<StageTiming[]>({
    queryKey: observabilityKeys.cycleTime(days, team),
    queryFn: () => observabilityApi.getCycleTime(days, team),
    refetchInterval: 60_000,
  });
}

/** Bottleneck distribution: cumulative dwell + live parked counts. */
export function useBottlenecks(days = 30) {
  return useQuery<BottleneckReport>({
    queryKey: observabilityKeys.bottlenecks(days),
    queryFn: () => observabilityApi.getBottlenecks(days),
    refetchInterval: 60_000,
  });
}

/** Rework rate overall, by team, and by agent + cost. */
export function useRework(days = 30, team?: string) {
  return useQuery<ReworkReport>({
    queryKey: observabilityKeys.rework(days, team),
    queryFn: () => observabilityApi.getRework(days, team),
    refetchInterval: 60_000,
  });
}

/** Per-cell delivery scorecard. */
export function useTeamScorecard(team: string, days = 7) {
  return useQuery<Scorecard>({
    queryKey: observabilityKeys.teamScorecard(team, days),
    queryFn: () => observabilityApi.getTeamScorecard(team, days),
    refetchInterval: 60_000,
  });
}
