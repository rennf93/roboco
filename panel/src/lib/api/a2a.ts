/**
 * A2A (Agent-to-Agent) Protocol API Client
 *
 * API functions for agent-to-agent communication protocol.
 */

import api from "./client";
import { isMockMode } from "@/lib/mock-data";

// =============================================================================
// Types
// =============================================================================

export interface A2AMessage {
  id: string;
  from_agent_id: string;
  to_agent_id: string;
  content: string;
  message_type: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface A2AMessageSendRequest {
  to_agent_id: string;
  content: string;
  message_type?: string;
  task_id?: string;
  metadata?: Record<string, unknown>;
}

export interface A2AMessageResponse {
  message_id: string;
  status: string;
  delivered_at?: string;
}

export interface A2ATask {
  id: string;
  title: string;
  status: string;
  assigned_to: string | null;
  created_at: string;
}

export interface A2AAgentCard {
  agent_id: string;
  name: string;
  role: string;
  capabilities: string[];
  status: string;
  current_task_id: string | null;
}

export interface A2AStreamChunk {
  chunk_id: string;
  content: string;
  is_final: boolean;
}

// =============================================================================
// API Client
// =============================================================================

export const a2aApi = {
  // ===========================================================================
  // MESSAGE ENDPOINTS
  // ===========================================================================

  /**
   * Send a message to another agent
   */
  sendMessage: async (request: A2AMessageSendRequest): Promise<A2AMessageResponse> => {
    if (isMockMode()) {
      return {
        message_id: `msg-${Date.now()}`,
        status: "delivered",
        delivered_at: new Date().toISOString(),
      };
    }
    const { data } = await api.post<A2AMessageResponse>("/a2a/message/send", request);
    return data;
  },

  /**
   * Stream a message to another agent (for long content)
   */
  streamMessage: async (request: A2AMessageSendRequest): Promise<A2AMessageResponse> => {
    if (isMockMode()) {
      return {
        message_id: `msg-${Date.now()}`,
        status: "streaming",
      };
    }
    const { data } = await api.post<A2AMessageResponse>("/a2a/message/stream", request);
    return data;
  },

  // ===========================================================================
  // TASK ENDPOINTS
  // ===========================================================================

  /**
   * List tasks visible via A2A protocol
   */
  listTasks: async (): Promise<A2ATask[]> => {
    if (isMockMode()) {
      return [];
    }
    const { data } = await api.get<A2ATask[]>("/a2a/tasks");
    return data;
  },

  /**
   * Get a specific task via A2A protocol
   */
  getTask: async (taskId: string): Promise<A2ATask> => {
    if (isMockMode()) {
      return {
        id: taskId,
        title: "Mock Task",
        status: "in_progress",
        assigned_to: null,
        created_at: new Date().toISOString(),
      };
    }
    const { data } = await api.get<A2ATask>(`/a2a/tasks/${taskId}`);
    return data;
  },

  /**
   * Subscribe to task updates (returns SSE stream URL)
   * Note: This endpoint returns Server-Sent Events, handle appropriately
   */
  subscribeToTask: (taskId: string): string => {
    // Returns the URL for SSE subscription
    return `/a2a/tasks/${taskId}/subscribe`;
  },

  /**
   * Cancel a task via A2A protocol
   */
  cancelTask: async (taskId: string): Promise<{ status: string; task_id: string }> => {
    if (isMockMode()) {
      return {
        status: "cancelled",
        task_id: taskId,
      };
    }
    const { data } = await api.post<{ status: string; task_id: string }>(`/a2a/tasks/${taskId}/cancel`);
    return data;
  },

  // ===========================================================================
  // AGENT ENDPOINTS
  // ===========================================================================

  /**
   * List all agents available via A2A protocol
   */
  listAgents: async (): Promise<A2AAgentCard[]> => {
    if (isMockMode()) {
      return [];
    }
    const { data } = await api.get<A2AAgentCard[]>("/a2a/agents");
    return data;
  },

  /**
   * Get agent card (profile/capabilities)
   */
  getAgentCard: async (agentId: string): Promise<A2AAgentCard> => {
    if (isMockMode()) {
      return {
        agent_id: agentId,
        name: "Mock Agent",
        role: "developer",
        capabilities: ["coding", "testing"],
        status: "idle",
        current_task_id: null,
      };
    }
    const { data } = await api.get<A2AAgentCard>(`/a2a/agents/${agentId}/card`);
    return data;
  },
};
