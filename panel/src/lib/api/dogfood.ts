import api from "./client";

// ---------------------------------------------------------------------------
// Dogfood (Board Program) engine — the Product Owner walks an opted-in
// project's live surfaces as a user (the panel via Playwright, the docs
// site, the Telegram flow) and authors 1-5 evidence-backed friction-fix item
// drafts; the CEO approves or rejects each item individually here. Approving
// materializes a BACKLOG task; nothing starts automatically. Mirrors
// lib/api/spackle.ts.
// ---------------------------------------------------------------------------

export interface FrictionFixItem {
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

export interface DogfoodCycle {
  task_id: string;
  title: string;
  status: string;
  items: FrictionFixItem[];
}

export interface FrictionFixItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const dogfoodApi = {
  listCycles: async (): Promise<DogfoodCycle[]> => {
    const { data } = await api.get<DogfoodCycle[]>("/dogfood/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<FrictionFixItemActionResult> => {
    const { data } = await api.post<FrictionFixItemActionResult>(
      `/dogfood/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<FrictionFixItemActionResult> => {
    const { data } = await api.post<FrictionFixItemActionResult>(
      `/dogfood/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
