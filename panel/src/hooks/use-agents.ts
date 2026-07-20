import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import {
  orchestratorApi,
  type SpawnAgentRequest,
} from "@/lib/api/orchestrator";
import { agentsApi, type AgentDefinition } from "@/lib/api/agents";
import { registerAgentRoster } from "@/lib/agent-utils";
import type { Agent, AgentRole, Team, AgentState } from "@/types";

export type { AgentDefinition } from "@/lib/api/agents";

// Offline / first-paint fallback only — the live `/api/agents` roster
// (useAgentDefinitions) is authoritative. Keep this list in sync when adding
// agents, but it is not the source of truth and may lag the backend.
const AGENT_ROSTER: Agent[] = [
  // Board / Management
  {
    id: "1",
    agent_id: "main-pm",
    name: "Main PM",
    role: "main_pm" as AgentRole,
    team: null,
    cell: null,
    status: "idle" as AgentState,
  },
  {
    id: "2",
    agent_id: "product-owner",
    name: "Product Owner",
    role: "product_owner" as AgentRole,
    team: "board" as Team,
    cell: null,
    status: "idle" as AgentState,
  },
  {
    id: "3",
    agent_id: "head-marketing",
    name: "Head of Marketing",
    role: "head_marketing" as AgentRole,
    team: "board" as Team,
    cell: null,
    status: "idle" as AgentState,
  },
  {
    id: "4",
    agent_id: "auditor",
    name: "Auditor",
    role: "auditor" as AgentRole,
    team: null,
    cell: null,
    status: "idle" as AgentState,
  },
  // Board-adjacent singletons
  {
    id: "20",
    agent_id: "intake-1",
    name: "Intake",
    role: "prompter" as AgentRole,
    team: "board" as Team,
    cell: null,
    status: "idle" as AgentState,
  },
  {
    id: "21",
    agent_id: "secretary-1",
    name: "Secretary",
    role: "secretary" as AgentRole,
    team: "board" as Team,
    cell: null,
    status: "idle" as AgentState,
  },
  {
    id: "22",
    agent_id: "pr-reviewer-1",
    name: "PR Reviewer",
    role: "pr_reviewer" as AgentRole,
    team: "board" as Team,
    cell: null,
    status: "idle" as AgentState,
  },
  // Backend Cell
  {
    id: "5",
    agent_id: "be-dev-1",
    name: "Backend Dev 1",
    role: "developer" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  {
    id: "6",
    agent_id: "be-dev-2",
    name: "Backend Dev 2",
    role: "developer" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  {
    id: "7",
    agent_id: "be-qa",
    name: "Backend QA",
    role: "qa" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  {
    id: "8",
    agent_id: "be-pm",
    name: "Backend PM",
    role: "cell_pm" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  {
    id: "9",
    agent_id: "be-doc",
    name: "Backend Documenter",
    role: "documenter" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  {
    id: "23",
    agent_id: "be-pr-reviewer",
    name: "Backend PR Reviewer",
    role: "pr_reviewer" as AgentRole,
    team: "backend" as Team,
    cell: "backend",
    status: "idle" as AgentState,
  },
  // Frontend Cell
  {
    id: "10",
    agent_id: "fe-dev-1",
    name: "Frontend Dev 1",
    role: "developer" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  {
    id: "11",
    agent_id: "fe-dev-2",
    name: "Frontend Dev 2",
    role: "developer" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  {
    id: "12",
    agent_id: "fe-qa",
    name: "Frontend QA",
    role: "qa" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  {
    id: "13",
    agent_id: "fe-pm",
    name: "Frontend PM",
    role: "cell_pm" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  {
    id: "14",
    agent_id: "fe-doc",
    name: "Frontend Documenter",
    role: "documenter" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  {
    id: "24",
    agent_id: "fe-pr-reviewer",
    name: "Frontend PR Reviewer",
    role: "pr_reviewer" as AgentRole,
    team: "frontend" as Team,
    cell: "frontend",
    status: "idle" as AgentState,
  },
  // UX/UI Cell
  {
    id: "15",
    agent_id: "ux-dev-1",
    name: "UX/UI Dev 1",
    role: "developer" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
  {
    id: "16",
    agent_id: "ux-dev-2",
    name: "UX/UI Dev 2",
    role: "developer" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
  {
    id: "19",
    agent_id: "ux-qa",
    name: "UX/UI QA",
    role: "qa" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
  {
    id: "17",
    agent_id: "ux-pm",
    name: "UX/UI PM",
    role: "cell_pm" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
  {
    id: "18",
    agent_id: "ux-doc",
    name: "UX/UI Documenter",
    role: "documenter" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
  {
    id: "25",
    agent_id: "ux-pr-reviewer",
    name: "UX/UI PR Reviewer",
    role: "pr_reviewer" as AgentRole,
    team: "ux_ui" as Team,
    cell: "ux_ui",
    status: "idle" as AgentState,
  },
];

// Query keys
export const agentKeys = {
  all: ["agents"] as const,
  definitions: () => [...agentKeys.all, "definitions"] as const,
  orchestrator: () => [...agentKeys.all, "orchestrator"] as const,
  status: () => [...agentKeys.orchestrator(), "status"] as const,
  waiting: () => [...agentKeys.orchestrator(), "waiting"] as const,
  agent: (id: string) => [...agentKeys.orchestrator(), "agent", id] as const,
};

// Fetch agent definitions from API
export function useAgentDefinitions() {
  return useQuery({
    queryKey: agentKeys.definitions(),
    queryFn: agentsApi.getAll,
    staleTime: 5 * 60 * 1000, // 5 min - agents don't change often
  });
}

/**
 * Register the live `/api/agents` roster into the display-name resolver
 * (agent-utils). Mount once near the app root so every surface that resolves an
 * assignee (task table, task detail, journals, commits) shows
 * the real agent name instead of a raw UUID, and never drifts as agents are
 * added backend-side. Returns nothing — it's a side-effecting sync.
 */
export function useAgentRosterSync(): void {
  const { data: definitions } = useAgentDefinitions();
  useEffect(() => {
    if (definitions && definitions.length > 0) {
      registerAgentRoster(
        definitions.map((d) => ({ uuid: d.uuid, slug: d.id, name: d.name })),
      );
    }
  }, [definitions]);
}

// Map team → cell (cells carry a cell name; board/management agents have none).
const TEAM_CELLS: ReadonlyArray<Team> = [
  "backend",
  "frontend",
  "ux_ui",
] as Team[];

function definitionToAgent(def: AgentDefinition): Agent {
  return {
    id: def.uuid,
    agent_id: def.id,
    name: def.name,
    role: (def.role ?? "developer") as AgentRole,
    team: def.team,
    cell: def.team && TEAM_CELLS.includes(def.team) ? def.team : null,
    status: "idle" as AgentState,
  };
}

// Hooks

// Returns the agent roster (live definitions when loaded, static fallback
// otherwise), enriched with live orchestrator status.
export function useAgents() {
  const { data: definitions } = useAgentDefinitions();
  const { data: orchestratorStatus } = useOrchestratorStatus();

  // A stable signature so the derived roster refetches when the live set
  // changes (react-query keys on this, not on the closure).
  const rosterKey = definitions?.map((d) => d.id).join(",") ?? "static";
  // Re-derive the roster when the live status snapshot changes — keying on the
  // roster itself would be circular (never changes).
  const statusEpoch =
    orchestratorStatus?.agents
      ?.map((a) => `${a.agent_id}:${a.state}`)
      .join(",") ?? "none";

  return useQuery({
    queryKey: [...agentKeys.all, "roster", rosterKey, statusEpoch],
    queryFn: async (): Promise<Agent[]> => {
      // Build a map of agent statuses from the orchestrator status array
      const statusMap = new Map<string, string>();
      if (orchestratorStatus?.agents) {
        for (const agentStatus of orchestratorStatus.agents) {
          statusMap.set(agentStatus.agent_id, agentStatus.state);
        }
      }

      const base: Agent[] =
        definitions && definitions.length > 0
          ? definitions.map(definitionToAgent)
          : AGENT_ROSTER;

      // Enrich with live status from orchestrator
      return base.map((agent) => {
        const liveState = statusMap.get(agent.agent_id);
        return {
          ...agent,
          status: (liveState as AgentState) ?? agent.status,
        };
      });
    },
    staleTime: 5 * 60 * 1000, // 5 min — allows roster to refresh when orchestrator status changes
    enabled: true,
  });
}

export function useOrchestratorStatus() {
  return useQuery({
    queryKey: agentKeys.status(),
    queryFn: () => orchestratorApi.getStatus(),
    refetchInterval: 10000, // Refetch every 10 seconds
  });
}

export function useAgentStatus(agentId: string) {
  return useQuery({
    queryKey: agentKeys.agent(agentId),
    queryFn: () => orchestratorApi.getAgentStatus(agentId),
    enabled: !!agentId,
    refetchInterval: 5000, // Refetch every 5 seconds
    // A 404 (agent not running) is deterministic, not transient — retrying
    // just delays settling into the degraded "not running" state.
    retry: false,
  });
}

export function useWaitingAgents() {
  return useQuery({
    queryKey: agentKeys.waiting(),
    queryFn: () => orchestratorApi.getWaitingAgents(),
    refetchInterval: 10000,
  });
}

export function useSpawnAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      agentId,
      request,
    }: {
      agentId: string;
      request?: SpawnAgentRequest;
    }) => orchestratorApi.spawn(agentId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.orchestrator() });
    },
  });
}

export function useStopAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      agentId,
      graceful = true,
    }: {
      agentId: string;
      graceful?: boolean;
    }) => orchestratorApi.stop(agentId, graceful),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.orchestrator() });
    },
  });
}

export function useResolveWait() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      agentId,
      resolution,
    }: {
      agentId: string;
      resolution: string;
    }) => orchestratorApi.resolveWait(agentId, resolution),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.orchestrator() });
    },
  });
}

// Fetch a single agent definition by ID or slug
export function useAgentDefinition(agentId: string) {
  return useQuery({
    queryKey: [...agentKeys.definitions(), agentId],
    queryFn: () => agentsApi.getOne(agentId),
    enabled: !!agentId,
    staleTime: 5 * 60 * 1000, // 5 min - agent definitions don't change often
  });
}
