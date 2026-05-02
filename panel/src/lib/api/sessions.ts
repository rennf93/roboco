import api from "./client";
import type { Session } from "@/types";
import { SessionStatus, SessionScope } from "@/types";
import { isMockMode, mockSessions } from "@/lib/mock-data";

// Session-Task link response from API
export interface SessionTaskLinkResponse {
  id: string;
  session_id: string;
  task_id: string;
  is_primary: boolean;
  relationship_type: string;
  added_at: string;
  added_by: string | null;
}

export interface SessionCreate {
  group_id: string;
  scope?: string;
}

export const sessionsApi = {
  // List sessions for a group
  listByGroup: async (groupId: string, limit: number = 50): Promise<Session[]> => {
    if (isMockMode()) {
      return (mockSessions as Session[]).slice(0, limit);
    }
    const { data } = await api.get<{ items: Session[]; total: number }>("/sessions", {
      params: { group_id: groupId, limit },
    });
    return data.items;
  },

  // Get session by ID
  get: async (sessionId: string): Promise<Session> => {
    if (isMockMode()) {
      const session = mockSessions.find((s) => s.id === sessionId);
      if (session) return session as Session;
      throw new Error("Session not found");
    }
    const { data } = await api.get<Session>("/sessions/" + sessionId);
    return data;
  },

  // Get sessions linked to a task
  getForTask: async (taskId: string): Promise<SessionTaskLinkResponse[]> => {
    if (isMockMode()) {
      return []; // No mock session-task links
    }
    const { data } = await api.get<SessionTaskLinkResponse[]>("/sessions/for-task/" + taskId);
    return data;
  },

  // Close a session
  close: async (sessionId: string): Promise<Session> => {
    if (isMockMode()) {
      const idx = mockSessions.findIndex((s) => s.id === sessionId);
      if (idx !== -1) {
        const session = mockSessions[idx] as Session;
        const closedSession: Session = {
          ...session,
          status: "closed" as SessionStatus,
          closed_at: new Date().toISOString(),
        };
        (mockSessions as Session[])[idx] = closedSession;
        return closedSession;
      }
      throw new Error("Session not found");
    }
    const { data } = await api.post<Session>("/sessions/" + sessionId + "/close");
    return data;
  },

  // Link a task to a session (PM only)
  linkTask: async (
    sessionId: string,
    taskId: string,
    isPrimary: boolean = false,
    relationshipType: string = "discussion"
  ): Promise<SessionTaskLinkResponse> => {
    const { data } = await api.post<SessionTaskLinkResponse>(
      "/sessions/" + sessionId + "/add-task",
      {
        task_id: taskId,
        is_primary: isPrimary,
        relationship_type: relationshipType,
      }
    );
    return data;
  },

  // Unlink a task from a session (PM only)
  unlinkTask: async (sessionId: string, taskId: string): Promise<void> => {
    await api.delete("/sessions/" + sessionId + "/remove-task", {
      data: { task_id: taskId },
    });
  },

  // Get tasks linked to a session
  getTasksForSession: async (sessionId: string): Promise<SessionTaskLinkResponse[]> => {
    // Note: Backend doesn't have a dedicated GET endpoint for session tasks
    // Task links are returned with session detail via get()
    const session = await sessionsApi.get(sessionId);
    // Return empty array if session doesn't have task_links
    return (session as Session & { task_links?: SessionTaskLinkResponse[] }).task_links || [];
  },

  // Create a session for tasks (PM only)
  createForTasks: async (
    taskIds: string[],
    channelSlug: string,
    relationshipType: string = "discussion"
  ): Promise<{ session: Session; links: SessionTaskLinkResponse[] }> => {
    const { data } = await api.post<{ session: Session; links: SessionTaskLinkResponse[] }>(
      "/sessions/for-tasks",
      {
        task_ids: taskIds,
        channel_slug: channelSlug,
        relationship_type: relationshipType,
      }
    );
    return data;
  },

  // Create a new session directly
  create: async (session: SessionCreate): Promise<Session> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      const newSession: Session = {
        id: `session-${Date.now()}`,
        group_id: session.group_id,
        status: SessionStatus.ACTIVE,
        scope: (session.scope as SessionScope) || SessionScope.CELL,
        message_count: 0,
        total_content_length: 0,
        started_at: now,
        last_activity_at: now,
        closed_at: null,
        task_links: [],
      };
      (mockSessions as Session[]).push(newSession);
      return newSession;
    }
    const { data } = await api.post<Session>("/sessions", session);
    return data;
  },

  // Update a task link in a session
  updateTaskLink: async (
    sessionId: string,
    taskId: string,
    updates: { is_primary?: boolean; relationship_type?: string }
  ): Promise<SessionTaskLinkResponse> => {
    if (isMockMode()) {
      return {
        id: `link-${Date.now()}`,
        session_id: sessionId,
        task_id: taskId,
        is_primary: updates.is_primary ?? false,
        relationship_type: updates.relationship_type ?? "discussion",
        added_at: new Date().toISOString(),
        added_by: null,
      };
    }
    const { data } = await api.post<SessionTaskLinkResponse>(
      "/sessions/" + sessionId + "/update-task",
      { task_id: taskId, ...updates }
    );
    return data;
  },
};

export const groupsApi = {
  // Get groups for a channel
  listByChannel: async (channelId: string): Promise<unknown[]> => {
    if (isMockMode()) {
      return [];
    }
    const { data } = await api.get<unknown[]>("/channels/" + channelId + "/groups");
    return data;
  },
};
