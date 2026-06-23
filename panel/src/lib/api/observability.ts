import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  StageTiming,
  BottleneckReport,
  ReworkReport,
  Scorecard,
} from "@/types";

// =============================================================================
// MOCK FALLBACKS (demo / no-backend mode)
// =============================================================================

const EMPTY_BOTTLENECK: BottleneckReport = {
  by_stage: [],
  worst_stage: null,
  active_blockers: 0,
};

const EMPTY_REWORK: ReworkReport = {
  rate: 0,
  total_completed: 0,
  total_reworked: 0,
  by_team: [],
  by_agent: [],
  rework_cost_usd: 0,
};

function emptyScorecard(scope: string, id: string): Scorecard {
  return {
    scope,
    id,
    name: id,
    tasks_completed: 0,
    avg_cycle_hours: null,
    rework_rate: 0,
    tokens: 0,
    cost_usd: 0,
  };
}

// =============================================================================
// API OBJECT
// =============================================================================

export const observabilityApi = {
  /** Per-stage cycle time — GET /dashboard/metrics/cycle-time?days&team */
  getCycleTime: async (days = 30, team?: string): Promise<StageTiming[]> => {
    if (isMockMode()) return [];
    const { data } = await api.get<StageTiming[]>("/dashboard/metrics/cycle-time", {
      params: { days, ...(team ? { team } : {}) },
    });
    return data;
  },

  /** Bottleneck distribution — GET /dashboard/metrics/bottlenecks?days */
  getBottlenecks: async (days = 30): Promise<BottleneckReport> => {
    if (isMockMode()) return EMPTY_BOTTLENECK;
    const { data } = await api.get<BottleneckReport>(
      "/dashboard/metrics/bottlenecks",
      { params: { days } },
    );
    return data;
  },

  /** Rework rate + attribution — GET /dashboard/metrics/rework?days&team */
  getRework: async (days = 30, team?: string): Promise<ReworkReport> => {
    if (isMockMode()) return EMPTY_REWORK;
    const { data } = await api.get<ReworkReport>("/dashboard/metrics/rework", {
      params: { days, ...(team ? { team } : {}) },
    });
    return data;
  },

  /** Per-cell scorecard — GET /dashboard/metrics/scorecard/team/{team}?days */
  getTeamScorecard: async (team: string, days = 7): Promise<Scorecard> => {
    if (isMockMode()) return emptyScorecard("cell", team);
    const { data } = await api.get<Scorecard>(
      `/dashboard/metrics/scorecard/team/${team}`,
      { params: { days } },
    );
    return data;
  },
};
