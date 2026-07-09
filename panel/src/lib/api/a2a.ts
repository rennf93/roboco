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

/** Summary row for the CEO's admin view of an agent-to-agent conversation. */
export interface AdminConversationSummary {
  id: string;
  agent_a: string;
  agent_b: string;
  topic: string | null;
  task_id: string | null;
  status: string;
  message_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  created_at: string;
  updated_at: string;
}

/** One persisted A2A chat message (full body — WS frames only carry excerpts). */
export interface A2AChatMessage {
  id: string;
  conversation_id: string;
  from_agent: string;
  content: string;
  message_kind: string;
  response_to_id: string | null;
  requires_response: boolean;
  read_at: string | null;
  created_at: string;
  edited_at: string | null;
}

/**
 * CEO interjection payload. The backend posts this INTO the conversation
 * being viewed (readable by both participants), addressed to `to_agent` —
 * not a re-homed CEO<->to_agent pairwise DM.
 */
export interface AdminReplyRequest {
  to_agent: string;
  content: string;
  skill?: string | null;
}

export interface AdminConversationListResponse {
  items: AdminConversationSummary[];
  total: number;
}

/**
 * One pair card for the CEO's A2A switchboard (org-chart view) — the static
 * can_a2a_direct matrix joined with the pair's representative conversation
 * stats when one exists.
 */
export interface AdminPairSummary {
  agent_a: string;
  role_a: string;
  team_a: string;
  agent_b: string;
  role_b: string;
  team_b: string;
  group_key: string;
  conversation_id: string | null;
  last_message_at: string | null;
  message_count: number;
}

export interface AdminPairListResponse {
  items: AdminPairSummary[];
  total: number;
}

export interface AdminMessageListResponse {
  items: A2AChatMessage[];
  total: number;
  has_more: boolean;
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
  sendMessage: async (
    request: A2AMessageSendRequest,
  ): Promise<A2AMessageResponse> => {
    if (isMockMode()) {
      return {
        message_id: `msg-${Date.now()}`,
        status: "delivered",
        delivered_at: new Date().toISOString(),
      };
    }
    const { data } = await api.post<A2AMessageResponse>(
      "/a2a/message/send",
      request,
    );
    return data;
  },

  /**
   * Stream a message to another agent (for long content)
   */
  streamMessage: async (
    request: A2AMessageSendRequest,
  ): Promise<A2AMessageResponse> => {
    if (isMockMode()) {
      return {
        message_id: `msg-${Date.now()}`,
        status: "streaming",
      };
    }
    const { data } = await api.post<A2AMessageResponse>(
      "/a2a/message/stream",
      request,
    );
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
  cancelTask: async (
    taskId: string,
  ): Promise<{ status: string; task_id: string }> => {
    if (isMockMode()) {
      return {
        status: "cancelled",
        task_id: taskId,
      };
    }
    const { data } = await api.post<{ status: string; task_id: string }>(
      `/a2a/tasks/${taskId}/cancel`,
    );
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

  // ===========================================================================
  // ADMIN (CEO) ENDPOINTS — A2A live view
  // ===========================================================================

  /**
   * List agent<->agent conversations, most-recent-first (CEO-only).
   */
  listAdminConversations: async (
    limit: number = 50,
  ): Promise<AdminConversationListResponse> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      return {
        items: [
          {
            id: "mock-conversation-1",
            agent_a: "be-dev-1",
            agent_b: "be-qa",
            topic: "QA handoff",
            task_id: null,
            status: "active",
            message_count: 3,
            last_message_at: now,
            last_message_preview: "Tests are green on the branch.",
            created_at: now,
            updated_at: now,
          },
        ],
        total: 1,
      };
    }
    const { data } = await api.get<AdminConversationListResponse>(
      "/a2a/chat/admin/conversations",
      { params: { limit } },
    );
    return data;
  },

  /**
   * List a conversation's messages, chronological oldest-first (CEO-only).
   */
  listAdminMessages: async (
    conversationId: string,
    limit: number = 100,
  ): Promise<AdminMessageListResponse> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      return {
        items: [
          {
            id: "mock-a2a-message-1",
            conversation_id: conversationId,
            from_agent: "be-dev-1",
            content: "Branch is ready for QA.",
            message_kind: "text",
            response_to_id: null,
            requires_response: false,
            read_at: null,
            created_at: now,
            edited_at: null,
          },
        ],
        total: 1,
        has_more: false,
      };
    }
    const { data } = await api.get<AdminMessageListResponse>(
      `/a2a/chat/admin/conversations/${conversationId}/messages`,
      { params: { limit } },
    );
    return data;
  },

  /**
   * List the org-chart switchboard's pair cards (CEO-only): every agent pair
   * allowed to A2A directly, joined with each pair's representative
   * conversation stats when one exists.
   */
  listAdminPairs: async (): Promise<AdminPairListResponse> => {
    if (isMockMode()) {
      return {
        items: [
          {
            agent_a: "be-dev-1",
            role_a: "developer",
            team_a: "backend",
            agent_b: "be-qa",
            role_b: "qa",
            team_b: "backend",
            group_key: "cell-backend",
            conversation_id: "mock-conversation-1",
            last_message_at: new Date().toISOString(),
            message_count: 3,
          },
          {
            agent_a: "auditor",
            role_a: "auditor",
            team_a: "board",
            agent_b: "product-owner",
            role_b: "product_owner",
            team_b: "board",
            group_key: "board",
            conversation_id: null,
            last_message_at: null,
            message_count: 0,
          },
        ],
        total: 2,
      };
    }
    const { data } = await api.get<AdminPairListResponse>(
      "/a2a/chat/admin/pairs",
    );
    return data;
  },

  /**
   * Send a CEO reply. Lands in the CEO<->to_agent pairwise conversation (the
   * A2A model is strictly pairwise), NOT inside the watched transcript.
   */
  replyAsCeo: async (
    conversationId: string,
    request: AdminReplyRequest,
  ): Promise<A2AChatMessage> => {
    if (isMockMode()) {
      return {
        id: `mock-reply-${Date.now()}`,
        conversation_id: conversationId,
        from_agent: "ceo",
        content: request.content,
        message_kind: "text",
        response_to_id: null,
        requires_response: false,
        read_at: null,
        created_at: new Date().toISOString(),
        edited_at: null,
      };
    }
    const { data } = await api.post<A2AChatMessage>(
      `/a2a/chat/admin/conversations/${conversationId}/reply`,
      request,
    );
    return data;
  },
};
