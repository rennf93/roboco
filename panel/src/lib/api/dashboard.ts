import api from "./client";
import { Team, TaskStatus } from "@/types";
import type {
  KanbanBoard,
  AuditorDashboard,
  AuditorFlag,
  AuditorReport,
  FlagSeverity,
  CEOOverview as CEOOverviewType,
  Task,
} from "@/types";
import {
  isMockMode,
  mockDashboardStats,
  mockTeamHealth,
  getMockRecentActivity,
  mockAuditorDashboard,
  mockAuditorFlags,
  mockAuditorReports,
  mockTasks,
  mockOrchestratorStatus,
} from "@/lib/mock-data";

// Re-export types from @/types for backward compatibility
export type { CEOOverviewType as CEOOverview };

export interface TeamHealth {
  team: Team;
  health_score: number;
  active_tasks: number;
  blocked_tasks: number;
  completed_today: number;
}

export interface MetricsSummary {
  velocity: VelocityMetric;
  blockers: BlockerMetric;
  communication: CommunicationMetric;
  agents: AgentMetric;
}

export interface VelocityMetric {
  tasks_completed_today: number;
  tasks_completed_week: number;
  average_completion_time_hours: number;
}

export interface BlockerMetric {
  total_blocked: number;
  blocked_by_team: Record<string, number>;
  longest_blocked_hours: number;
}

export interface CommunicationMetric {
  messages_today: number;
  active_channels: number;
  notifications_pending: number;
}

export interface AgentMetric {
  total_agents: number;
  running: number;
  idle: number;
  waiting: number;
  errors: number;
}

// =============================================================================
// AUDITOR API TYPES
// =============================================================================

export interface CreateFlagRequest {
  severity: FlagSeverity;
  category: string;
  title: string;
  description: string;
  related_task_id?: string;
  related_agent_id?: string;
}

export interface CreateReportRequest {
  report_type: string;
  title: string;
  summary: string;
  sections: Array<Record<string, unknown>>;
}

// Helper to create mock kanban board from tasks
function createMockKanbanBoard(tasks: Task[], team?: Team): KanbanBoard {
  const filteredTasks = team ? tasks.filter((t) => t.team === team) : tasks;
  const pendingTasks = filteredTasks.filter((t) => t.status === TaskStatus.PENDING);
  const inProgressTasks = filteredTasks.filter((t) => t.status === TaskStatus.IN_PROGRESS);
  const blockedTasks = filteredTasks.filter((t) => t.status === TaskStatus.BLOCKED);
  const awaitingQaTasks = filteredTasks.filter((t) => t.status === TaskStatus.AWAITING_QA);
  const completedTasks = filteredTasks.filter((t) => t.status === TaskStatus.COMPLETED);

  return {
    columns: [
      {
        id: "pending",
        title: "Pending",
        status: TaskStatus.PENDING,
        tasks: pendingTasks,
        count: pendingTasks.length,
      },
      {
        id: "in_progress",
        title: "In Progress",
        status: TaskStatus.IN_PROGRESS,
        tasks: inProgressTasks,
        count: inProgressTasks.length,
      },
      {
        id: "blocked",
        title: "Blocked",
        status: TaskStatus.BLOCKED,
        tasks: blockedTasks,
        count: blockedTasks.length,
      },
      {
        id: "awaiting_qa",
        title: "Awaiting QA",
        status: TaskStatus.AWAITING_QA,
        tasks: awaitingQaTasks,
        count: awaitingQaTasks.length,
      },
      {
        id: "completed",
        title: "Completed",
        status: TaskStatus.COMPLETED,
        tasks: completedTasks,
        count: completedTasks.length,
      },
    ],
    total_tasks: filteredTasks.length,
  };
}

