import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  UsageSummary,
  AgentUsageRow,
  TeamUsageRow,
  ModelUsageSlice,
  UsageTimePoint,
  UsageProjection,
  CacheEfficiencyResponse,
  UsageSession,
} from "@/types";

export type UsagePeriod = "24h" | "7d" | "30d";

// =============================================================================
// MOCK DATA  — shapes must exactly match the real backend response schemas
// =============================================================================

function mockSummary(period: UsagePeriod = "24h"): UsageSummary {
  const scale = period === "30d" ? 30 : period === "7d" ? 7 : 1;
  const base = 124_800 * scale;
  return {
    tokens_input: Math.round(base * 0.55),
    tokens_output: Math.round(base * 0.35),
    total_tokens: base,
    total_cost_usd: parseFloat((base * 0.000030).toFixed(6)),
    trend_pct: 12.5,
    period,
  };
}

function mockTimeSeries(period: UsagePeriod = "24h"): UsageTimePoint[] {
  const now = new Date();
  const points = period === "24h" ? 24 : period === "7d" ? 7 : 30;
  const step = period === "24h" ? "hour" : "day";
  return Array.from({ length: points }, (_, i) => {
    const ts = new Date(now);
    if (step === "hour") {
      ts.setHours(now.getHours() - (points - 1 - i), 0, 0, 0);
    } else {
      ts.setDate(now.getDate() - (points - 1 - i));
      ts.setHours(0, 0, 0, 0);
    }
    const base = 3_000 + Math.round(Math.random() * 4_000);
    const tokens_input = Math.round(base * 0.55);
    const tokens_output = Math.round(base * 0.35);
    const total_tokens = base;
    return {
      bucket: ts.toISOString(),
      tokens_input,
      tokens_output,
      total_tokens,
      cost_usd: parseFloat((total_tokens * 0.000030).toFixed(6)),
    };
  });
}

function mockAgentUsage(period: UsagePeriod = "24h"): AgentUsageRow[] {
  const scale = period === "30d" ? 30 : period === "7d" ? 7 : 1;
  const agents = [
    { agent_slug: "be-dev-1" },
    { agent_slug: "be-dev-2" },
    { agent_slug: "fe-dev-1" },
    { agent_slug: "fe-dev-2" },
    { agent_slug: "ux-dev-1" },
    { agent_slug: "be-qa" },
    { agent_slug: "fe-qa" },
    { agent_slug: "main-pm" },
  ];
  const grand = agents.length * 15_000 * scale;
  return agents.map((a) => {
    const ti = Math.round((5_000 + Math.random() * 20_000) * scale);
    const to_ = Math.round(ti * 0.65);
    const total = ti + to_;
    return {
      agent_slug: a.agent_slug,
      tokens_input: ti,
      tokens_output: to_,
      total_tokens: total,
      cost_usd: parseFloat((total * 0.000030).toFixed(6)),
      pct_of_total: parseFloat(((total / grand) * 100).toFixed(2)),
    };
  });
}

function mockTeamUsage(period: UsagePeriod = "24h"): TeamUsageRow[] {
  const scale = period === "30d" ? 30 : period === "7d" ? 7 : 1;
  const teams = ["backend", "frontend", "ux_ui", "main_pm"];
  const grand = teams.length * 50_000 * scale;
  return teams.map((team) => {
    const ti = Math.round((30_000 + Math.random() * 40_000) * scale);
    const to_ = Math.round(ti * 0.65);
    const total = ti + to_;
    return {
      team,
      tokens_input: ti,
      tokens_output: to_,
      total_tokens: total,
      cost_usd: parseFloat((total * 0.000030).toFixed(6)),
      pct_of_total: parseFloat(((total / grand) * 100).toFixed(2)),
    };
  });
}

function mockModelUsage(period: UsagePeriod = "24h"): ModelUsageSlice[] {
  const scale = period === "30d" ? 30 : period === "7d" ? 7 : 1;
  const models = [
    { model: "claude-opus-4", share: 0.548 },
    { model: "claude-sonnet-4", share: 0.346 },
    { model: "claude-haiku-4", share: 0.106 },
  ];
  const base = 124_800 * scale;
  return models.map((m) => {
    const ti = Math.round(base * m.share * 0.55);
    const to_ = Math.round(base * m.share * 0.35);
    const total = Math.round(base * m.share);
    return {
      model: m.model,
      tokens_input: ti,
      tokens_output: to_,
      total_tokens: total,
      cost_usd: parseFloat((total * 0.000030).toFixed(6)),
      pct_of_total: parseFloat((m.share * 100).toFixed(1)),
    };
  });
}

