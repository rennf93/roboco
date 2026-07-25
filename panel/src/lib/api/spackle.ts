import api from "./client";

// ---------------------------------------------------------------------------
// Spackle (Board Program) engine — the Product Owner audits an opted-in
// project's half-shipped surface area (API routes with no panel surface and
// vice versa, armed flags with no docs, docs claims vs code, coverage
// holes, dead-end panel tabs) and authors 1-5 evidence-backed gap-fill item
// drafts; the CEO approves or rejects each item individually here. Approving
// materializes a BACKLOG task; nothing starts automatically. Mirrors
// lib/api/pest-control.ts.
// ---------------------------------------------------------------------------

export interface GapFillItem {
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

export interface SpackleCycle {
  task_id: string;
  title: string;
  status: string;
  items: GapFillItem[];
}

export interface GapFillItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const spackleApi = {
  listCycles: async (): Promise<SpackleCycle[]> => {
    const { data } = await api.get<SpackleCycle[]>("/spackle/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<GapFillItemActionResult> => {
    const { data } = await api.post<GapFillItemActionResult>(
      `/spackle/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<GapFillItemActionResult> => {
    const { data } = await api.post<GapFillItemActionResult>(
      `/spackle/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
