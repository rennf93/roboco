/**
 * Agent Definitions
 *
 * Helper functions to filter agents by organizational group.
 * Agent data is now fetched from API via useAgentDefinitions() hook.
 */

import { AgentRole, Team } from "@/types";

export interface AgentDefinition {
  id: string;
  name: string;
  role: AgentRole | null;
  team: Team | null;
}

// Helper functions to filter agents by group
// These now accept agents as a parameter instead of using static data
// All functions handle undefined/null gracefully

export const getBoardAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter(
    (a) =>
      // The CEO is the human operator, not a spawnable agent — exclude it even
      // though its record carries team=board.
      // MAIN_PM is excluded here because it has its own dedicated section.
      a.role !== AgentRole.CEO &&
      a.role !== AgentRole.MAIN_PM &&
      (a.team === Team.BOARD ||
        a.role === AgentRole.HEAD_MARKETING ||
        a.role === AgentRole.AUDITOR ||
        a.role === AgentRole.PRODUCT_OWNER)
  );

export const getMainPm = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.role === AgentRole.MAIN_PM);

export const getBackendAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.team === Team.BACKEND);

export const getFrontendAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.team === Team.FRONTEND);

export const getUxAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.team === Team.UX_UI);

export const getMarketingAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.team === Team.MARKETING);

/**
 * On-demand agents: spawned only when needed, not part of any permanent cell.
 * Catches agents not matched by any standard section filter (board, main_pm,
 * backend, frontend, ux_ui, marketing) and not the human CEO.
 * Includes roles like "prompter" (Intake interviewer) that the API may return.
 */
export const getOnDemandAgents = (agents: AgentDefinition[] | undefined | null) => {
  const permanentTeams: (Team | null)[] = [
    Team.BOARD,
    Team.MAIN_PM,
    Team.BACKEND,
    Team.FRONTEND,
    Team.UX_UI,
    Team.MARKETING,
  ];
  const permanentRoles: (AgentRole | null)[] = [
    AgentRole.CEO,
    AgentRole.MAIN_PM,
    AgentRole.PRODUCT_OWNER,
    AgentRole.HEAD_MARKETING,
    AgentRole.AUDITOR,
    AgentRole.CELL_PM,
    AgentRole.DEVELOPER,
    AgentRole.QA,
    AgentRole.DOCUMENTER,
    AgentRole.SYSTEM,
  ];
  return (agents ?? []).filter(
    (a) =>
      !permanentTeams.includes(a.team) &&
      !permanentRoles.includes(a.role)
  );
};
