"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  dashboardApi,
  MetricsSummary,
  CreateFlagRequest,
  CreateReportRequest,
} from "@/lib/api/dashboard";
import type {
  Team,
  AuditorDashboard,
  AuditorFlag,
  AuditorReport,
  FlagSeverity,
} from "@/types";

export const dashboardKeys = {
  all: ["dashboard"] as const,
  ceoOverview: () => [...dashboardKeys.all, "ceo-overview"] as const,
  ceoTeams: () => [...dashboardKeys.all, "ceo-teams"] as const,
  ceoBlockers: () => [...dashboardKeys.all, "ceo-blockers"] as const,
  ceoVelocity: (days: number) =>
    [...dashboardKeys.all, "ceo-velocity", days] as const,
  metrics: () => [...dashboardKeys.all, "metrics"] as const,
  velocity: () => [...dashboardKeys.all, "velocity"] as const,
  blockers: () => [...dashboardKeys.all, "blockers"] as const,
  activity: (hours: number) =>
    [...dashboardKeys.all, "activity", hours] as const,
  agentStatus: () => [...dashboardKeys.all, "agent-status"] as const,
  // Kanban keys
  kanbanDev: (team: Team) =>
    [...dashboardKeys.all, "kanban", "dev", team] as const,
  kanbanQa: (team: Team) =>
    [...dashboardKeys.all, "kanban", "qa", team] as const,
  kanbanDocumenter: (team: Team) =>
    [...dashboardKeys.all, "kanban", "documenter", team] as const,
  kanbanCellPm: (team: Team) =>
    [...dashboardKeys.all, "kanban", "cell-pm", team] as const,
  kanbanPm: () => [...dashboardKeys.all, "kanban", "pm"] as const,
  kanbanBoard: () => [...dashboardKeys.all, "kanban", "board"] as const,
  kanbanStats: (team?: Team) =>
    [...dashboardKeys.all, "kanban", "stats", team] as const,
  // Auditor keys
  auditor: () => [...dashboardKeys.all, "auditor"] as const,
  auditorFlags: (params?: { severity?: FlagSeverity; resolved?: boolean }) =>
    [...dashboardKeys.all, "auditor", "flags", params] as const,
  auditorReports: (params?: { report_type?: string; limit?: number }) =>
    [...dashboardKeys.all, "auditor", "reports", params] as const,
};

export function useCeoOverview() {
  return useQuery({
    queryKey: dashboardKeys.ceoOverview(),
    queryFn: () => dashboardApi.getCeoOverview(),
    refetchInterval: 60000, // Refetch every minute
  });
}

export function useMetrics() {
  return useQuery({
    queryKey: dashboardKeys.metrics(),
    queryFn: async (): Promise<MetricsSummary> => {
      // Fetch all metrics in parallel
      const [velocity, blockers, communication] = await Promise.all([
        dashboardApi.getVelocityMetrics(),
        dashboardApi.getBlockerMetrics(),
        dashboardApi.getCommunicationMetrics(),
      ]);
      return {
        velocity,
        blockers,
        communication,
        agents: { total_agents: 0, running: 0, idle: 0, waiting: 0, errors: 0 },
      };
    },
    refetchInterval: 60000,
  });
}

export function useVelocityMetrics() {
  return useQuery({
    queryKey: dashboardKeys.velocity(),
    queryFn: () => dashboardApi.getVelocityMetrics(),
    refetchInterval: 60000,
  });
}

export function useBlockerMetrics() {
  return useQuery({
    queryKey: dashboardKeys.blockers(),
    queryFn: () => dashboardApi.getBlockerMetrics(),
    refetchInterval: 60000,
  });
}

export function useKanbanDev(team: Team) {
  return useQuery({
    queryKey: dashboardKeys.kanbanDev(team),
    queryFn: () => dashboardApi.getKanbanDev(team),
    refetchInterval: 30000,
  });
}

export function useKanbanQa(team: Team) {
  return useQuery({
    queryKey: dashboardKeys.kanbanQa(team),
    queryFn: () => dashboardApi.getKanbanQa(team),
    refetchInterval: 30000,
  });
}

export function useKanbanPm() {
  return useQuery({
    queryKey: dashboardKeys.kanbanPm(),
    queryFn: () => dashboardApi.getKanbanPm(),
    refetchInterval: 30000,
  });
}

// =============================================================================
// ADDITIONAL CEO HOOKS
// =============================================================================

