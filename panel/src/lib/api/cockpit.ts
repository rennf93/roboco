import api from "./client";

export interface CockpitSummary {
  basis: string;
  north_star: string;
  objectives: Record<string, unknown>[];
  delivery: {
    task_counts: Record<string, number>;
    in_flight: number;
    blocked: number;
    awaiting_ceo: number;
  };
  spend: {
    spend_30d_usd: number;
    projected_monthly_usd: number | null;
    monthly_budget_cap_usd: number | null;
    over_budget: boolean;
  };
  pending_pitches: number;
  signals: CockpitSignal[];
}

export interface CockpitSignal {
  kind: string;
  summary: string;
  detail: string;
}

export const cockpitApi = {
  // GET /api/cockpit/summary — read-only company snapshot (CEO / Board / PM).
  summary: async (): Promise<CockpitSummary> => {
    const { data } = await api.get<CockpitSummary>("/cockpit/summary");
    return data;
  },

  // GET /api/cockpit/signals — just the strategy-engine signals (Dashboard panel);
  // lighter than /summary, which runs the full goals/usage/counts/pitches fan-out.
  signals: async (): Promise<CockpitSignal[]> => {
    const { data } = await api.get<{ signals: CockpitSignal[] }>(
      "/cockpit/signals"
    );
    return data.signals;
  },
};
