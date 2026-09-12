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
    completed_30d?: number;
    median_lead_time_hours?: number | null;
    // Fraction of tasks shipped to merge with no human code edits (0–1).
    // Formatted as a percentage, matching the phone's pctOrDash convention
    // in tg-metrics-tab.tsx.
    first_pass_yield?: number | null;
    // Count of critical escaped defects per release.
    escaped_defects?: number | null;
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
      "/cockpit/signals",
    );
    return data.signals;
  },
};