export const dashboardApi = {
  // Get CEO overview
  getCeoOverview: async (): Promise<CEOOverviewType> => {
    if (isMockMode()) {
      return {
        health_status: mockTeamHealth,
        key_metrics: {
          total_tasks: mockDashboardStats.total_tasks,
          tasks_in_progress: mockDashboardStats.tasks_in_progress,
          tasks_blocked: mockDashboardStats.tasks_blocked,
          tasks_completed_today: mockDashboardStats.tasks_completed_today,
          active_agents: mockDashboardStats.active_agents,
        },
        auditor_alerts: {},
        roadmap_progress: {
          phase_1: 100,
          phase_2: 75,
          phase_3: 25,
        },
      };
    }
    const { data } = await api.get<CEOOverviewType>("/dashboard/ceo");
    return data;
  },

  // Get metrics - individual endpoints
  getVelocityMetrics: async (): Promise<VelocityMetric> => {
    if (isMockMode()) {
      return {
        tasks_completed_today: mockDashboardStats.tasks_completed_today,
        tasks_completed_week: 15,
        average_completion_time_hours: 4.5,
      };
    }
    const { data } = await api.get<VelocityMetric>("/dashboard/metrics/velocity");
    return data;
  },

  getBlockerMetrics: async (): Promise<BlockerMetric> => {
    if (isMockMode()) {
      const blockedTasks = mockTasks.filter((t) => t.status === TaskStatus.BLOCKED);
      return {
        total_blocked: blockedTasks.length,
        blocked_by_team: {
          backend: blockedTasks.filter((t) => t.team === Team.BACKEND).length,
          frontend: blockedTasks.filter((t) => t.team === Team.FRONTEND).length,
          ux_ui: blockedTasks.filter((t) => t.team === Team.UX_UI).length,
          marketing: blockedTasks.filter((t) => t.team === Team.MARKETING).length,
        },
        longest_blocked_hours: 24,
      };
    }
    const { data } = await api.get<BlockerMetric>("/dashboard/metrics/blockers");
    return data;
  },

  getCommunicationMetrics: async (): Promise<CommunicationMetric> => {
    if (isMockMode()) {
      return {
        messages_today: 45,
        active_channels: 5,
        notifications_pending: 3,
      };
    }
    const { data } = await api.get<CommunicationMetric>("/dashboard/metrics/communication");
    return data;
  },

  getHealthMetrics: async () => {
    if (isMockMode()) {
      return mockTeamHealth;
    }
    const { data } = await api.get("/dashboard/metrics/health");
    return data;
  },

  // Get kanban board for a team (dev view)
  getKanbanDev: async (team: Team): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks, team);
    }
    const { data } = await api.get<KanbanBoard>("/kanban/dev/" + team);
    return data;
  },

  // Get QA kanban board
  getKanbanQa: async (team: Team): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks, team);
    }
    const { data } = await api.get<KanbanBoard>("/kanban/qa/" + team);
    return data;
  },

  // Get PM kanban board (cross-team view)
  getKanbanPm: async (): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks);
    }
    const { data } = await api.get<KanbanBoard>("/kanban/main-pm");
    return data;
  },

  // Get agent status
  getAgentStatus: async () => {
    if (isMockMode()) {
      return mockOrchestratorStatus;
    }
    const { data } = await api.get("/dashboard/agents/status");
    return data;
  },

  // Get recent activity
  getRecentActivity: async (hours: number = 24, limit: number = 50) => {
    if (isMockMode()) {
      return getMockRecentActivity().slice(0, limit);
    }
    const { data } = await api.get<{ period_hours: number; activity: unknown[] }>("/dashboard/activity/recent", {
      params: { hours, limit },
    });
    // Backend returns { period_hours, activity }, extract the activity array
    return data.activity ?? [];
  },

  // =============================================================================
  // AUDITOR DASHBOARD ENDPOINTS
  // =============================================================================

  // Get complete auditor dashboard
  getAuditorDashboard: async (): Promise<AuditorDashboard> => {
    if (isMockMode()) {
      return mockAuditorDashboard as AuditorDashboard;
    }
    const { data } = await api.get<AuditorDashboard>("/dashboard/auditor");
    return data;
  },

  // Get auditor flags
  getAuditorFlags: async (params?: {
    severity?: FlagSeverity;
    resolved?: boolean;
  }): Promise<AuditorFlag[]> => {
    if (isMockMode()) {
      let flags = [...mockAuditorFlags] as AuditorFlag[];
      if (params?.severity) {
        flags = flags.filter((f) => f.severity === params.severity);
      }
      if (params?.resolved !== undefined) {
        flags = flags.filter((f) => (params.resolved ? f.resolved_at : !f.resolved_at));
      }
      return flags;
    }
    const { data } = await api.get<AuditorFlag[]>("/dashboard/auditor/flags", {
      params,
    });
    return data;
  },

  // Create an auditor flag
  createAuditorFlag: async (
    request: CreateFlagRequest
  ): Promise<AuditorFlag> => {
    if (isMockMode()) {
      const newFlag: AuditorFlag = {
        id: `flag-${Date.now()}`,
        severity: request.severity,
        category: request.category,
        title: request.title,
        description: request.description,
        related_task_id: request.related_task_id ?? null,
        related_agent_id: request.related_agent_id ?? null,
        created_at: new Date().toISOString(),
        resolved_at: null,
        notes: null,
      };
      (mockAuditorFlags as AuditorFlag[]).push(newFlag);
      return newFlag;
    }
    const { data } = await api.post<AuditorFlag>(
      "/dashboard/auditor/flags",
      request
    );
    return data;
  },

  // Resolve an auditor flag
  resolveAuditorFlag: async (
    flagId: string,
    notes?: string
  ): Promise<{ status: string; flag_id: string }> => {
    if (isMockMode()) {
      const flags = mockAuditorFlags as AuditorFlag[];
      const idx = flags.findIndex((f) => f.id === flagId);
      if (idx !== -1) {
        flags[idx] = {
          ...flags[idx],
          resolved_at: new Date().toISOString(),
          notes: notes ?? null,
        };
        return { status: "resolved", flag_id: flagId };
      }
      throw new Error("Flag not found");
    }
    const { data } = await api.put(`/dashboard/auditor/flags/${flagId}/resolve`, null, {
      params: { notes },
    });
    return data;
  },

  // Get auditor reports
  getAuditorReports: async (params?: {
    report_type?: string;
    limit?: number;
  }): Promise<AuditorReport[]> => {
    if (isMockMode()) {
      let reports = [...mockAuditorReports] as AuditorReport[];
      if (params?.report_type) {
        reports = reports.filter((r) => r.report_type === params.report_type);
      }
      if (params?.limit) {
        reports = reports.slice(0, params.limit);
      }
      return reports;
    }
    const { data } = await api.get<AuditorReport[]>(
      "/dashboard/auditor/reports",
      { params }
    );
    return data;
  },

  // Create an auditor report
  createAuditorReport: async (
    request: CreateReportRequest
  ): Promise<AuditorReport> => {
    if (isMockMode()) {
      const newReport: AuditorReport = {
        id: `report-${Date.now()}`,
        report_type: request.report_type,
        title: request.title,
        summary: request.summary,
        sections: request.sections,
        created_at: new Date().toISOString(),
        sent_at: null,
      };
      (mockAuditorReports as AuditorReport[]).push(newReport);
      return newReport;
    }
    const { data } = await api.post<AuditorReport>(
      "/dashboard/auditor/reports",
      request
    );
    return data;
  },

  // Send a report to CEO
  sendAuditorReport: async (
    reportId: string
  ): Promise<{ status: string; report_id: string }> => {
    if (isMockMode()) {
      const reports = mockAuditorReports as AuditorReport[];
      const idx = reports.findIndex((r) => r.id === reportId);
      if (idx !== -1) {
        reports[idx] = {
          ...reports[idx],
          sent_at: new Date().toISOString(),
        };
        return { status: "sent", report_id: reportId };
      }
      throw new Error("Report not found");
    }
    const { data } = await api.post(
      `/dashboard/auditor/reports/${reportId}/send`
    );
    return data;
  },

  // =============================================================================
  // CEO DETAIL ENDPOINTS
  // =============================================================================

  // Get detailed team metrics
  getCeoTeamDetails: async () => {
    if (isMockMode()) {
      return mockTeamHealth.map((th) => ({
        ...th,
        velocity_7d: 10,
        avg_completion_time: 4.5,
        agent_count: 5,
      }));
    }
    const { data } = await api.get("/dashboard/ceo/teams");
    return data;
  },

  // Get blocker details for CEO
  getCeoBlockerDetails: async () => {
    if (isMockMode()) {
      const blockedTasks = mockTasks.filter((t) => t.status === TaskStatus.BLOCKED);
      return {
        total_blocked: blockedTasks.length,
        blockers: blockedTasks.map((t) => ({
          task_id: t.id,
          title: t.title,
          team: t.team,
          blocked_hours: 24,
          blocker_reason: "Dependency not resolved",
        })),
      };
    }
    const { data } = await api.get("/dashboard/ceo/blockers");
    return data;
  },

  // Get velocity metrics for CEO
  getCeoVelocity: async (days: number = 7) => {
    if (isMockMode()) {
      return {
        period_days: days,
        total_completed: 15,
        by_team: {
          backend: 6,
          frontend: 5,
          ux_ui: 4,
          marketing: 0,
        },
        daily_breakdown: Array.from({ length: days }, (_, i) => ({
          date: new Date(Date.now() - i * 24 * 60 * 60 * 1000).toISOString().split("T")[0],
          completed: Math.floor(Math.random() * 5) + 1,
        })),
      };
    }
    const { data } = await api.get("/dashboard/ceo/velocity", {
      params: { days },
    });
    return data;
  },

  // =============================================================================
  // KANBAN ENDPOINTS (Role-specific views)
  // =============================================================================

  // Get documenter kanban board
  getKanbanDocumenter: async (team: Team): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks, team);
    }
    const { data } = await api.get<KanbanBoard>(`/kanban/documenter/${team}`);
    return data;
  },

  // Get cell PM kanban board
  getKanbanCellPm: async (team: Team): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks, team);
    }
    const { data } = await api.get<KanbanBoard>(`/kanban/pm/${team}`);
    return data;
  },

  // Get board-level roadmap kanban
  getKanbanBoard: async (): Promise<KanbanBoard> => {
    if (isMockMode()) {
      return createMockKanbanBoard(mockTasks);
    }
    const { data } = await api.get<KanbanBoard>("/kanban/board");
    return data;
  },

  // Get kanban statistics
  getKanbanStats: async (team?: Team) => {
    if (isMockMode()) {
      const tasks = team ? mockTasks.filter((t) => t.team === team) : mockTasks;
      return {
        total: tasks.length,
        by_status: {
          pending: tasks.filter((t) => t.status === TaskStatus.PENDING).length,
          in_progress: tasks.filter((t) => t.status === TaskStatus.IN_PROGRESS).length,
          blocked: tasks.filter((t) => t.status === TaskStatus.BLOCKED).length,
          awaiting_qa: tasks.filter((t) => t.status === TaskStatus.AWAITING_QA).length,
          completed: tasks.filter((t) => t.status === TaskStatus.COMPLETED).length,
        },
      };
    }
    const { data } = await api.get("/kanban/stats", { params: { team } });
    return data;
  },

  // Get metrics for a specific agent
  getAgentMetrics: async (agentId: string) => {
    if (isMockMode()) {
      return {
        agent_id: agentId,
        tasks_completed: 10,
        tasks_in_progress: 2,
        avg_completion_time_hours: 8.5,
        quality_score: 0.92,
      };
    }
    const { data } = await api.get(`/dashboard/metrics/agent/${agentId}`);
    return data;
  },

  // Get metrics for a specific team
  getTeamMetrics: async (team: Team) => {
    if (isMockMode()) {
      return {
        team,
        tasks_total: 25,
        tasks_completed: 15,
        tasks_in_progress: 5,
        tasks_blocked: 2,
        velocity: 8.5,
        quality_score: 0.88,
      };
    }
    const { data } = await api.get(`/dashboard/metrics/team/${team}`);
    return data;
  },

  // Get kanban board for any team (generic endpoint)
  getKanbanForTeam: async (team: Team): Promise<KanbanBoard> => {
    if (isMockMode()) {
      const teamTasks = mockTasks.filter((t) => t.team === team);
      return createMockKanbanBoard(teamTasks);
    }
    const { data } = await api.get<KanbanBoard>(`/dashboard/kanban/${team}`);
    return data;
  },
};
