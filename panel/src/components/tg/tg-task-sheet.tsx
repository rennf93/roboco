"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { TgSheet } from "@/components/tg/motion";
import { TG_PRESS, TgSection } from "@/components/tg/ui";
import { taskKeys, useTaskFindings } from "@/hooks/use-tasks";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { haptics } from "@/lib/telegram/webapp";
import { TaskStatus, type Task } from "@/types";
import { tasksApi, type TaskFinding } from "@/lib/api/tasks";
import { Textarea } from "@/components/ui/textarea";
import { ExternalLink, Loader2 } from "lucide-react";
import { CheckCircle } from "@phosphor-icons/react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const FINDINGS_SHOWN = 5;

const SEVERITY_DOT: Record<TaskFinding["severity"], string> = {
  blocker: "bg-rose-400",
  major: "bg-amber-400",
  minor: "bg-sky-400",
  nit: "bg-muted-foreground/60",
};

/** The cockpit's 5-tone status language (needs-you rose, review violet,
 * active sky, done emerald, queued/idle muted) — a deliberately smaller
 * palette than the desktop's per-status badge, so a status reads as "what
 * kind of wait is this" at a glance. */
const STATUS_TONE: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "muted",
  [TaskStatus.PENDING]: "muted",
  [TaskStatus.CLAIMED]: "sky",
  [TaskStatus.IN_PROGRESS]: "sky",
  [TaskStatus.BLOCKED]: "rose",
  [TaskStatus.PAUSED]: "muted",
  [TaskStatus.VERIFYING]: "sky",
  [TaskStatus.NEEDS_REVISION]: "rose",
  [TaskStatus.AWAITING_QA]: "violet",
  [TaskStatus.AWAITING_DOCUMENTATION]: "violet",
  [TaskStatus.AWAITING_PR_REVIEW]: "violet",
  [TaskStatus.AWAITING_PM_REVIEW]: "violet",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "violet",
  [TaskStatus.COMPLETED]: "emerald",
  [TaskStatus.CANCELLED]: "muted",
};

const TONE_CLASSES: Record<string, string> = {
  emerald: "bg-emerald-500/15 text-emerald-300",
  rose: "bg-rose-500/15 text-rose-300",
  sky: "bg-sky-500/15 text-sky-300",
  violet: "bg-violet-500/15 text-violet-300",
  muted: "bg-muted/70 text-muted-foreground",
};

function sentenceCase(s: string): string {
  return s.length === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

function StatusPill({ status }: { status: TaskStatus }) {
  const tone = STATUS_TONE[status] ?? "muted";
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-1 text-xs font-semibold",
        TONE_CLASSES[tone],
      )}
    >
      {sentenceCase(status.replace(/_/g, " "))}
    </span>
  );
}

function FindingRow({ finding }: { finding: TaskFinding }) {
  return (
    <li className="flex gap-2 py-1.5">
      <span
        className={cn(
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          SEVERITY_DOT[finding.severity],
        )}
      />
      <div className="min-w-0">
        {finding.file && (
          <p className="truncate text-xs font-medium">
            {finding.file}
            {finding.line !== null && `:${finding.line}`}
          </p>
        )}
        <p className="line-clamp-2 text-xs text-muted-foreground">
          {finding.fix ?? finding.expected}
        </p>
      </div>
    </li>
  );
}

const MIN_REJECT = 10;

/**
 * The CEO verbs a phone actually needs, right where "Needs you" points:
 * approve / request-changes on a task awaiting CEO approval, and unblock on
 * a blocked one. Everything else stays a desktop concern.
 */
