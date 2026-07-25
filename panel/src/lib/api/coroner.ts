import api from "./client";

// ---------------------------------------------------------------------------
// Coroner (Board Program) engine — the Auditor's event-triggered postmortem:
// a task bounced >=3x, was cancelled after work started, or was budget-
// blocked. One propose_postmortem call completes the autopsy atomically —
// there is no per-item approve/reject like Pest Control/Roadmap, so this is
// a plain read-only list. Mirrors lib/api/pest-control.ts.
// ---------------------------------------------------------------------------

export interface Postmortem {
  task_id: string;
  title: string;
  completed_at: string | null;
  incident_task_id: string | null;
  incident_kind: string | null;
  incident_title: string | null;
  incident_summary: string | null;
  root_cause: string | null;
  failed_stage: string | null;
  process_change_kind: string | null;
  process_change_description: string | null;
  playbook_id: string | null;
}

export const coronerApi = {
  listPostmortems: async (): Promise<Postmortem[]> => {
    const { data } = await api.get<Postmortem[]>("/coroner/postmortems");
    return data;
  },
};
