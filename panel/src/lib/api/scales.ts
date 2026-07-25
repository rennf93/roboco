import api from "./client";

// ---------------------------------------------------------------------------
// Scales (Board Program) engine — the Product Owner reviews the live
// backlog against the charter and authors 1-7 re-priority/cancellation
// drafts; the CEO approves or rejects each item individually here. Approving
// EXECUTES the item against the live target task — nothing here creates a
// task. Mirrors lib/api/pest-control.ts.
// ---------------------------------------------------------------------------

export interface RebalanceItem {
  id: string;
  task_ref: string;
  target_task_id: string;
  target_task_title: string;
  action: "reprioritize" | "cancel";
  new_priority?: number | null;
  rationale: string;
  status: "proposed" | "approved" | "rejected";
  reject_reason?: string | null;
  executed_detail?: string | null;
}

export interface RebalanceCycle {
  task_id: string;
  title: string;
  status: string;
  items: RebalanceItem[];
}

export interface RebalanceItemActionResult {
  status: string;
  item_id: string;
  executed_detail?: string | null;
  detail: string;
}

export const scalesApi = {
  listCycles: async (): Promise<RebalanceCycle[]> => {
    const { data } = await api.get<RebalanceCycle[]>("/scales/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<RebalanceItemActionResult> => {
    const { data } = await api.post<RebalanceItemActionResult>(
      `/scales/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<RebalanceItemActionResult> => {
    const { data } = await api.post<RebalanceItemActionResult>(
      `/scales/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
