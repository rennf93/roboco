import api from "./client";

// ---------------------------------------------------------------------------
// Sentinel (Board Program) engine — the Auditor files a weekly "state of
// quality" report (waiver-accumulation trends, conventions-violation
// hotspots, budget anomalies). Like Periscope this is a REPORT, not a queue
// item: read-only here, no approve/reject. Mirrors lib/api/periscope.ts's
// shape exactly.
// ---------------------------------------------------------------------------

export interface QualityReportItem {
  id: string;
  area: string;
  observation: string;
  evidence: string;
  suggested_action: string;
}

export interface QualityReport {
  task_id: string;
  title: string;
  completed_at: string | null;
  headline: string;
  items: QualityReportItem[];
  overall_assessment: string;
}

export const sentinelApi = {
  listReports: async (): Promise<QualityReport[]> => {
    const { data } = await api.get<QualityReport[]>("/sentinel/reports");
    return data;
  },
};
