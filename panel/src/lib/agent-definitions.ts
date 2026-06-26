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
      // The Board is exactly the three review/oversight roles: Product Owner,
      // Head of Marketing, and the Auditor. The CEO (human operator) and the
      // Main PM are not Board agents, and neither are the CEO-direct helpers
      // (Intake, Secretary, root PR Reviewer) — those are grouped as Support.
      a.role === AgentRole.PRODUCT_OWNER ||
      a.role === AgentRole.HEAD_MARKETING ||
      a.role === AgentRole.AUDITOR,
  );

export const getMainPm = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.role === AgentRole.MAIN_PM);

export const getBackendAgents = (
  agents: AgentDefinition[] | undefined | null,
) => (agents ?? []).filter((a) => a.team === Team.BACKEND);

export const getFrontendAgents = (
  agents: AgentDefinition[] | undefined | null,
) => (agents ?? []).filter((a) => a.team === Team.FRONTEND);

export const getUxAgents = (agents: AgentDefinition[] | undefined | null) =>
  (agents ?? []).filter((a) => a.team === Team.UX_UI);

export const getMarketingAgents = (
  agents: AgentDefinition[] | undefined | null,
) => (agents ?? []).filter((a) => a.team === Team.MARKETING);

// CEO-direct support roles — Intake (Prompter), Secretary, and the root PR
// Reviewer. Board-adjacent and spawned on demand, but NOT Board members. Cell
// PR reviewers carry their cell's team and stay grouped under that cell; only
// the root reviewer carries team=board, which is how it is distinguished here.
export const getSupportAgents = (
  agents: AgentDefinition[] | undefined | null,
) =>
  (agents ?? []).filter(
    (a) =>
      a.role === AgentRole.PROMPTER ||
      a.role === AgentRole.SECRETARY ||
      (a.role === AgentRole.PR_REVIEWER && a.team === Team.BOARD),
  );
