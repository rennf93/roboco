import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  StageTiming,
  BottleneckReport,
  ReworkReport,
  Scorecard,
  CeoScorecard,
  MemberScorecard,
  OrgScorecard,
  TaskMetrics,
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

const EMPTY_CEO: CeoScorecard = {
  member_kind: "ceo",
  approval_p50_seconds: 0,
  approval_p90_seconds: 0,
  approval_count: 0,
  unblock_p50_seconds: 0,
  unblock_count: 0,
  godmode_actions: 0,
};

function emptyMember(id: string): MemberScorecard {
  return {
    scope: "member",
    id,
    name: id,
    member_kind: "agent",
    tasks_completed: 0,
    first_pass_yield: null,
    effort_throughput_per_hour: null,
    active_runtime_hours: 0,
    turns: 0,
    tool_calls: 0,
    tokens: 0,
    cost_usd: 0,
    turns_per_task: null,
    tool_calls_per_task: null,
    revisions_caused: 0,
    revisions_received: 0,
    qa_pass_rate: null,
    escalations: 0,
    blocked_others: 0,
    idle_hours: 0,
    utilization: null,
    includes_live_inflight: false,
  };
}

function emptyOrg(team: string | null): OrgScorecard {
  return {
    scope: team ? "team" : "org",
    team,
    member_count: 0,
    tasks_completed: 0,
    first_pass_yield: null,
    effort_throughput_per_hour: null,
    active_runtime_hours: 0,
    turns: 0,
    tool_calls: 0,
    tokens: 0,
    cost_usd: 0,
    revisions_caused: 0,
    revisions_received: 0,
  };
}

// =============================================================================
// API OBJECT
// =============================================================================

export const observabilityApi = {
  /** Per-stage cycle time — GET /dashboard/metrics/cycle-time?days&team */
  getCycleTime: async (days = 30, team?: string): Promise<StageTiming[]> => {
    if (isMockMode()) return [];
    const { data } = await api.get<StageTiming[]>(
      "/dashboard/metrics/cycle-time",
      {
        params: { days, ...(team ? { team } : {}) },
      },
    );
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

  /** CEO-as-member scorecard — GET /dashboard/metrics/member/ceo?days */
  getCeoScorecard: async (days = 30): Promise<CeoScorecard> => {
    if (isMockMode()) return EMPTY_CEO;
    const { data } = await api.get<CeoScorecard>(
      "/dashboard/metrics/member/ceo",
      { params: { days } },
    );
    return data;
  },

  /** Per-member rollup scorecard — GET /dashboard/metrics/member/{id}?days */
  getMemberScorecard: async (
    agentId: string,
    days = 30,
  ): Promise<MemberScorecard> => {
    if (isMockMode()) return emptyMember(agentId);
    const { data } = await api.get<MemberScorecard>(
      `/dashboard/metrics/member/${agentId}`,
      { params: { days } },
    );
    return data;
  },

  /** Every agent's rollup scorecard in one batch (N+1 fix for the panel's
   * Members table) — GET /dashboard/metrics/members?team&days */
  getAllMemberScorecards: async (
    days = 30,
    team?: string,
  ): Promise<MemberScorecard[]> => {
    if (isMockMode()) return [];
    const { data } = await api.get<MemberScorecard[]>(
      "/dashboard/metrics/members",
      { params: { days, ...(team ? { team } : {}) } },
    );
    return data;
  },

  /** Org / team rollup — GET /dashboard/metrics/org?team&days */
  getOrgScorecard: async (days = 30, team?: string): Promise<OrgScorecard> => {
    if (isMockMode()) return emptyOrg(team ?? null);
    const { data } = await api.get<OrgScorecard>("/dashboard/metrics/org", {
      params: { days, ...(team ? { team } : {}) },
    });
    return data;
  },

  /** Granular per-task metrics — GET /dashboard/metrics/task/{id} */
  getTaskMetrics: async (taskId: string): Promise<TaskMetrics | null> => {
    if (isMockMode()) return null;
    const { data } = await api.get<TaskMetrics>(
      `/dashboard/metrics/task/${taskId}`,
    );
    return data;
  },
};
