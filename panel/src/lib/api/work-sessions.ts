import api from "./client";
import type {
  WorkSession,
  WorkSessionSummary,
  WorkSessionCreate,
  WorkSessionStatus,
} from "@/types";
import { isMockMode } from "@/lib/mock-data";

// Mock data for offline mode
const mockWorkSessions: WorkSession[] = [];

export interface WorkSessionFilters {
  project_id?: string;
  agent_id?: string;
  status?: WorkSessionStatus;
  active_only?: boolean;
}

export const workSessionsApi = {
  // List work sessions with optional filters
  list: async (filters?: WorkSessionFilters): Promise<WorkSessionSummary[]> => {
    if (isMockMode()) {
      let sessions = [...mockWorkSessions];
      if (filters?.project_id) {
        sessions = sessions.filter((s) => s.project_id === filters.project_id);
      }
      if (filters?.agent_id) {
        sessions = sessions.filter((s) => s.agent_id === filters.agent_id);
      }
      if (filters?.status) {
        sessions = sessions.filter((s) => s.status === filters.status);
      }
      if (filters?.active_only) {
        sessions = sessions.filter((s) => s.status === "active");
      }
      return sessions.map((s) => ({
        id: s.id,
        task_id: s.task_id,
        branch_name: s.branch_name,
        status: s.status,
        started_at: s.started_at,
        has_pr: s.pr_number !== null,
      }));
    }

    const params = new URLSearchParams();
    if (filters?.project_id) params.append("project_id", filters.project_id);
    if (filters?.agent_id) params.append("agent_id", filters.agent_id);
    if (filters?.status) params.append("status", filters.status);
    if (filters?.active_only) params.append("active_only", "true");

    const url = "/work-sessions?" + params.toString();
    const { data } = await api.get<WorkSessionSummary[]>(url);
    return data;
  },

  // Get single work session
  get: async (sessionId: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const session = mockWorkSessions.find((s) => s.id === sessionId);
      if (!session) throw new Error("Work session not found");
      return session;
    }

    const { data } = await api.get<WorkSession>("/work-sessions/" + sessionId);
    return data;
  },

  // Get active work session for a task
  getForTask: async (taskId: string): Promise<WorkSession | null> => {
    if (isMockMode()) {
      const session = mockWorkSessions.find(
        (s) => s.task_id === taskId && s.status === "active"
      );
      return session ?? null;
    }

    try {
      const { data } = await api.get<WorkSession | null>("/work-sessions/task/" + taskId);
      return data;
    } catch {
      return null;
    }
  },

  // Create work session (Developer or PM)
  create: async (session: WorkSessionCreate): Promise<WorkSession> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      const newSession: WorkSession = {
        id: `session-${Date.now()}`,
        project_id: session.project_id,
        task_id: session.task_id,
        agent_id: "00000000-0000-0000-0000-000000000001", // CEO in mock
        branch_name: session.branch_name,
        base_branch: session.base_branch,
        target_branch: session.target_branch,
        started_at: now,
        ended_at: null,
        status: "active" as WorkSessionStatus,
        commits: [],
        files_modified: [],
        pr_number: null,
        pr_url: null,
        pr_status: null,
        pr_created_at: null,
        pr_merged_at: null,
        merged_by: null,
        created_at: now,
        updated_at: now,
      };
      mockWorkSessions.push(newSession);
      return newSession;
    }
    const { data } = await api.post<WorkSession>("/work-sessions", session);
    return data;
  },

  // Add a commit to the work session
  addCommit: async (sessionId: string, commitSha: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        commits: [...mockWorkSessions[idx].commits, commitSha],
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/commits", {
      commit_sha: commitSha,
    });
    return data;
  },

  // Add modified files to the work session
  addFiles: async (sessionId: string, filePaths: string[]): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      const existingFiles = new Set(mockWorkSessions[idx].files_modified);
      filePaths.forEach((f) => existingFiles.add(f));
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        files_modified: Array.from(existingFiles),
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/files", {
      file_paths: filePaths,
    });
    return data;
  },

  // Record PR creation
  createPR: async (sessionId: string, prNumber: number, prUrl: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        pr_number: prNumber,
        pr_url: prUrl,
        pr_status: "open",
        pr_created_at: now,
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/pr", {
      pr_number: prNumber,
      pr_url: prUrl,
    });
    return data;
  },

  // Update PR status
  updatePRStatus: async (sessionId: string, prStatus: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        pr_status: prStatus,
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.patch<WorkSession>("/work-sessions/" + sessionId + "/pr", {
      pr_status: prStatus,
    });
    return data;
  },

  // Record PR merge (PM only)
  mergePR: async (sessionId: string, mergedBy: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        pr_status: "merged",
        pr_merged_at: now,
        merged_by: mergedBy,
        status: "completed" as WorkSessionStatus,
        ended_at: now,
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/pr/merge", {
      merged_by: mergedBy,
    });
    return data;
  },

  // Complete the session
  complete: async (sessionId: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        status: "completed" as WorkSessionStatus,
        ended_at: now,
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/complete");
    return data;
  },

  // Abandon the session
  abandon: async (sessionId: string, reason?: string): Promise<WorkSession> => {
    if (isMockMode()) {
      const idx = mockWorkSessions.findIndex((s) => s.id === sessionId);
      if (idx === -1) throw new Error("Work session not found");
      const now = new Date().toISOString();
      mockWorkSessions[idx] = {
        ...mockWorkSessions[idx],
        status: "abandoned" as WorkSessionStatus,
        ended_at: now,
        updated_at: now,
      };
      return mockWorkSessions[idx];
    }
    const params = reason ? `?reason=${encodeURIComponent(reason)}` : "";
    const { data } = await api.post<WorkSession>("/work-sessions/" + sessionId + "/abandon" + params);
    return data;
  },
};
