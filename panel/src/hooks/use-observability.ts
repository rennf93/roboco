"use client";

import { useQuery } from "@tanstack/react-query";
import { observabilityApi } from "@/lib/api/observability";
import type {
  StageTiming,
  BottleneckReport,
  ReworkReport,
  Scorecard,
  CeoScorecard,
  MemberScorecard,
  OrgScorecard,
  TaskMetrics,
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
  ceoScorecard: (days: number) =>
    [...observabilityKeys.all, "scorecard", "ceo", days] as const,
  memberScorecard: (agentId: string, days: number) =>
    [...observabilityKeys.all, "scorecard", "member", agentId, days] as const,
  orgScorecard: (days: number, team?: string) =>
    [...observabilityKeys.all, "scorecard", "org", team ?? "all", days] as const,
  taskMetrics: (taskId: string) =>
    [...observabilityKeys.all, "task-metrics", taskId] as const,
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

/** The human CEO as a measured member (approval/unblock dwell + god-mode). */
export function useCeoScorecard(days = 30) {
  return useQuery<CeoScorecard>({
    queryKey: observabilityKeys.ceoScorecard(days),
    queryFn: () => observabilityApi.getCeoScorecard(days),
    refetchInterval: 60_000,
  });
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Only real member ids may fetch a scorecard: an agent UUID or the "ceo"
 * alias. The static fallback roster carries placeholder ids ("1".."22")
 * while agent definitions load — fetching those fired 22 guaranteed-422
 * requests per cycle and queued the whole metrics page behind them. */
export function isScorecardMemberId(agentId: string): boolean {
  return agentId === "ceo" || UUID_RE.test(agentId);
}

/** Per-member rollup scorecard (+ live in-flight overlay). */
export function useMemberScorecard(agentId: string, days = 30) {
  return useQuery<MemberScorecard>({
    queryKey: observabilityKeys.memberScorecard(agentId, days),
    queryFn: () => observabilityApi.getMemberScorecard(agentId, days),
    enabled: isScorecardMemberId(agentId),
    refetchInterval: 60_000,
  });
}

/** Org-wide (or per-cell) rollup aggregate. */
export function useOrgScorecard(days = 30, team?: string) {
  return useQuery<OrgScorecard>({
    queryKey: observabilityKeys.orgScorecard(days, team),
    queryFn: () => observabilityApi.getOrgScorecard(days, team),
    refetchInterval: 60_000,
  });
}

/** Granular per-task metrics (active-vs-wait drill-down). */
export function useTaskMetrics(taskId: string) {
  return useQuery<TaskMetrics | null>({
    queryKey: observabilityKeys.taskMetrics(taskId),
    queryFn: () => observabilityApi.getTaskMetrics(taskId),
    enabled: Boolean(taskId),
  });
}
