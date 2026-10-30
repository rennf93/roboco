"use client";

// The task-detail Verification tab: the approver's trust story assembled in
// one place instead of scattered across Notes, Findings, and Commits. Every
// data read here goes through the shared hooks in @/hooks/use-verification —
// this file only composes the presentational receipt pieces.
import { Task } from "@/types";
import {
  useAcVerificationStamps,
  useFindingsByRound,
  usePrCiVerdict,
  useReviewerChain,
  useTaskConventionFindings,
} from "@/hooks/use-verification";
import {
  AcVerificationList,
  CiVerdictBadge,
  ConventionsFindingsList,
  FindingsByRoundList,
  ReviewerChainList,
} from "@/components/verification/receipt";
import { HelpTip } from "@/components/ui/help-tip";

interface TabVerificationProps {
  task: Task;
}

export function TabVerification({ task }: TabVerificationProps) {
  const ac = useAcVerificationStamps(task.id);
  const findings = useFindingsByRound(task.id);
  const ci = usePrCiVerdict(task.id);
  const conventions = useTaskConventionFindings(task.id);
  const reviewers = useReviewerChain(task.id);

  return (
    <div className="space-y-5">
      <section>
        <HelpTip label="Matched from pass_review's [AC] evidence stamps in qa_notes, by exact criterion text">
          <h3 className="w-fit text-sm font-medium">Acceptance criteria</h3>
        </HelpTip>
        <div className="mt-2">
          <AcVerificationList data={ac.data} isLoading={ac.isLoading} />
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">PR CI verdict</h3>
          <CiVerdictBadge data={ci.data} isLoading={ci.isLoading} />
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium">Findings by review round</h3>
        <div className="mt-2">
          <FindingsByRoundList data={findings.data} isLoading={findings.isLoading} />
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium">Conventions findings</h3>
        <div className="mt-2">
          <ConventionsFindingsList
            data={conventions.data}
            isLoading={conventions.isLoading}
          />
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium">Reviewer chain</h3>
        <div className="mt-2">
          <ReviewerChainList data={reviewers.data} isLoading={reviewers.isLoading} />
        </div>
      </section>
    </div>
  );
}
