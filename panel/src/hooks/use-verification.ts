// Data-fetching hooks for the verification receipt (task-detail Verification
// tab; release-proposal rollup). Every hook rides an existing query
// (`useTask` / `useTaskFindings` / `conventionsApi.findings`) — no new query
// path is introduced anywhere in this file.
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { conventionsApi, type ConventionFinding } from "@/lib/api/conventions";
import {
  buildReviewerChain,
  groupFindingsByRound,
  parseAcVerificationStamps,
  PR_CI_VERDICT_UNAVAILABLE,
  type AcVerificationStamp,
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

// Reviewer/role/round chain, derived from the same revision-findings ledger
// the Findings tab already fetches (`useTaskFindings`) — see
// `buildReviewerChain` for why `model` always reads null.
export function useReviewerChain(taskId: string): {
  data: ReviewerChainEntry[] | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useTaskFindings(taskId);
  const chain = useMemo(
    () => (data ? buildReviewerChain(data.findings) : undefined),
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

// The PR CI verdict bound to the task's PR head commit. Always reads
// unavailable — see `PR_CI_VERDICT_UNAVAILABLE` for why (no backend route
// exists to fetch it). Shaped as a hook, not a bare constant, so both UI
// leaves consume one uniform {data, isLoading} shape across every
// verification data source, and the day a real endpoint ships only this
// hook's body changes.
export function usePrCiVerdict(_taskId: string): {
  data: PrCiVerdict;
  isLoading: false;
} {
  return { data: PR_CI_VERDICT_UNAVAILABLE, isLoading: false };
}
