import api from "./client";

// ---------------------------------------------------------------------------
// Mirror (Board Program) engine — the Head of Marketing audits an opted-in
// project's messaging surfaces (README, docs-site, website) against the
// charter and shipped reality and authors 1-5 evidence-backed messaging-fix
// item drafts; the CEO approves or rejects each item individually here.
// Approving materializes a BACKLOG docs task; nothing starts automatically.
// Mirrors lib/api/spackle.ts.
// ---------------------------------------------------------------------------

export interface MessagingFixItem {
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

export interface MirrorCycle {
  task_id: string;
  title: string;
  status: string;
  items: MessagingFixItem[];
}

export interface MessagingFixItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const mirrorApi = {
  listCycles: async (): Promise<MirrorCycle[]> => {
    const { data } = await api.get<MirrorCycle[]>("/mirror/cycles");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<MessagingFixItemActionResult> => {
    const { data } = await api.post<MessagingFixItemActionResult>(
      `/mirror/cycles/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<MessagingFixItemActionResult> => {
    const { data } = await api.post<MessagingFixItemActionResult>(
      `/mirror/cycles/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
