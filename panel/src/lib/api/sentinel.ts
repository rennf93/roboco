import api from "./client";

// ---------------------------------------------------------------------------
// Sentinel (Board Program) engine — the Auditor files a weekly "state of
// quality" report (waiver-accumulation trends, conventions-violation
// hotspots, budget anomalies). The report itself is read-only — a report,
// not a queue item, the exploration task completes atomically at propose
// time — but each drift item still carries its own proposed/approved/
// rejected status the CEO decides on afterward. Mirrors lib/api/
// periscope.ts's per-item approve/reject shape.
// ---------------------------------------------------------------------------

export interface QualityReportItem {
  id: string;
  area: string;
  observation: string;
  evidence: string;
  suggested_action: string;
  status: "proposed" | "approved" | "rejected";
  reject_reason?: string | null;
  materialized_task_id?: string | null;
}

export interface QualityReport {
  task_id: string;
  title: string;
  completed_at: string | null;
  headline: string;
  items: QualityReportItem[];
  overall_assessment: string;
}

export interface QualityReportItemActionResult {
  status: string;
  item_id: string;
  materialized_task_id?: string | null;
  detail: string;
}

export const sentinelApi = {
  listReports: async (): Promise<QualityReport[]> => {
    const { data } = await api.get<QualityReport[]>("/sentinel/reports");
    return data;
  },
  approveItem: async (
    taskId: string,
    itemId: string,
  ): Promise<QualityReportItemActionResult> => {
    const { data } = await api.post<QualityReportItemActionResult>(
      `/sentinel/reports/${taskId}/items/${itemId}/approve`,
      {},
    );
    return data;
  },
  rejectItem: async (
    taskId: string,
    itemId: string,
    reason: string,
  ): Promise<QualityReportItemActionResult> => {
    const { data } = await api.post<QualityReportItemActionResult>(
      `/sentinel/reports/${taskId}/items/${itemId}/reject`,
      { reason },
    );
    return data;
  },
};
