"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { releaseApi, type ReleaseProposal } from "@/lib/api/release";
import { getErrorMessage } from "@/lib/api/client";
import { haptics } from "@/lib/telegram/webapp";
import { Badge } from "@/components/ui/badge";
import { TgSection } from "@/components/tg/ui";
import { PrimaryAction } from "./primary-action";
import { RejectForm } from "./reject-form";
import { cn } from "@/lib/utils";

const MIN_REJECT_CHARS = 10;

/**
 * Focused release proposal: version/gate at a glance, the drafted
 * changelog, gaps and migration notes, then approve (runs the fail-closed
 * ~40min executor server-side) or reject-with-required-changes.
 */
export function ReleaseDetail({
  proposal,
  onDone,
}: {
  proposal: ReleaseProposal;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const report = proposal.report;
  const inFlight = proposal.execute_in_flight === true;
  const executeFailed = !inFlight && !!proposal.execute_status;

  const approve = useMutation({
    mutationFn: () => releaseApi.approve(),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["release", "proposal"] });
      if (result.status === "published") {
        haptics.success();
        toast.success(`Published v${result.version}`);
        onDone();
      } else if (result.status === "accepted") {
        haptics.success();
        toast.info("Approved — executing in the background (~40 min).");
        onDone();
      } else {
        haptics.error();
        toast.warning(`Release halted (${result.status}): ${result.detail}`);
      }
    },
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (changes: string) => releaseApi.reject(changes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["release", "proposal"] });
      haptics.success();
      toast.success("Rejected — changes requested.");
      onDone();
    },
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge>v{report.proposed_version}</Badge>
        <Badge variant="secondary">{report.bump_kind}</Badge>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-medium",
            report.gate_state === "green"
              ? "bg-emerald-500/15 text-emerald-300"
              : "bg-rose-500/15 text-rose-300",
          )}
        >
          gate: {report.gate_state}
        </span>
      </div>

      {inFlight && (
        <p className="rounded-xl bg-primary/10 p-2 text-xs text-primary">
          Execute is running in the background (~40 min). This card updates
          itself.
        </p>
      )}
      {executeFailed && (
        <p className="rounded-xl bg-rose-500/10 p-2 text-xs text-rose-300">
          Last execute failed ({proposal.execute_status})
          {proposal.execute_detail ? `: ${proposal.execute_detail}` : ""}
        </p>
      )}

      {report.gaps.length > 0 && (
        <TgSection title="Gaps">
          <div className="space-y-1">
            {report.gaps.map((gap, i) => (
              <p key={i} className="text-xs text-muted-foreground">
                [{gap.category}] {gap.detail}
              </p>
            ))}
          </div>
        </TgSection>
      )}

      <TgSection title="Changelog draft">
        <pre className="whitespace-pre-wrap rounded-xl bg-muted p-2 text-xs leading-relaxed">
          {report.drafted_changelog}
        </pre>
      </TgSection>

      {report.migration_notes.length > 0 && (
        <TgSection title="Migrations">
          <div className="space-y-1">
            {report.migration_notes.map((note, i) => (
              <p key={i} className="text-xs">
                {note}
              </p>
            ))}
          </div>
        </TgSection>
      )}

      <PrimaryAction
        text={executeFailed ? "Retry approve & publish" : "Approve & publish"}
        disabled={inFlight}
        loading={approve.isPending}
        onClick={() => approve.mutate()}
      />
      <RejectForm
        minChars={MIN_REJECT_CHARS}
        placeholder="What must change before this ships?"
        pending={reject.isPending}
        onSubmit={(reason) => reject.mutate(reason)}
      />
    </div>
  );
}
