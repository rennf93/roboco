export * from "./use-tasks";
export * from "./use-agents";
export * from "./use-channels";
export * from "./use-notifications";
// Re-export dashboard hooks excluding duplicates from use-agents
export {
  dashboardKeys,
  useCeoOverview,
  useMetrics,
  useVelocityMetrics,
  useBlockerMetrics,
  useKanbanDev,
  useKanbanQa,
  useKanbanPm,
  useCeoTeamDetails,
  useCeoBlockerDetails,
  useCeoVelocity,
  useRecentActivity,
  useAgentStatus as useDashboardAgentStatus, // Renamed to avoid conflict
  useKanbanDocumenter,
  useKanbanCellPm,
  useKanbanBoard,
  useKanbanStats,
  useAuditorDashboard,
  useAuditorFlags,
  useAuditorReports,
  useCreateAuditorFlag,
  useResolveAuditorFlag,
  useCreateAuditorReport,
  useSendAuditorReport,
} from "./use-dashboard";
export * from "./use-websocket";
export * from "./use-journals";
export * from "./use-projects";
export * from "./use-work-sessions";
