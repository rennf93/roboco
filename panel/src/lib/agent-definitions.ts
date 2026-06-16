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
      // MAIN_PM has its own dedicated section below Board, so exclude it here.
      a.role !== AgentRole.CEO &&
      a.role !== AgentRole.MAIN_PM &&
      (a.team === Team.BOARD ||
        a.role === AgentRole.HEAD_MARKETING ||
        a.role === AgentRole.AUDITOR ||
        a.role === AgentRole.PRODUCT_OWNER ||
        a.role === AgentRole.PR_REVIEWER)
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

// On-demand agents (Prompter/Intake, Secretary) are not part of any standing
// cell team — they are spawned on request. Captured by inclusion of their
// explicit roles so new on-demand agents appear automatically when roles
// are added to the enum.
export const getOnDemandAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter(
    (a) =>
      a.role === AgentRole.PROMPTER ||
      a.role === AgentRole.SECRETARY
  );
