import axios from "axios";
import api from "./client";

// ---------------------------------------------------------------------------
// Release manager — the CEO approves or rejects a held release proposal that
// the release-manager engine prepared (deterministic readiness sweep). Nothing
// publishes until the CEO approves; the executor is fail-closed on a red gate.
// ---------------------------------------------------------------------------

export interface ReleaseGap {
  category: string;
  detail: string;
}

export interface ReleaseReport {
  proposed_version: string;
  bump_kind: string;
  change_summary: string[];
  drafted_changelog: string;
  version_bump_plan: string[];
  gaps: ReleaseGap[];
  migration_notes: string[];
  gate_state: string;
}

export interface ReleaseProposal {
  task_id: string;
  title: string;
  status: string;
  required_changes?: string | null;
  report: ReleaseReport;
}

export interface ReleaseExecuteResult {
  status: string;
  version: string;
  files_changed: string[];
  commit_sha?: string | null;
  release_url?: string | null;
  detail: string;
}

export const releaseApi = {
  // 404 means "no open proposal" — a normal empty state, returned as null.
  getProposal: async (): Promise<ReleaseProposal | null> => {
    try {
      const { data } = await api.get<ReleaseProposal>("/release/proposal");
      return data;
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return null;
      }
      throw err;
    }
  },
  approve: async (): Promise<ReleaseExecuteResult> => {
    const { data } = await api.post<ReleaseExecuteResult>(
      "/release/proposal/approve",
    );
    return data;
  },
  reject: async (requiredChanges: string): Promise<ReleaseProposal> => {
    const { data } = await api.post<ReleaseProposal>("/release/proposal/reject", {
      required_changes: requiredChanges,
    });
    return data;
  },
};
