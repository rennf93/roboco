import api from "./client";

// ---------------------------------------------------------------------------
// Periscope (Board Program) engine — the Head of Marketing files a weekly
// market-research brief (competitors, adjacent-tool releases, positioning
// shifts). The brief itself is read-only — a report, not a queue item, the
// exploration task completes atomically at propose time — but each cited
// finding still carries its own proposed/approved/rejected status the CEO
// decides on afterward. Mirrors lib/api/roadmap.ts's per-item approve/reject
// shape.
// ---------------------------------------------------------------------------

export interface MarketBriefFinding {
  id: string;
  claim: string;
  source_url: string;
  relevance: string;
  status: "proposed" | "approved" | "rejected";
  reject_reason?: string | null;
  materialized_task_id?: string | null;
}

export interface MarketBrief {
  task_id: string;
  title: string;
  completed_at: string | null;
  headline: string;
  findings: MarketBriefFinding[];
  threats: string[];
  opportunities: string[];
  positioning_note: string;
}

export interface MarketBriefFindingActionResult {
  status: string;
  finding_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const periscopeApi = {
  listBriefs: async (): Promise<MarketBrief[]> => {
    const { data } = await api.get<MarketBrief[]>("/periscope/briefs");
    return data;
  },
  approveFinding: async (
    taskId: string,
    findingId: string,
  ): Promise<MarketBriefFindingActionResult> => {
    const { data } = await api.post<MarketBriefFindingActionResult>(
      `/periscope/briefs/${taskId}/findings/${findingId}/approve`,
      {},
    );
    return data;
  },
  rejectFinding: async (
    taskId: string,
    findingId: string,
    reason: string,
  ): Promise<MarketBriefFindingActionResult> => {
    const { data } = await api.post<MarketBriefFindingActionResult>(
      `/periscope/briefs/${taskId}/findings/${findingId}/reject`,
      { reason },
    );
    return data;
  },
};
