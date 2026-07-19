"use client";

import { TgSheet } from "@/components/tg/motion";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import { useTaskFindings } from "@/hooks/use-tasks";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { isTgDemoMode } from "@/lib/telegram/demo";
import type { Task } from "@/types";
import type { TaskFinding } from "@/lib/api/tasks";
import { CheckCircle2, ExternalLink } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";

const FINDINGS_SHOWN = 5;

const SEVERITY_DOT: Record<TaskFinding["severity"], string> = {
  blocker: "bg-rose-400",
  major: "bg-amber-400",
  minor: "bg-sky-400",
  nit: "bg-muted-foreground/60",
};

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
          <p className="tg-display truncate text-xs">
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

/**
 * Read-only task detail for the Board tab's tap-through: status, meta,
 * description, acceptance criteria, the open revision findings, and the PR
 * link. Mutations stay on the desktop panel — the cockpit is a
 * glance-and-decide surface, and the decide verbs already live in
 * Approvals.
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
              <TaskStatusBadge status={task.status} />
              {(task.revision_count ?? 0) > 0 && (
                <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium tabular-nums text-amber-300">
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
            <section>
              <h4 className="tg-display mb-1.5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                Acceptance criteria
              </h4>
              <ul className="space-y-1.5">
                {task.acceptance_criteria.map((criterion, i) => (
                  <li key={i} className="flex gap-2 text-sm leading-snug">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/50" />
                    <span>{criterion}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {openFindings.length > 0 && (
            <section>
              <h4 className="tg-display mb-1 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
                Open findings · {openFindings.length}
              </h4>
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
            </section>
          )}

          {task.pr_url && (
            <a
              href={task.pr_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-center gap-2 rounded-xl bg-muted py-2.5 text-sm font-medium transition-transform active:scale-[0.98]"
            >
              <ExternalLink className="h-4 w-4" />
              Open PR{task.pr_number !== null && ` #${task.pr_number}`}
            </a>
          )}
        </div>
      )}
    </TgSheet>
  );
}
