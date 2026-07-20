/**
 * Metrics tab demo fixtures (`/tg?demo=1`) — tg-only, so a plain static
 * import is fine here (unlike demo-data.ts, which every other tab dynamic-
 * imports to keep it out of the prod bundle: this file is smaller and only
 * ever pulled in by tg-metrics-tab.tsx, itself already tg-scoped).
 *
 * Every number below is organic (no round figures) but internally
 * consistent: the by-agent, by-team, and by-model cost slices all sum to
 * the same TOTAL_COST, which is also the 7-point spend series' total and
 * the hero summary's total_cost_usd.
 */

import type {
  AgentUsageRow,
  CacheEfficiencyResponse,
  MemberScorecard,
  ModelUsageSlice,
  ReworkReport,
  SpawnWasteResponse,
  StageTiming,
  TeamUsageRow,
  UsageProjection,
  UsageSummary,
  UsageTimePoint,
} from "@/types";

// Rough $/token rate matching the ratio the desktop mock data already uses
// (usage.ts's mockTimeSeries etc.), so demo token counts read as plausible
// alongside demo dollar figures.
const TOKENS_PER_DOLLAR = 1 / 0.00003;

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

function tokensFor(cost_usd: number) {
  const total_tokens = Math.round(cost_usd * TOKENS_PER_DOLLAR);
  return {
    tokens_input: Math.round(total_tokens * 0.55),
    tokens_output: Math.round(total_tokens * 0.35),
    total_tokens,
  };
}

function agentRow(
  agent_slug: string,
  cost_usd: number,
  total: number,
): AgentUsageRow {
  return {
    agent_slug,
    ...tokensFor(cost_usd),
    cost_usd,
    pct_of_total: round1((cost_usd / total) * 100),
  };
}

function teamRow(team: string, cost_usd: number, total: number): TeamUsageRow {
  return {
    team,
    ...tokensFor(cost_usd),
    cost_usd,
    pct_of_total: round1((cost_usd / total) * 100),
  };
}

function modelRow(
  model: string,
  cost_usd: number,
  total: number,
): ModelUsageSlice {
  return {
    model,
    ...tokensFor(cost_usd),
    cost_usd,
    pct_of_total: round1((cost_usd / total) * 100),
  };
}

const AGENT_COSTS: Array<[string, number]> = [
  ["be-dev-1", 18.42],
  ["fe-dev-2", 14.07],
  ["main-pm", 11.63],
  ["ux-dev-1", 9.28],
  ["be-qa", 7.51],
  ["fe-pm", 5.63],
];

/** Source of truth for every other slice's total — same grand total sliced
 * three different ways (agent / team / model), the way real spend is. */
const TOTAL_COST = AGENT_COSTS.reduce((sum, [, cost]) => sum + cost, 0);

export const DEMO_AGENT_USAGE: AgentUsageRow[] = AGENT_COSTS.map(
  ([slug, cost]) => agentRow(slug, cost, TOTAL_COST),
);

export const DEMO_TEAM_USAGE: TeamUsageRow[] = (
  [
    ["backend", 24.1],
    ["frontend", 19.35],
    ["ux_ui", 13.86],
    ["main_pm", 9.23],
  ] as Array<[string, number]>
).map(([team, cost]) => teamRow(team, cost, TOTAL_COST));

export const DEMO_MODEL_USAGE: ModelUsageSlice[] = (
  [
    ["claude-opus-4-6", 42.1],
    ["glm-5.2:cloud", 16.8],
    ["grok-build", 7.64],
  ] as Array<[string, number]>
).map(([model, cost]) => modelRow(model, cost, TOTAL_COST));

// 7 daily points, oldest -> newest, ending today — happens to sum to the
// same TOTAL_COST as the agent/team/model slices above.
const SERIES_COSTS = [4.12, 9.87, 6.4, 12.3, 8.05, 14.6, 11.2];

function daysAgoIso(daysBack: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  d.setHours(12, 0, 0, 0);
  return d.toISOString();
}

export const DEMO_USAGE_SERIES: UsageTimePoint[] = SERIES_COSTS.map(
  (cost_usd, i) => ({
    bucket: daysAgoIso(SERIES_COSTS.length - 1 - i),
    ...tokensFor(cost_usd),
    cost_usd,
  }),
);