export function useCeoTeamDetails() {
  return useQuery({
    queryKey: dashboardKeys.ceoTeams(),
    queryFn: () => dashboardApi.getCeoTeamDetails(),
    refetchInterval: 60000,
  });
}

export function useCeoBlockerDetails() {
  return useQuery({
    queryKey: dashboardKeys.ceoBlockers(),
    queryFn: () => dashboardApi.getCeoBlockerDetails(),
    refetchInterval: 60000,
  });
}

export function useCeoVelocity(days: number = 7) {
  return useQuery({
    queryKey: dashboardKeys.ceoVelocity(days),
    queryFn: () => dashboardApi.getCeoVelocity(days),
    refetchInterval: 60000,
  });
}

export function useRecentActivity(hours: number = 24) {
  return useQuery({
    queryKey: dashboardKeys.activity(hours),
    queryFn: () => dashboardApi.getRecentActivity(hours),
    refetchInterval: 30000,
  });
}

export function useAgentStatus() {
  return useQuery({
    queryKey: dashboardKeys.agentStatus(),
    queryFn: () => dashboardApi.getAgentStatus(),
    refetchInterval: 10000,
  });
}

// =============================================================================
// ADDITIONAL KANBAN HOOKS
// =============================================================================

export function useKanbanDocumenter(team: Team) {
  return useQuery({
    queryKey: dashboardKeys.kanbanDocumenter(team),
    queryFn: () => dashboardApi.getKanbanDocumenter(team),
    refetchInterval: 30000,
  });
}

export function useKanbanCellPm(team: Team) {
  return useQuery({
    queryKey: dashboardKeys.kanbanCellPm(team),
    queryFn: () => dashboardApi.getKanbanCellPm(team),
    refetchInterval: 30000,
  });
}

export function useKanbanBoard() {
  return useQuery({
    queryKey: dashboardKeys.kanbanBoard(),
    queryFn: () => dashboardApi.getKanbanBoard(),
    refetchInterval: 30000,
  });
}

export function useKanbanStats(team?: Team) {
  return useQuery({
    queryKey: dashboardKeys.kanbanStats(team),
    queryFn: () => dashboardApi.getKanbanStats(team),
    refetchInterval: 60000,
  });
}

// =============================================================================
// AUDITOR DASHBOARD HOOKS
// =============================================================================

/**
 * Get the complete auditor dashboard
 */
export function useAuditorDashboard() {
  return useQuery<AuditorDashboard>({
    queryKey: dashboardKeys.auditor(),
    queryFn: () => dashboardApi.getAuditorDashboard(),
    refetchInterval: 30000,
  });
}

/**
 * Get auditor flags with optional filters
 */
export function useAuditorFlags(params?: {
  severity?: FlagSeverity;
  resolved?: boolean;
}) {
  return useQuery<AuditorFlag[]>({
    queryKey: dashboardKeys.auditorFlags(params),
    queryFn: () => dashboardApi.getAuditorFlags(params),
    refetchInterval: 30000,
  });
}

/**
 * Get auditor reports
 */
export function useAuditorReports(params?: {
  report_type?: string;
  limit?: number;
}) {
  return useQuery<AuditorReport[]>({
    queryKey: dashboardKeys.auditorReports(params),
    queryFn: () => dashboardApi.getAuditorReports(params),
    refetchInterval: 60000,
  });
}

/**
 * Create a new auditor flag
 */
export function useCreateAuditorFlag() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateFlagRequest) => dashboardApi.createAuditorFlag(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.auditor() });
      queryClient.invalidateQueries({
        queryKey: dashboardKeys.auditorFlags(),
      });
    },
  });
}

/**
 * Resolve an auditor flag
 */
export function useResolveAuditorFlag() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ flagId, notes }: { flagId: string; notes?: string }) =>
      dashboardApi.resolveAuditorFlag(flagId, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.auditor() });
      queryClient.invalidateQueries({
        queryKey: dashboardKeys.auditorFlags(),
      });
    },
  });
}

/**
 * Create a new auditor report
 */
export function useCreateAuditorReport() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateReportRequest) =>
      dashboardApi.createAuditorReport(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.auditor() });
      queryClient.invalidateQueries({
        queryKey: dashboardKeys.auditorReports(),
      });
    },
  });
}

/**
 * Send an auditor report to CEO
 */
export function useSendAuditorReport() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (reportId: string) => dashboardApi.sendAuditorReport(reportId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.auditor() });
      queryClient.invalidateQueries({
        queryKey: dashboardKeys.auditorReports(),
      });
    },
  });
}
