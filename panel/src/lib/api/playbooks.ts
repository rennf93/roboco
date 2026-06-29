import api from "./client";

// ---------------------------------------------------------------------------
// Playbooks — curated, reusable procedures. Delivery agents draft them; the
// Auditor (or CEO, via this panel) approves → indexed + auto-suggested, or
// rejects → archived.
// ---------------------------------------------------------------------------

export interface Playbook {
  id: string;
  title: string;
  slug: string;
  problem: string;
  procedure: string;
  tags: string[];
  team?: string | null;
  scope: string;
  status: string;
  created_at?: string | null;
}

export const playbooksApi = {
  listDrafts: async (): Promise<Playbook[]> => {
    const { data } = await api.get<Playbook[]>("/playbooks", {
      params: { status: "draft" },
    });
    return data;
  },
  approve: async (id: string): Promise<Playbook> => {
    const { data } = await api.post<Playbook>(`/playbooks/${id}/approve`);
    return data;
  },
  reject: async (id: string, reason: string): Promise<Playbook> => {
    const { data } = await api.post<Playbook>(`/playbooks/${id}/reject`, {
      reason,
    });
    return data;
  },
};
