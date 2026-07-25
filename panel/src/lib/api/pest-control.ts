import api from "./client";

// ---------------------------------------------------------------------------
// Pest Control (Board Program) engine — the Product Owner hunts latent
// defects (findings-ledger clusters, rework hotspots) in an opted-in project
// and authors 1-5 evidence-backed bug item drafts; the CEO approves or
// rejects each item individually here. Approving materializes a BACKLOG
// task; nothing starts automatically. Mirrors lib/api/roadmap.ts.
// ---------------------------------------------------------------------------

export interface PestHuntItem {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  project_slug: string;
  team: string;
  priority: number;
  evidence: string;
  status: "proposed" | "approved" | "rejected";
  reject_reason?: string | null;
  materialized_task_id?: string | null;
}

export interface PestHuntCycle {
  task_id: string;
  title: string;
  status: string;
  items: PestHuntItem[];
}

export interface PestHuntItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const pestControlApi = {
  listCycles: async (): Promise<PestHuntCycle[]> => {
    const { data } = await api.get<PestHuntCycle[]>("/pest-control/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<PestHuntItemActionResult> => {
    const { data } = await api.post<PestHuntItemActionResult>(
      `/pest-control/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<PestHuntItemActionResult> => {
    const { data } = await api.post<PestHuntItemActionResult>(
      `/pest-control/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
