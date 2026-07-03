import api from "./client";

// ---------------------------------------------------------------------------
// Board roadmap engine — the Product Owner authors a themed cycle of roadmap
// items (a goal + 3-7 drafts); the CEO approves or rejects each item
// individually here. Approving materializes a BACKLOG task; nothing starts
// automatically.
// ---------------------------------------------------------------------------

export interface RoadmapItem {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  project_slug: string;
  team: string;
  priority: number;
  rationale: string;
  status: "proposed" | "approved" | "rejected";
  reject_reason?: string | null;
  materialized_task_id?: string | null;
}

export interface RoadmapCycle {
  task_id: string;
  title: string;
  status: string;
  goal: string;
  items: RoadmapItem[];
}

export interface RoadmapItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const roadmapApi = {
  listCycles: async (): Promise<RoadmapCycle[]> => {
    const { data } = await api.get<RoadmapCycle[]>("/roadmap/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<RoadmapItemActionResult> => {
    const { data } = await api.post<RoadmapItemActionResult>(
      `/roadmap/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<RoadmapItemActionResult> => {
    const { data } = await api.post<RoadmapItemActionResult>(
      `/roadmap/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
