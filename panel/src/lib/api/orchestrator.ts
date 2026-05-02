import api from "./client";
import type { OrchestratorStatus, AgentStatusResponse, WaitingAgent } from "@/types";
import { isMockMode, mockOrchestratorStatus, mockWaitingAgents } from "@/lib/mock-data";

export interface SpawnAgentRequest {
  task_id?: string;
  initial_prompt?: string;
}

export const orchestratorApi = {
  // Get orchestrator status
  getStatus: async (): Promise<OrchestratorStatus> => {
    if (isMockMode()) {
      return mockOrchestratorStatus as OrchestratorStatus;
    }
    const { data } = await api.get<OrchestratorStatus>("/orchestrator/status");
    return data;
  },

  // Get specific agent status
  getAgentStatus: async (agentId: string): Promise<AgentStatusResponse> => {
    if (isMockMode()) {
      const found = mockOrchestratorStatus.agents.find(a => a.agent_id === agentId);
      if (found) return found as AgentStatusResponse;
      throw new Error("Agent not found");
    }
    const { data } = await api.get<AgentStatusResponse>("/orchestrator/agents/" + agentId);
    return data;
  },

  // Get waiting agents
  getWaitingAgents: async (): Promise<WaitingAgent[]> => {
    if (isMockMode()) {
      return mockWaitingAgents as WaitingAgent[];
    }
    const { data } = await api.get<WaitingAgent[]>("/orchestrator/waiting");
    return data;
  },

  // Spawn an agent
  spawn: async (agentId: string, request?: SpawnAgentRequest): Promise<AgentStatusResponse> => {
    if (isMockMode()) {
      // Find or create agent in mock status
      const existing = mockOrchestratorStatus.agents.find(a => a.agent_id === agentId);
      if (existing) {
        existing.state = "running";
        // Mock data uses string, so use empty string for no task
        (existing as { task_id: string }).task_id = request?.task_id ?? "";
        return existing as AgentStatusResponse;
      }
      const newAgent = {
        agent_id: agentId,
        state: "running",
        task_id: request?.task_id ?? "",
        error_count: 0,
        started_at: new Date().toISOString(),
        waiting_for: null as string | null,
      };
      mockOrchestratorStatus.agents.push(newAgent);
      return newAgent as AgentStatusResponse;
    }
    const { data } = await api.post<AgentStatusResponse>(
      "/orchestrator/agents/" + agentId + "/spawn",
      { agent_id: agentId, ...request }
    );
    return data;
  },

  // Stop an agent
  stop: async (agentId: string, graceful: boolean = true): Promise<void> => {
    if (isMockMode()) {
      const agent = mockOrchestratorStatus.agents.find(a => a.agent_id === agentId);
      if (agent) {
        agent.state = "idle";
        (agent as { task_id: string }).task_id = "";
      }
      return;
    }
    await api.post("/orchestrator/agents/" + agentId + "/stop", null, {
      params: { graceful },
    });
  },

  // Resolve a waiting agent
  resolveWait: async (agentId: string, resolution: string): Promise<AgentStatusResponse> => {
    if (isMockMode()) {
      // Remove from waiting agents
      const idx = mockWaitingAgents.findIndex(a => a.agent_id === agentId);
      if (idx !== -1) mockWaitingAgents.splice(idx, 1);
      // Update agent status
      const agent = mockOrchestratorStatus.agents.find(a => a.agent_id === agentId);
      if (agent) {
        agent.state = "running";
        return agent as AgentStatusResponse;
      }
      throw new Error("Agent not found");
    }
    const { data } = await api.post<AgentStatusResponse>(
      "/orchestrator/agents/" + agentId + "/resolve-wait",
      { resolution }
    );
    return data;
  },
};