function CeoActions({ task, onActed }: { task: Task; onActed: () => void }) {
  const demo = isTgDemoMode();
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const done = (verb: string) => {
    haptics.success();
    toast.success(verb);
    void queryClient.invalidateQueries({ queryKey: taskKeys.all });
    onActed();
  };
  const failed = (err: unknown) => {
    haptics.error();
    toast.error(getErrorMessage(err));
  };

  const approve = useMutation({
    mutationFn: () => tasksApi.ceoApprove(task.id),
    onSuccess: () => done("Approved"),
    onError: failed,
  });
  const reject = useMutation({
    mutationFn: () => tasksApi.ceoReject(task.id, reason.trim()),
    onSuccess: () => done("Sent back for revision"),
    onError: failed,
  });
  const unblock = useMutation({
    mutationFn: () => tasksApi.unblock(task.id),
    onSuccess: () => done("Unblocked"),
    onError: failed,
  });
  const busy = approve.isPending || reject.isPending || unblock.isPending;

  if (task.status === TaskStatus.BLOCKED) {
    return (
      <button
        type="button"
        disabled={demo || busy}
        onClick={() => unblock.mutate()}
        className={cn(
          "flex w-full items-center justify-center gap-2 rounded-full bg-primary py-3 text-[15px] font-semibold text-primary-foreground disabled:opacity-40",
          TG_PRESS,
        )}
      >
        {unblock.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        Unblock
      </button>
    );
  }

  if (task.status !== TaskStatus.AWAITING_CEO_APPROVAL) return null;

  return (
    <div className="space-y-2">
      <button
        type="button"
        disabled={demo || busy}
        onClick={() => approve.mutate()}
        className={cn(
          "flex w-full items-center justify-center gap-2 rounded-full bg-primary py-3 text-[15px] font-semibold text-primary-foreground disabled:opacity-40",
          TG_PRESS,
        )}
      >
        {approve.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        Approve
      </button>
      {rejecting ? (
        <div className="space-y-2">
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={`What needs to change? (at least ${MIN_REJECT} characters)`}
            className="min-h-[80px] resize-none rounded-2xl border-0 bg-muted/50 shadow-none focus-visible:ring-0"
            disabled={demo || busy}
          />
          <button
            type="button"
            disabled={demo || busy || reason.trim().length < MIN_REJECT}
            onClick={() => reject.mutate()}
            className={cn(
              "flex w-full items-center justify-center gap-2 rounded-full bg-rose-500/15 py-3 text-[15px] font-semibold text-rose-300 disabled:opacity-40",
              TG_PRESS,
            )}
          >
            {reject.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Send back for revision
          </button>
        </div>
      ) : (
        <button
          type="button"
          disabled={demo || busy}
          onClick={() => setRejecting(true)}
          className={cn(
            "w-full rounded-full bg-muted/60 py-3 text-[15px] font-medium text-muted-foreground disabled:opacity-40",
            TG_PRESS,
          )}
        >
          Request changes
        </button>
      )}
    </div>
  );
}

/**
 * Task detail for the Board tab's tap-through: status, meta, description,
 * acceptance criteria, the open revision findings, the PR link — and the
 * CEO's own decide verbs (approve / request changes / unblock) when the
 * task is waiting on exactly those.
 */
export function TgTaskSheet({
  task,
  onClose,
}: {
  task: Task | null;
  onClose: () => void;
}) {
  // Demo fixtures have no backend — disable the ledger fetch entirely there.
  const findingsQuery = useTaskFindings(task && !isTgDemoMode() ? task.id : "");
  const openFindings = (findingsQuery.data?.findings ?? []).filter(
    (f) => f.status === "open",
  );

  return (
    <TgSheet open={task !== null} onClose={onClose} title="Task">
      {task && (
        <div className="space-y-4 pb-1">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <StatusPill status={task.status} />
              {(task.revision_count ?? 0) > 0 && (
                <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium tabular-nums text-primary">
                  bounced ×{task.revision_count}
                </span>
              )}
            </div>
            <h3 className="text-base font-semibold leading-snug">
              {task.title}
            </h3>
            <p className="text-[11px] text-muted-foreground">
              {[
                task.team,
                getAgentDisplayName(task.assigned_to),
                task.updated_at &&
                  `${formatDistanceToNow(new Date(task.updated_at))} ago`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>

          {task.description && (
            <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
              {task.description}
            </p>
          )}

          {task.acceptance_criteria.length > 0 && (
            <TgSection title="Acceptance criteria">
              <ul className="space-y-1.5">
                {task.acceptance_criteria.map((criterion, i) => (
                  <li key={i} className="flex gap-2 text-sm leading-relaxed">
                    <CheckCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/50" />
                    <span>{criterion}</span>
                  </li>
                ))}
              </ul>
            </TgSection>
          )}

          {openFindings.length > 0 && (
            <TgSection title={`Open findings · ${openFindings.length}`}>
              <ul className="divide-y">
                {openFindings.slice(0, FINDINGS_SHOWN).map((f) => (
                  <FindingRow key={f.id} finding={f} />
                ))}
              </ul>
              {openFindings.length > FINDINGS_SHOWN && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  +{openFindings.length - FINDINGS_SHOWN} more on the desktop
                  panel
                </p>
              )}
            </TgSection>
          )}

          {task.pr_url && (
            <a
              href={task.pr_url}
              target="_blank"
              rel="noreferrer"
              className={cn(
                "flex items-center justify-center gap-2 rounded-xl bg-muted py-2.5 text-sm font-medium",
                TG_PRESS,
              )}
            >
              <ExternalLink className="h-4 w-4" />
              Open PR{task.pr_number !== null && ` #${task.pr_number}`}
            </a>
          )}

          <CeoActions task={task} onActed={onClose} />
        </div>
      )}
    </TgSheet>
  );
}
