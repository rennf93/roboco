// Data-fetching hooks for the verification receipt. The task-detail
// Verification tab reads one task through the per-task endpoints (`useTask` /
// `useTaskFindings` / `conventionsApi.findings` / `tasksApi.getAttestation`);
// the release-proposal rollup reads every member task through two bulk
// requests (`useReleaseVerification` / `useReleaseCiVerdicts`), never per
// member.
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { conventionsApi, type ConventionFinding } from "@/lib/api/conventions";
import { dashboardApi } from "@/lib/api/dashboard";
import { releaseApi } from "@/lib/api/release";
import { tasksApi } from "@/lib/api/tasks";
import {
  buildReviewerChain,
  ciVerdictsByTask,
  groupFindingsByRound,
  memberVerificationFromReceipt,
  parseAcVerificationStamps,
  prCiVerdictFromAttestation,
  PR_CI_VERDICT_NOT_AVAILABLE,
  PR_CI_VERDICT_UNREADABLE,
  type AcVerificationStamp,
  type MemberVerification,
  type PrCiVerdict,
  type ReviewerChainEntry,
  type ReviewRoundFindings,
} from "@/lib/api/verification";
import { useTask, useTaskFindings } from "./use-tasks";

// Findings grouped by review round (severity + open/addressed/verified/waived
// status live on each finding already) — reuses the exact same query
// `useTaskFindings` already runs for the panel's Findings tab, just
// re-shaped for the two verification-receipt consumers.
export function useFindingsByRound(taskId: string): {
  data: ReviewRoundFindings[] | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useTaskFindings(taskId);
  const rounds = useMemo(
    () => (data ? groupFindingsByRound(data.findings) : undefined),
    [data],
  );
  return { data: rounds, isLoading };
}

// Per-AC verification stamps (QA's `criteria_verified` evidence lines parsed
// out of `qa_notes`). Derives from the same GET /tasks/{id} the rest of
// task-detail already fetches, so this rides `useTask`'s own cache entry —
// no dedicated endpoint or extra request.
export function useAcVerificationStamps(taskId: string): {
  data: AcVerificationStamp[] | undefined;
  isLoading: boolean;
} {
  const { data: task, isLoading } = useTask(taskId);
  const data = useMemo(
    () =>
      task
        ? parseAcVerificationStamps(task.acceptance_criteria, task.qa_notes)
        : undefined,
    [task],
  );
  return { data, isLoading };
}

// Reviewer/role/round chain — sourced from GET /dashboard/metrics/task/
// {task_id}'s `reviewer_chain` (real role/agent_slug/model per round), not
// the revision-findings ledger the Findings tab fetches — see
// `buildReviewerChain` for why the ledger derivation is gone.
export function useReviewerChain(taskId: string): {
  data: ReviewerChainEntry[] | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useQuery({
    queryKey: ["task-metrics", taskId],
    queryFn: () => dashboardApi.getTaskMetrics(taskId),
  });
  const chain = useMemo(
    () => (data ? buildReviewerChain(data.reviewer_chain) : undefined),
    [data],
  );
  return { data: chain, isLoading };
}

// Conventions-validator findings for this task's diff. Reuses the SAME
// project-scoped query key (["conventions-findings", projectId]) the
// project's Conventions tab already runs (`conventionsApi.findings`),
// filtered down to this task client-side — not a new query path. Findings
// are recorded at gate time (i_am_done / pr_pass) tagged with `task_id`, but
// the endpoint caps to the project's most recent rows, so an old task's
// findings can fall off that cap — a known ceiling of reusing a
// project-scoped feed for a task-scoped view.
export function useTaskConventionFindings(taskId: string): {
  data: ConventionFinding[];
  isLoading: boolean;
} {
  const { data: task, isLoading: isTaskLoading } = useTask(taskId);
  const projectId = task?.project_id ?? null;
  const { data, isLoading: isFindingsLoading } = useQuery({
    queryKey: ["conventions-findings", projectId],
    queryFn: () => conventionsApi.findings(projectId as string),
    enabled: !!projectId,
  });
  const filtered = useMemo(
    () => (data ?? []).filter((f) => f.task_id === taskId),
    [data, taskId],
  );
  return { data: filtered, isLoading: isTaskLoading || isFindingsLoading };
}

// The PR CI verdict bound to the task's PR head commit, off
// GET /tasks/{id}/attestation's `ci`. The attestation endpoint resolves the
// task's project, PR and git service server-side, so the panel needs no
// dedicated CI route; a failed load reads as unavailable, never as a pass.
export function usePrCiVerdict(taskId: string): {
  data: PrCiVerdict;
  isLoading: boolean;
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["task-attestation", taskId],
    queryFn: () => tasksApi.getAttestation(taskId),
  });
  const verdict = useMemo(() => {
    if (isError) return PR_CI_VERDICT_UNREADABLE;
    return data
      ? prCiVerdictFromAttestation(data.ci)
      : PR_CI_VERDICT_NOT_AVAILABLE;
  }, [data, isError]);
  return { data: verdict, isLoading };
}

// The rollup's query keys live under ["release", ...], never under ["tasks"]:
// the panel invalidates the ["tasks"] prefix on every task mutation, and a
// rollup keyed there would refetch with each one.
export const releaseVerificationKeys = {
  receipts: (proposalTaskId: string) =>
    ["release", "verification", proposalTaskId] as const,
  ciVerdicts: (proposalTaskId: string) =>
    ["release", "ci-verdicts", proposalTaskId] as const,
};

// Every member task's receipt for the open release proposal from ONE request
// (GET /release/proposal/verification), shaped for the receipt pieces. The
// rollup used to fire four per-task requests for each member, about 300 for
// the 74-member release that exhausted the orchestrator's DB pool on
// 2026-10-28.
export function useReleaseVerification(
  proposalTaskId: string,
  enabled: boolean,
): {
  data: MemberVerification[] | undefined;
  isLoading: boolean;
  isError: boolean;
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: releaseVerificationKeys.receipts(proposalTaskId),
    queryFn: () => releaseApi.getVerification(),
    enabled,
  });
  const members = useMemo(
    () => data?.members.map(memberVerificationFromReceipt),
    [data],
  );
  return { data: members, isLoading, isError };
}

// Every member PR's CI verdict for the open release proposal from ONE request
// (GET /release/proposal/ci-verdicts), keyed by member task id. Separate from
// the receipts so the forge round-trips never hold the receipts back.
export function useReleaseCiVerdicts(
  proposalTaskId: string,
  enabled: boolean,
): {
  data: Record<string, PrCiVerdict> | undefined;
  isLoading: boolean;
  isError: boolean;
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: releaseVerificationKeys.ciVerdicts(proposalTaskId),
    queryFn: () => releaseApi.getCiVerdicts(),
    enabled,
  });
  const verdicts = useMemo(
    () => (data ? ciVerdictsByTask(data.verdicts) : undefined),
    [data],
  );
  return { data: verdicts, isLoading, isError };
}
