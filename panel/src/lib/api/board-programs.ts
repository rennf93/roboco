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
};
