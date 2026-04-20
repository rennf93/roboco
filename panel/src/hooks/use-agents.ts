import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { orchestratorApi, type SpawnAgentRequest } from "@/lib/api/orchestrator";
import { agentsApi } from "@/lib/api/agents";
import type { Agent, AgentRole, Team, AgentState } from "@/types";

export type { AgentDefinition } from "@/lib/api/agents";

// Static agent roster for RoboCo (18 agents)
const AGENT_ROSTER: Agent[] = [
  // Board / Management
  { id: "1", agent_id: "main-pm", name: "Main PM", role: "main_pm" as AgentRole, team: null, cell: null, status: "idle" as AgentState },
  { id: "2", agent_id: "product-owner", name: "Product Owner", role: "product_owner" as AgentRole, team: "board" as Team, cell: null, status: "idle" as AgentState },
  { id: "3", agent_id: "head-marketing", name: "Head of Marketing", role: "head_marketing" as AgentRole, team: "board" as Team, cell: null, status: "idle" as AgentState },
  { id: "4", agent_id: "auditor", name: "Auditor", role: "auditor" as AgentRole, team: null, cell: null, status: "idle" as AgentState },
  // Backend Cell
  { id: "5", agent_id: "be-dev-1", name: "Backend Dev 1", role: "developer" as AgentRole, team: "backend" as Team, cell: "backend", status: "idle" as AgentState },
  { id: "6", agent_id: "be-dev-2", name: "Backend Dev 2", role: "developer" as AgentRole, team: "backend" as Team, cell: "backend", status: "idle" as AgentState },
  { id: "7", agent_id: "be-qa", name: "Backend QA", role: "qa" as AgentRole, team: "backend" as Team, cell: "backend", status: "idle" as AgentState },
  { id: "8", agent_id: "be-pm", name: "Backend PM", role: "cell_pm" as AgentRole, team: "backend" as Team, cell: "backend", status: "idle" as AgentState },
  { id: "9", agent_id: "be-doc", name: "Backend Documenter", role: "documenter" as AgentRole, team: "backend" as Team, cell: "backend", status: "idle" as AgentState },
  // Frontend Cell
  { id: "10", agent_id: "fe-dev-1", name: "Frontend Dev 1", role: "developer" as AgentRole, team: "frontend" as Team, cell: "frontend", status: "idle" as AgentState },
  { id: "11", agent_id: "fe-dev-2", name: "Frontend Dev 2", role: "developer" as AgentRole, team: "frontend" as Team, cell: "frontend", status: "idle" as AgentState },
  { id: "12", agent_id: "fe-qa", name: "Frontend QA", role: "qa" as AgentRole, team: "frontend" as Team, cell: "frontend", status: "idle" as AgentState },
  { id: "13", agent_id: "fe-pm", name: "Frontend PM", role: "cell_pm" as AgentRole, team: "frontend" as Team, cell: "frontend", status: "idle" as AgentState },
  { id: "14", agent_id: "fe-doc", name: "Frontend Documenter", role: "documenter" as AgentRole, team: "frontend" as Team, cell: "frontend", status: "idle" as AgentState },
  // UX/UI Cell
  { id: "15", agent_id: "ux-dev-1", name: "UX/UI Dev 1", role: "developer" as AgentRole, team: "ux_ui" as Team, cell: "ux_ui", status: "idle" as AgentState },
  { id: "16", agent_id: "ux-dev-2", name: "UX/UI Dev 2", role: "developer" as AgentRole, team: "ux_ui" as Team, cell: "ux_ui", status: "idle" as AgentState },
  { id: "16", agent_id: "ux-qa", name: "UX/UI QA", role: "qa" as AgentRole, team: "ux_ui" as Team, cell: "ux_ui", status: "idle" as AgentState },
  { id: "17", agent_id: "ux-pm", name: "UX/UI PM", role: "cell_pm" as AgentRole, team: "ux_ui" as Team, cell: "ux_ui", status: "idle" as AgentState },
  { id: "18", agent_id: "ux-doc", name: "UX/UI Documenter", role: "documenter" as AgentRole, team: "ux_ui" as Team, cell: "ux_ui", status: "idle" as AgentState },
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

// Hooks

// Returns the static agent roster (optionally enriched with live status)
export function useAgents() {
  const { data: orchestratorStatus } = useOrchestratorStatus();

  return useQuery({
    queryKey: [...agentKeys.all, "roster"],
    queryFn: async (): Promise<Agent[]> => {
      // Build a map of agent statuses from the agents array
      const statusMap = new Map<string, string>();
      if (orchestratorStatus?.agents) {
        for (const agentStatus of orchestratorStatus.agents) {
          statusMap.set(agentStatus.agent_id, agentStatus.state);
        }
      }

      // Enrich static roster with live status from orchestrator
      return AGENT_ROSTER.map((agent) => {
        const liveState = statusMap.get(agent.agent_id);
        return {
          ...agent,
          status: (liveState as AgentState) ?? agent.status,
        };
      });
    },
    staleTime: Infinity, // Static data, only updates when orchestrator updates
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
    mutationFn: ({ agentId, request }: { agentId: string; request?: SpawnAgentRequest }) =>
      orchestratorApi.spawn(agentId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.orchestrator() });
    },
  });
}

export function useStopAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ agentId, graceful = true }: { agentId: string; graceful?: boolean }) =>
      orchestratorApi.stop(agentId, graceful),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.orchestrator() });
    },
  });
}

export function useResolveWait() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ agentId, resolution }: { agentId: string; resolution: string }) =>
      orchestratorApi.resolveWait(agentId, resolution),
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
