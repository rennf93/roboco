import api from "./client";

// ---------------------------------------------------------------------------
// Board Programs — the generic registry (fourteen entries: the migrated
// roadmap/x_feature cycles plus the twelve Phase-2/3 programs) the CEO
// monitors and can run off-schedule. See roboco/api/routes/board_programs.py.
// ---------------------------------------------------------------------------

export interface BoardProgram {
  key: string;
  title: string;
  description: string;
  role: string;
  trigger: string;
  scope: string;
  enabled: boolean;
  opted_in_project_slugs: string[];
  last_opened_at: string | null;
  open_cycle: boolean;
  last_cycle_summary: string | null;
}

// One CEO approve/reject recorded on a cycle. item_snapshot is the bounded
// payload BoardProgramEngine.record_decision stamps at decision time; it
// survives even after the originating exploration task is deleted, absent
// only for a decision recorded before this existed.
export interface BoardProgramDecision {
  item_ref: string;
  verdict: string;
  reason: string | null;
  item_snapshot: Record<string, unknown> | null;
}

export interface BoardProgramCycle {
  id: string;
  opened_at: string;
  closed_at: string | null;
  items_proposed: number;
  items_approved: number;
  items_rejected: number;
  nothing_to_propose_reason: string | null;
  decisions: BoardProgramDecision[];
}

export const boardProgramsApi = {
  list: async (): Promise<BoardProgram[]> => {
    const { data } = await api.get<BoardProgram[]>("/board-programs");
    return data;
  },
  runNow: async (key: string): Promise<BoardProgram> => {
    const { data } = await api.post<BoardProgram>(
      `/board-programs/${key}/run-now`,
    );
    return data;
  },
  cycles: async (key: string, limit = 20): Promise<BoardProgramCycle[]> => {
    const { data } = await api.get<BoardProgramCycle[]>(
      `/board-programs/${key}/cycles`,
      { params: { limit } },
    );
    return data;
  },
};
