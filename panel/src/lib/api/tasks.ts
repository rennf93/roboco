import api from "./client";
import { TaskStatus, Complexity, TaskNature, TaskType } from "@/types";
import type {
  Task,
  TaskCreate,
  Team,
  ProgressRequest,
  CheckpointRequest,
  CommitRequest,
  SoftBlockRequest,
  EscalateRequest,
  EscalateResponse,
  TaskCountResponse,
} from "@/types";
import { isMockMode, mockTasks } from "@/lib/mock-data";

export interface TaskFilters {
  status?: TaskStatus;
  team?: Team;
  limit?: number;
  offset?: number;
}

export const tasksApi = {
  // List tasks with optional filters
  list: async (filters?: TaskFilters): Promise<Task[]> => {
    if (isMockMode()) {
      let tasks = [...mockTasks];
      if (filters?.status) {
        tasks = tasks.filter((t) => t.status === filters.status);
      }
      if (filters?.team) {
        tasks = tasks.filter((t) => t.team === filters.team);
      }
      return tasks;
    }

    const params = new URLSearchParams();
    if (filters?.status) params.append("status", filters.status);
    if (filters?.team) params.append("team", filters.team);
    if (filters?.limit) params.append("limit", String(filters.limit));
    if (filters?.offset) params.append("offset", String(filters.offset));

    const url = "/tasks?" + params.toString();
    const { data } = await api.get<Task[]>(url);
    return data;
  },

  // Get single task
  get: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const task = mockTasks.find((t) => t.id === taskId);
      if (!task) throw new Error("Task not found");
      return task;
    }

    const { data } = await api.get<Task>("/tasks/" + taskId);
    return data;
  },

  // Create task
  create: async (task: TaskCreate): Promise<Task> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      const newTask: Task = {
        id: `task-${Date.now()}`,
        title: task.title,
        description: task.description,
        team: task.team,
        priority: task.priority ?? 2,
        sequence: mockTasks.length + 1, // Auto-increment sequence
        estimated_complexity: task.estimated_complexity ?? Complexity.MEDIUM,
        nature: task.nature ?? TaskNature.TECHNICAL,
        task_type: task.task_type ?? TaskType.CODE,
        project_id: task.project_id,
        docs_complete: false,
        pr_created: false,
        pm_approvals: {},
        acceptance_criteria: task.acceptance_criteria,
        parent_task_id: task.parent_task_id ?? null,
        target_date: task.target_date ?? null,
        status: task.status ?? TaskStatus.PENDING,
        dependency_ids: [],
        blocker_ids: [],
        created_at: now,
        updated_at: now,
        created_by: "00000000-0000-0000-0000-000000000001", // CEO
        assigned_to: null,
        claimed_at: null,
        started_at: null,
        completed_at: null,
        self_verified: false,
        qa_verified: null,
        plan: null,
        progress_updates: [],
        checkpoints: [],
        commits: [],
        dev_notes: null,
        qa_notes: null,
        auditor_notes: null,
        quick_context: null,
        sessions: [],
        branch_name: null,
        pr_number: null,
        pr_url: null,
      };
      mockTasks.push(newTask);
      return newTask;
    }
    const { data } = await api.post<Task>("/tasks", task);
    return data;
  },

  // Update task
  update: async (taskId: string, updates: Partial<Task>): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      mockTasks[idx] = { ...mockTasks[idx], ...updates, updated_at: new Date().toISOString() };
      return mockTasks[idx];
    }
    const { data } = await api.put<Task>("/tasks/" + taskId, updates);
    return data;
  },

  // Delete task
  delete: async (taskId: string): Promise<void> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx !== -1) mockTasks.splice(idx, 1);
      return;
    }
    await api.delete("/tasks/" + taskId);
  },

  // =========================================================================
  // LIFECYCLE ACTIONS
  // =========================================================================

  claim: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.CLAIMED, claimed_at: now, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/claim");
    return data;
  },

  start: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.IN_PROGRESS, started_at: now, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/start");
    return data;
  },

  block: async (taskId: string, blockerId?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      const blockerIds = blockerId ? [...mockTasks[idx].blocker_ids, blockerId] : mockTasks[idx].blocker_ids;
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.BLOCKED, blocker_ids: blockerIds, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/block", { blocker_id: blockerId });
    return data;
  },

  unblock: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.IN_PROGRESS, blocker_ids: [], updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/unblock");
    return data;
  },

  pause: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.PAUSED, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/pause");
    return data;
  },

  resume: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.IN_PROGRESS, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/resume");
    return data;
  },

  verify: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.VERIFYING, self_verified: true, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/verify");
    return data;
  },

  submitQa: async (taskId: string, devNotes?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.AWAITING_QA, dev_notes: devNotes ?? null, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/submit-qa", { dev_notes: devNotes });
    return data;
  },

  passQa: async (taskId: string, qaNotes?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.AWAITING_DOCUMENTATION, qa_verified: true, qa_notes: qaNotes ?? null, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/pass-qa", { qa_notes: qaNotes });
    return data;
  },

  failQa: async (taskId: string, qaNotes?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.NEEDS_REVISION, qa_verified: false, qa_notes: qaNotes ?? null, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/fail-qa", { qa_notes: qaNotes });
    return data;
  },

  complete: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.COMPLETED, completed_at: now, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/complete");
    return data;
  },

  cancel: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.CANCELLED, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/cancel");
    return data;
  },

  // Note: reopen is not supported by backend - use update with status change instead
  reopen: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.PENDING, completed_at: null, updated_at: now };
      return mockTasks[idx];
    }
    // Backend doesn't have /reopen endpoint - use update instead
    const { data } = await api.put<Task>("/tasks/" + taskId, { status: TaskStatus.PENDING });
    return data;
  },

  // Activate a task from BACKLOG status (PM only)
  activate: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.PENDING, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/activate");
    return data;
  },

  // Mark documentation as complete (Documenter only)
  docsComplete: async (taskId: string, docNotes?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = { ...mockTasks[idx], status: TaskStatus.AWAITING_PM_REVIEW, updated_at: now };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/docs-complete", docNotes ?? null);
    return data;
  },

  // =========================================================================
  // CONVENIENCE METHODS
  // =========================================================================

  getMyTasks: async (): Promise<Task[]> => {
    if (isMockMode()) {
      // Return tasks assigned to "me" (mock: any assigned task)
      return mockTasks.filter((t) => t.assigned_to !== null);
    }
    const { data } = await api.get<Task[]>("/tasks/my");
    return data;
  },

  getPending: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.PENDING);
    }
    const { data } = await api.get<Task[]>("/tasks/pending");
    return data;
  },

  getBlocked: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.BLOCKED);
    }
    const { data } = await api.get<Task[]>("/tasks/blocked");
    return data;
  },

  getAwaitingQa: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.AWAITING_QA);
    }
    const { data } = await api.get<Task[]>("/tasks/awaiting-qa");
    return data;
  },

  getTeamTasks: async (team: Team): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.team === team);
    }
    const { data } = await api.get<Task[]>("/tasks/team/" + team);
    return data;
  },

  getAwaitingDocs: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.AWAITING_DOCUMENTATION);
    }
    const { data } = await api.get<Task[]>("/tasks/awaiting-docs");
    return data;
  },

  getSubtasks: async (taskId: string): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.parent_task_id === taskId);
    }
    const { data } = await api.get<Task[]>("/tasks/" + taskId + "/subtasks");
    return data;
  },

  // =========================================================================
  // STATS
  // =========================================================================

  getStats: async (): Promise<TaskCountResponse> => {
    if (isMockMode()) {
      const counts: Record<string, number> = {};
      mockTasks.forEach((t) => {
        counts[t.status] = (counts[t.status] || 0) + 1;
      });
      return { counts };
    }
    const { data } = await api.get<TaskCountResponse>("/tasks/stats");
    return data;
  },

  getStatsByTeam: async (): Promise<TaskCountResponse> => {
    if (isMockMode()) {
      const counts: Record<string, number> = {};
      mockTasks.forEach((t) => {
        counts[t.team] = (counts[t.team] || 0) + 1;
      });
      return { counts };
    }
    const { data } = await api.get<TaskCountResponse>("/tasks/stats/by-team");
    return data;
  },

  // =========================================================================
  // PROGRESS TRACKING
  // =========================================================================

  addProgress: async (taskId: string, request: ProgressRequest): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      const update = {
        timestamp: now,
        agent_id: "00000000-0000-0000-0000-000000000001",
        message: request.message,
        percentage: request.percentage ?? null,
      };
      mockTasks[idx] = {
        ...mockTasks[idx],
        progress_updates: [...mockTasks[idx].progress_updates, update],
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/progress", request);
    return data;
  },

  addCheckpoint: async (taskId: string, request: CheckpointRequest): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      const checkpoint = {
        id: `checkpoint-${Date.now()}`,
        timestamp: now,
        agent_id: "00000000-0000-0000-0000-000000000001",
        state_summary: request.state_summary,
        remaining_work: request.remaining_work,
        notes: request.notes ?? null,
      };
      mockTasks[idx] = {
        ...mockTasks[idx],
        checkpoints: [...mockTasks[idx].checkpoints, checkpoint],
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/checkpoint", request);
    return data;
  },

  addCommit: async (taskId: string, request: CommitRequest): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      const commit = {
        hash: request.hash,
        message: request.message,
        timestamp: now,
        author_agent_id: "00000000-0000-0000-0000-000000000001",
      };
      mockTasks[idx] = {
        ...mockTasks[idx],
        commits: [...mockTasks[idx].commits, commit],
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/commit", request);
    return data;
  },

  // =========================================================================
  // SOFT BLOCK & ESCALATION
  // =========================================================================

  softBlock: async (taskId: string, request: SoftBlockRequest): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = {
        ...mockTasks[idx],
        status: TaskStatus.BLOCKED,
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/soft-block", request);
    return data;
  },

  escalate: async (taskId: string, request: EscalateRequest): Promise<EscalateResponse> => {
    if (isMockMode()) {
      return {
        status: "escalated",
        task_id: taskId,
        escalated_to: request.escalate_to || "cell-pm",
        reason: request.reason,
        message: "Task escalated successfully (mock)",
      };
    }
    const { data } = await api.post<EscalateResponse>("/tasks/" + taskId + "/escalate", request);
    return data;
  },

  submitPmReview: async (taskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = {
        ...mockTasks[idx],
        status: TaskStatus.AWAITING_PM_REVIEW,
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/submit-pm-review");
    return data;
  },

  // =========================================================================
  // CEO APPROVAL (CEO only - Human-in-the-Loop)
  // =========================================================================

  // Get tasks awaiting CEO approval
  getAwaitingCeoApproval: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.AWAITING_CEO_APPROVAL);
    }
    const { data } = await api.get<Task[]>("/tasks/awaiting-ceo-approval");
    return data;
  },

  // CEO approves a task (completes it)
  ceoApprove: async (taskId: string, notes?: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = {
        ...mockTasks[idx],
        status: TaskStatus.COMPLETED,
        completed_at: now,
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/ceo-approve", { notes });
    return data;
  },

  // CEO rejects a task (sends back for revision)
  ceoReject: async (taskId: string, notes: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = {
        ...mockTasks[idx],
        status: TaskStatus.NEEDS_REVISION,
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/ceo-reject", { notes });
    return data;
  },

  // =========================================================================
  // ADDITIONAL CONVENIENCE METHODS
  // =========================================================================

  // Get tasks awaiting PM review
  getAwaitingPmReview: async (): Promise<Task[]> => {
    if (isMockMode()) {
      return mockTasks.filter((t) => t.status === TaskStatus.AWAITING_PM_REVIEW);
    }
    const { data } = await api.get<Task[]>("/tasks/awaiting-pm-review");
    return data;
  },

  // Get all descendants of a task (subtasks, sub-subtasks, etc.)
  getDescendants: async (taskId: string): Promise<Task[]> => {
    if (isMockMode()) {
      // Simple mock: just return direct subtasks
      return mockTasks.filter((t) => t.parent_task_id === taskId);
    }
    const { data } = await api.get<Task[]>("/tasks/" + taskId + "/descendants");
    return data;
  },

  // Get sessions linked to a task
  getSessions: async (taskId: string): Promise<{ session_id: string; is_primary: boolean; relationship_type: string }[]> => {
    if (isMockMode()) {
      return [];
    }
    const { data } = await api.get<{ session_id: string; is_primary: boolean; relationship_type: string }[]>(
      "/tasks/" + taskId + "/sessions"
    );
    return data;
  },

  // Escalate task directly to CEO
  escalateToCeo: async (taskId: string, reason: string): Promise<EscalateResponse> => {
    if (isMockMode()) {
      return {
        status: "escalated",
        task_id: taskId,
        escalated_to: "ceo",
        reason,
        message: "Task escalated to CEO (mock)",
      };
    }
    const { data } = await api.post<EscalateResponse>("/tasks/" + taskId + "/escalate-to-ceo", { reason });
    return data;
  },

  // Substitute a task with another (create replacement)
  substitute: async (taskId: string, replacementTaskId: string): Promise<Task> => {
    if (isMockMode()) {
      const idx = mockTasks.findIndex((t) => t.id === taskId);
      if (idx === -1) throw new Error("Task not found");
      const now = new Date().toISOString();
      mockTasks[idx] = {
        ...mockTasks[idx],
        status: TaskStatus.CANCELLED,
        updated_at: now,
      };
      return mockTasks[idx];
    }
    const { data } = await api.post<Task>("/tasks/" + taskId + "/substitute", {
      replacement_task_id: replacementTaskId,
    });
    return data;
  },
};