export const DEMO_USAGE_SUMMARY: UsageSummary = {
  ...tokensFor(TOTAL_COST),
  total_cost_usd: TOTAL_COST,
  trend_pct: 8.4,
  period: "7d",
};

export const DEMO_DELIVERY: { rework: ReworkReport; cycle: StageTiming[] } = {
  rework: {
    rate: 11 / 48,
    total_completed: 48,
    total_reworked: 11,
    by_team: [
      { team: "backend", rate: 0.18 },
      { team: "frontend", rate: 0.26 },
      { team: "ux_ui", rate: 0.15 },
    ],
    by_agent: [
      {
        agent_slug: "fe-dev-2",
        rate: 0.31,
        qa_fails: 3,
        pr_fails: 1,
        pm_rejects: 1,
        ceo_rejects: 0,
      },
      {
        agent_slug: "be-dev-1",
        rate: 0.19,
        qa_fails: 2,
        pr_fails: 1,
        pm_rejects: 0,
        ceo_rejects: 0,
      },
      {
        agent_slug: "ux-dev-1",
        rate: 0.22,
        qa_fails: 1,
        pr_fails: 1,
        pm_rejects: 1,
        ceo_rejects: 0,
      },
      {
        agent_slug: "main-pm",
        rate: 0.05,
        qa_fails: 0,
        pr_fails: 0,
        pm_rejects: 1,
        ceo_rejects: 0,
      },
    ],
    rework_cost_usd: 9.47,
  },
  cycle: [
    {
      status: "awaiting_pm_review",
      avg_seconds: 44_640,
      median_seconds: 39_600,
      p90_seconds: 72_000,
      sample_size: 22,
    },
    {
      status: "in_progress",
      avg_seconds: 30_960,
      median_seconds: 27_000,
      p90_seconds: 54_000,
      sample_size: 48,
    },
    {
      status: "awaiting_qa",
      avg_seconds: 12_600,
      median_seconds: 10_800,
      p90_seconds: 21_600,
      sample_size: 44,
    },
    {
      status: "awaiting_ceo_approval",
      avg_seconds: 9_000,
      median_seconds: 7_200,
      p90_seconds: 16_200,
      sample_size: 9,
    },
  ],
};

export const DEMO_EFFICIENCY: {
  cache: CacheEfficiencyResponse;
  projection: UsageProjection;
  spawnWaste: SpawnWasteResponse;
} = {
  cache: {
    cache_hit_rate: 0.334,
    tokens_cache_read: 812_000,
    tokens_cache_write: 145_000,
    tokens_input: 402_000,
    cost_saved_by_cache_usd: 7.62,
    period: "7d",
  },
  projection: {
    total_cost_7d: TOTAL_COST,
    avg_daily_cost_usd: round1(TOTAL_COST / 7),
    projected_monthly_cost_usd: round1((TOTAL_COST / 7) * 30),
    basis_days: 7,
  },
  spawnWaste: {
    total_spawns: 132,
    unproductive_spawns: 41,
    unproductive_pct: 31.1,
    by_role: [
      {
        role: "developer",
        spawns: 58,
        unproductive: 22,
        unproductive_pct: 37.9,
      },
      { role: "cell_pm", spawns: 34, unproductive: 11, unproductive_pct: 32.4 },
      { role: "qa", spawns: 21, unproductive: 5, unproductive_pct: 23.8 },
      { role: "main_pm", spawns: 19, unproductive: 3, unproductive_pct: 15.8 },
    ],
    respawn_strikes: [],
    period: "7d",
  },
};

export const DEMO_MEMBER_SCORECARD: MemberScorecard = {
  scope: "member",
  id: "demo-be-dev-1",
  name: "Backend Dev 1",
  member_kind: "agent",
  tasks_completed: 14,
  first_pass_yield: 0.786,
  effort_throughput_per_hour: 1.9,
  active_runtime_hours: 38.4,
  turns: 212,
  tool_calls: 963,
  tokens: 614_000,
  cost_usd: 18.42,
  turns_per_task: 15.1,
  tool_calls_per_task: 68.8,
  revisions_caused: 0,
  revisions_received: 3,
  qa_pass_rate: 0.786,
  escalations: 1,
  blocked_others: 0,
  idle_hours: 6.2,
  utilization: 0.612,
  includes_live_inflight: false,
};
