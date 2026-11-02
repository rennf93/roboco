import axios from "axios";
import api from "./client";
import type { ConventionFinding } from "./conventions";
import type { TaskMetricsReviewerChainEntry } from "./dashboard";
import type { AttestationCiVerdict, TaskFinding } from "./tasks";

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

// One member task of a release proposal, for the verification rollup.
export interface ReleaseMemberTaskId {
  task_id: string;
  pr_number: number | null;
}

export interface ReleaseProposal {
  task_id: string;
  title: string;
  status: string;
  required_changes?: string | null;
  execute_status?: string | null;
  execute_detail?: string | null;
  execute_in_flight?: boolean;
  // The release's member task ids, for the verification rollup — always
  // present (an empty list means this release genuinely has no member
  // tasks, see RELEASE_NO_MEMBER_TASKS_MESSAGE in @/lib/api/verification).
  member_task_ids: ReleaseMemberTaskId[];
  report: ReleaseReport;
}

// One member task's verification receipt for the rollup, in the wire shapes
// the per-task endpoints already use (backend ReleaseMemberReceiptModel in
// roboco/api/schemas/release.py): the task's acceptance criteria and
// qa_notes, its findings ledger (newest round first), its conventions
// findings, and its review-layer spawn chain.
export interface ReleaseMemberReceipt {
  task_id: string;
  title: string;
  acceptance_criteria: string[];
  qa_notes: string | null;
  findings: TaskFinding[];
  conventions_findings: ConventionFinding[];
  reviewer_chain: TaskMetricsReviewerChainEntry[];
}

// GET /release/proposal/verification: every member task's receipt in one
// response, in the proposal's member order.
export interface ReleaseVerification {
  proposal_task_id: string;
  members: ReleaseMemberReceipt[];
}

// One member task's PR CI verdict (backend ReleaseMemberCiVerdictModel).
export interface ReleaseMemberCiVerdict {
  task_id: string;
  ci: AttestationCiVerdict;
}

// GET /release/proposal/ci-verdicts: every member PR's CI verdict, read from
// the forge server-side a few at a time.
export interface ReleaseCiVerdicts {
  proposal_task_id: string;
  verdicts: ReleaseMemberCiVerdict[];
}

export interface ReleaseExecuteResult {
  status: string;
  version: string;
  files_changed: string[];
  commit_sha?: string | null;
  release_url?: string | null;
  detail: string;
}

export interface ReleaseCertificateSeverityCounts {
  blocker: number;
  major: number;
  minor: number;
  nit: number;
}

export interface ReleaseCertificateFindingsSummary {
  open: ReleaseCertificateSeverityCounts;
  closed: ReleaseCertificateSeverityCounts;
  waived: ReleaseCertificateSeverityCounts;
}

export interface ReleaseCertificateTaskState {
  task_id: string;
  title: string;
  status: string;
  criteria_total: number;
  criteria_verified: number;
  qa_passed: boolean | null;
}

export interface ReleaseCertificate {
  version: string;
  generated_at: string;
  ci_verdict: string;
  conventions_clean: boolean;
  ceo_approved_at: string | null;
  changelog_excerpt: string;
  task_states: ReleaseCertificateTaskState[];
  findings_summary: ReleaseCertificateFindingsSummary;
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
  // 404 means the version hasn't published yet — a normal not-ready state,
  // returned as null (mirrors getProposal).
  getCertificate: async (
    version: string,
  ): Promise<ReleaseCertificate | null> => {
    try {
      const { data } = await api.get<ReleaseCertificate>(
        `/releases/${version}/certificate`,
      );
      return data;
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return null;
      }
      throw err;
    }
  },
  getVerification: async (): Promise<ReleaseVerification> => {
    const { data } = await api.get<ReleaseVerification>(
      "/release/proposal/verification",
    );
    return data;
  },
  getCiVerdicts: async (): Promise<ReleaseCiVerdicts> => {
    const { data } = await api.get<ReleaseCiVerdicts>(
      "/release/proposal/ci-verdicts",
    );
    return data;
  },
  approve: async (): Promise<ReleaseExecuteResult> => {
    const { data } = await api.post<ReleaseExecuteResult>(
      "/release/proposal/approve",
    );
    return data;
  },
  reject: async (requiredChanges: string): Promise<ReleaseProposal> => {
    const { data } = await api.post<ReleaseProposal>(
      "/release/proposal/reject",
      {
        required_changes: requiredChanges,
      },
    );
    return data;
  },
};
