import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  TokenUsageSnapshot,
  AgentUsageRow,
  UsageSession,
  UsageTimePoint,
  ModelUsageSlice,
} from "@/types";

// =============================================================================
// MOCK DATA
// =============================================================================

function mockSnapshot(): TokenUsageSnapshot {
  return {
    tokens_today: 124_800,
    cost_today: 3.74,
    cost_this_week: 22.50,
    cost_last_week: 18.20,
    active_sessions: 3,
    cache_savings: 1.25,
    top_consumer: "be-dev-1",
  };
}

function mockTimeSeries(): UsageTimePoint[] {
  const now = new Date();
  return Array.from({ length: 24 }, (_, i) => {
    const ts = new Date(now);
    ts.setHours(now.getHours() - (23 - i), 0, 0, 0);
    const base = 3_000 + Math.round(Math.random() * 4_000);
    return {
      timestamp: ts.toISOString(),
      tokens_input: Math.round(base * 0.45),
      tokens_output: Math.round(base * 0.35),
      tokens_cache: Math.round(base * 0.20),
    };
  });
}

function mockAgentUsage(): AgentUsageRow[] {
  const agents = [
    { agent_id: "be-dev-1", agent_name: "BE-Dev-1", team: "backend" },
    { agent_id: "be-dev-2", agent_name: "BE-Dev-2", team: "backend" },
    { agent_id: "fe-dev-1", agent_name: "FE-Dev-1", team: "frontend" },
    { agent_id: "fe-dev-2", agent_name: "FE-Dev-2", team: "frontend" },
    { agent_id: "ux-dev-1", agent_name: "UX-Dev-1", team: "ux_ui" },
    { agent_id: "be-qa", agent_name: "BE-QA", team: "backend" },
    { agent_id: "fe-qa", agent_name: "FE-QA", team: "frontend" },
    { agent_id: "main-pm", agent_name: "Main-PM", team: "main_pm" },
  ];
  return agents.map((a) => ({
    ...a,
    tokens_today: Math.round(5_000 + Math.random() * 25_000),
    cost_today: parseFloat((0.15 + Math.random() * 0.75).toFixed(2)),
    tokens_total: Math.round(50_000 + Math.random() * 200_000),
  }));
}

function mockSessions(): UsageSession[] {
  const models = ["claude-opus-4", "claude-sonnet-4", "claude-haiku-4"];
  const agents = [
    { agent_id: "be-dev-1", agent_name: "BE-Dev-1" },
    { agent_id: "be-dev-2", agent_name: "BE-Dev-2" },
    { agent_id: "fe-dev-1", agent_name: "FE-Dev-1" },
    { agent_id: "fe-qa", agent_name: "FE-QA" },
    { agent_id: "main-pm", agent_name: "Main-PM" },
  ];
  return Array.from({ length: 35 }, (_, i) => {
    const agent = agents[i % agents.length];
    const model = models[i % models.length];
    const input = Math.round(2_000 + Math.random() * 8_000);
    const output = Math.round(500 + Math.random() * 3_000);
    const cache = Math.round(100 + Math.random() * 1_000);
    const started = new Date(Date.now() - (i + 1) * 12 * 60_000);
    const ended = i < 3 ? null : new Date(started.getTime() + Math.round(5 + Math.random() * 55) * 60_000);
    return {
      id: `session-mock-${i + 1}`,
      agent_id: agent.agent_id,
      agent_name: agent.agent_name,
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

function mockModelUsage(): ModelUsageSlice[] {
  return [
    { model: "claude-opus-4", tokens: 68_400, cost: 2.05, percentage: 54.8 },
    { model: "claude-sonnet-4", tokens: 43_200, cost: 1.30, percentage: 34.6 },
    { model: "claude-haiku-4", tokens: 13_200, cost: 0.40, percentage: 10.6 },
  ];
}

// =============================================================================
// API OBJECT
// =============================================================================

export const usageApi = {
  /** Current-day snapshot for the overview panel */
  getUsageSnapshot: async (): Promise<TokenUsageSnapshot> => {
    if (isMockMode()) return mockSnapshot();
    const { data } = await api.get<TokenUsageSnapshot>("/usage/snapshot");
    return data;
  },

  /** Hourly time-series data for the stacked area chart */
  getUsageTimeSeries: async (hours: number = 24): Promise<UsageTimePoint[]> => {
    if (isMockMode()) return mockTimeSeries();
    const { data } = await api.get<UsageTimePoint[]>("/usage/time-series", {
      params: { hours },
    });
    return data;
  },

  /** Per-agent usage rows for the bar chart and agent cards */
  getAgentUsage: async (): Promise<AgentUsageRow[]> => {
    if (isMockMode()) return mockAgentUsage();
    const { data } = await api.get<AgentUsageRow[]>("/usage/agents");
    return data;
  },

  /** Recent inference sessions for the sessions table */
  getUsageSessions: async (limit: number = 100): Promise<UsageSession[]> => {
    if (isMockMode()) return mockSessions();
    const { data } = await api.get<UsageSession[]>("/usage/sessions", {
      params: { limit },
    });
    return data;
  },

  /** Per-model usage slices for the donut chart */
  getModelUsage: async (): Promise<ModelUsageSlice[]> => {
    if (isMockMode()) return mockModelUsage();
    const { data } = await api.get<ModelUsageSlice[]>("/usage/models");
    return data;
  },
};
