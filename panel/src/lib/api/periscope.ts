import api from "./client";

// ---------------------------------------------------------------------------
// Periscope (Board Program) engine — the Head of Marketing files a weekly
// market-research brief (competitors, adjacent-tool releases, positioning
// shifts). Unlike Roadmap/Pest Control this is a REPORT, not a queue item:
// read-only here, no approve/reject. Mirrors lib/api/pest-control.ts's shape
// minus the mutating routes.
// ---------------------------------------------------------------------------

export interface MarketBriefFinding {
  id: string;
  claim: string;
  source_url: string;
  relevance: string;
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

export const periscopeApi = {
  listBriefs: async (): Promise<MarketBrief[]> => {
    const { data } = await api.get<MarketBrief[]>("/periscope/briefs");
    return data;
  },
};