function mockProjection(): UsageProjection {
  const total_cost_7d = parseFloat((124_800 * 7 * 0.000030).toFixed(6));
  return {
    total_cost_7d,
    avg_daily_cost_usd: parseFloat((total_cost_7d / 7).toFixed(6)),
    projected_monthly_cost_usd: parseFloat((total_cost_7d / 7 * 30).toFixed(4)),
    basis_days: 7,
  };
}

function mockCacheEfficiency(period: UsagePeriod = "24h"): CacheEfficiencyResponse {
  return {
    cache_hit_rate: 0.3142,
    tokens_cache_read: 39_168,
    tokens_cache_write: 12_480,
    tokens_input: 85_632,
    cost_saved_by_cache_usd: parseFloat((39_168 * (3.00 - 0.30) / 1_000_000).toFixed(6)),
    period,
  };
}

function mockSessions(): UsageSession[] {
  const models = ["claude-opus-4", "claude-sonnet-4", "claude-haiku-4"];
  const agentSlugs = ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-qa", "main-pm"];
  return Array.from({ length: 35 }, (_, i) => {
    const agent_slug = agentSlugs[i % agentSlugs.length];
    const model = models[i % models.length];
    const input = Math.round(2_000 + Math.random() * 8_000);
    const output = Math.round(500 + Math.random() * 3_000);
    const cache = Math.round(100 + Math.random() * 1_000);
    const started = new Date(Date.now() - (i + 1) * 12 * 60_000);
    const ended = i < 3 ? null : new Date(started.getTime() + Math.round(5 + Math.random() * 55) * 60_000);
    return {
      id: `session-mock-${i + 1}`,
      agent_slug,
      started_at: started.toISOString(),
      ended_at: ended ? ended.toISOString() : null,
      tokens_input: input,
      tokens_output: output,
      tokens_cache: cache,
      total_tokens: input + output + cache,
      cost: parseFloat(((input + output) * 0.00003 + cache * 0.000003).toFixed(4)),
      model,
    };
  });
}

// =============================================================================
// API OBJECT
// =============================================================================

export const usageApi = {
  /** Aggregated token usage summary — GET /usage/summary?period= */
  getUsageSummary: async (period: UsagePeriod = "24h"): Promise<UsageSummary> => {
    if (isMockMode()) return mockSummary(period);
    const { data } = await api.get<UsageSummary>("/usage/summary", {
      params: { period },
    });
    return data;
  },

  /** Bucketed time-series — GET /usage/time-series?period= */
  getUsageTimeSeries: async (period: UsagePeriod = "24h"): Promise<UsageTimePoint[]> => {
    if (isMockMode()) return mockTimeSeries(period);
    const { data } = await api.get<UsageTimePoint[]>("/usage/time-series", {
      params: { period },
    });
    return data;
  },

  /** Per-agent usage rows — GET /usage/by-agent?period= */
  getAgentUsage: async (period: UsagePeriod = "24h"): Promise<AgentUsageRow[]> => {
    if (isMockMode()) return mockAgentUsage(period);
    const { data } = await api.get<AgentUsageRow[]>("/usage/by-agent", {
      params: { period },
    });
    return data;
  },

  /** Per-team usage rows — GET /usage/by-team?period= */
  getTeamUsage: async (period: UsagePeriod = "24h"): Promise<TeamUsageRow[]> => {
    if (isMockMode()) return mockTeamUsage(period);
    const { data } = await api.get<TeamUsageRow[]>("/usage/by-team", {
      params: { period },
    });
    return data;
  },

  /** Per-model usage slices — GET /usage/by-model?period= */
  getModelUsage: async (period: UsagePeriod = "24h"): Promise<ModelUsageSlice[]> => {
    if (isMockMode()) return mockModelUsage(period);
    const { data } = await api.get<ModelUsageSlice[]>("/usage/by-model", {
      params: { period },
    });
    return data;
  },

  /** Monthly cost projection — GET /usage/projection */
  getUsageProjection: async (): Promise<UsageProjection> => {
    if (isMockMode()) return mockProjection();
    const { data } = await api.get<UsageProjection>("/usage/projection");
    return data;
  },

  /** Cache efficiency stats — GET /usage/cache-efficiency?period= */
  getCacheEfficiency: async (period: UsagePeriod = "24h"): Promise<CacheEfficiencyResponse> => {
    if (isMockMode()) return mockCacheEfficiency(period);
    const { data } = await api.get<CacheEfficiencyResponse>("/usage/cache-efficiency", {
      params: { period },
    });
    return data;
  },

  /**
   * Recent inference sessions — mock-mode only.
   *
   * The backend has no /usage/sessions endpoint.  In production this
   * returns an empty array so SessionsTable shows a graceful "no data"
   * state instead of throwing a 404.
   */
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  getUsageSessions: async (_limit: number = 100): Promise<UsageSession[]> => {
    if (isMockMode()) return mockSessions();
    return [];
  },
};
