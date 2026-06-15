import api from "./client";

// ---------------------------------------------------------------------------
// Pitches — Board proposals the CEO approves to auto-provision a product.
// The panel (acting as CEO) lists pitches and approves/rejects them; the
// Board authors them through the agent gateway.
// ---------------------------------------------------------------------------

export interface Pitch {
  id: string;
  title: string;
  slug: string;
  problem: string;
  proposed_solution: string;
  target_cells: string[];
  status: string;
  created_by: string;
  decided_by?: string | null;
  decision_notes?: string | null;
  provisioned_product_id?: string | null;
  provisioned_project_ids: string[];
  seed_task_id?: string | null;
  created_at?: string | null;
}

export const pitchesApi = {
  list: async (statusFilter?: string): Promise<Pitch[]> => {
    const { data } = await api.get<Pitch[]>("/pitches", {
      params: statusFilter ? { status_filter: statusFilter } : undefined,
    });
    return data;
  },
  approve: async (id: string, notes?: string): Promise<Pitch> => {
    const { data } = await api.post<Pitch>(`/pitches/${id}/approve`, { notes });
    return data;
  },
  reject: async (id: string, notes: string): Promise<Pitch> => {
    const { data } = await api.post<Pitch>(`/pitches/${id}/reject`, { notes });
    return data;
  },
};
