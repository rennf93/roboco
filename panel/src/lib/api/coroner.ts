import api from "./client";

// ---------------------------------------------------------------------------
// Coroner (Board Program) engine — the Auditor's event-triggered postmortem:
// a task bounced >=3x, was cancelled after work started, or was budget-
// blocked. One propose_postmortem call completes the autopsy atomically —
// the EXPLORATION TASK has no per-item decision to wait on — but the
// postmortem's single process change still carries its own proposed/
// approved/rejected status the CEO decides on afterward (unless it already
// drafted a playbook: process_change_status "not_applicable", nothing left
// to decide). Unlike Periscope/Sentinel there is no item id — a postmortem
// is one process change, not a list — so the action routes key on the task
// id alone.
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
  process_change_status:
    "proposed" | "approved" | "rejected" | "not_applicable";
  process_change_reject_reason: string | null;
  process_change_materialized_task_id: string | null;
}

export interface ProcessChangeActionResult {
  status: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const coronerApi = {
  listPostmortems: async (): Promise<Postmortem[]> => {
    const { data } = await api.get<Postmortem[]>("/coroner/postmortems");
    return data;
  },
  approveProcessChange: async (
    taskId: string,
  ): Promise<ProcessChangeActionResult> => {
    const { data } = await api.post<ProcessChangeActionResult>(
      `/coroner/postmortems/${taskId}/process-change/approve`,
      {},
    );
    return data;
  },
  rejectProcessChange: async (
    taskId: string,
    reason: string,
  ): Promise<ProcessChangeActionResult> => {
    const { data } = await api.post<ProcessChangeActionResult>(
      `/coroner/postmortems/${taskId}/process-change/reject`,
      { reason },
    );
    return data;
  },
};
