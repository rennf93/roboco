"use client";

import { MessageCircle, Users, Rocket, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { HelpTip } from "@/components/ui/help-tip";
import type { DraftProposal } from "@/lib/api/prompter";
import type { StartRoute } from "@/hooks/use-prompter";

interface DraftProposalCardProps {
  draft: DraftProposal;
  onKeepChatting: () => void;
  onStart: (route: StartRoute) => void;
  /** A launch is in flight — disable the actions so a double-click can't dupe. */
  isLaunching?: boolean;
}

// 0 is the highest priority, 3 the lowest — matches the backend contract.
const PRIORITY_LABELS: Record<number, string> = {
  0: "Highest",
  1: "High",
  2: "Medium",
  3: "Low",
};

const cellLabel = (team: string) =>
  team === "ux_ui" ? "UX/UI" : team.charAt(0).toUpperCase() + team.slice(1);

/** Render the draft as plain markdown text for the copy button, so the CEO can
 *  stash the full spec elsewhere (a safety net until refresh-durability lands). */
function draftToText(draft: DraftProposal): string {
  const lines: string[] = [`# ${draft.title}`, ""];
  if (draft.objective) lines.push("## Objective", draft.objective, "");
  if (draft.what_this_builds?.length) {
    lines.push(
      "## What This Builds",
      ...draft.what_this_builds.map((b) => `- ${b}`),
      "",
    );
  }
  if (Array.isArray(draft.the_work) && draft.the_work.length) {
    lines.push("## The Work");
    for (const cell of draft.the_work) {
      lines.push(`### ${cellLabel(cell.team)}`, cell.summary);
      if (cell.items?.length) lines.push(...cell.items.map((i) => `- ${i}`));
      lines.push("");
    }
  }
  if (draft.notes?.length) {
    lines.push("## Notes", ...draft.notes.map((n) => `- ${n}`), "");
  }
  if (draft.acceptance_criteria.length) {
    lines.push(
      "## Success Criteria",
      ...draft.acceptance_criteria.map((c) => `- ${c}`),
      "",
    );
  }
  return lines.join("\n").trim();
}

export function DraftProposalCard({
  draft,
  onKeepChatting,
  onStart,
  isLaunching = false,
}: DraftProposalCardProps) {
  const priorityLabel = PRIORITY_LABELS[draft.priority ?? 2] ?? "Medium";
  const cells = Array.isArray(draft.the_work) ? draft.the_work : [];
  // Distinct cells only: the_work has one entry per work item, so a cell with
  // several items would otherwise show its badge repeated (Backend Backend …).
  const distinctTeams = Array.from(new Set(cells.map((c) => c.team)));

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm font-semibold leading-tight">
            {draft.title}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-1 shrink-0">
            {draft.team && (
              <Badge variant="secondary" className="text-xs">
                {draft.team}
              </Badge>
            )}
            <Badge variant="outline" className="text-xs">
              {priorityLabel}
            </Badge>
            {draft.task_type && (
              <Badge variant="outline" className="text-xs">
                {draft.task_type}
              </Badge>
            )}
            <CopyButton value={draftToText(draft)} className="ml-0.5" />
          </div>
        </div>
      </CardHeader>

      <CardContent className="pb-3 space-y-3">
        {/* Objective (falls back to a description excerpt) */}
        {(draft.objective || draft.description) && (
          <p className="text-sm text-muted-foreground line-clamp-3">
            {draft.objective || draft.description}
          </p>
        )}

        {/* The Work — participating cells (distinct) */}
        {distinctTeams.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              {distinctTeams.length > 1 ? (
                <HelpTip label="This feature spans multiple delivery cells of one product — the Board reviews it before delivery starts.">
                  <span>Board-led across</span>
                </HelpTip>
              ) : (
                "Cell:"
              )}
            </span>
            {distinctTeams.map((team) => (
              <Badge key={team} variant="outline" className="text-xs">
                {cellLabel(team)}
              </Badge>
            ))}
          </div>
        )}

        {/* Acceptance criteria */}
        {draft.acceptance_criteria.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">
              Success criteria ({draft.acceptance_criteria.length})
            </p>
            <ul className="space-y-1">
              {draft.acceptance_criteria.slice(0, 4).map((criterion, i) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  <span className="mt-0.5 h-3 w-3 shrink-0 rounded-full border border-primary/50 flex items-center justify-center">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary/50" />
                  </span>
                  <span className="text-foreground line-clamp-2">
                    {criterion}
                  </span>
                </li>
              ))}
              {draft.acceptance_criteria.length > 4 && (
                <li className="text-xs text-muted-foreground pl-5">
                  +{draft.acceptance_criteria.length - 4} more…
                </li>
              )}
            </ul>
          </div>
        )}
      </CardContent>

      <CardFooter className="flex-wrap gap-2 pt-0">
        <Button
          variant="outline"
          size="sm"
          onClick={onKeepChatting}
          disabled={isLaunching}
        >
          <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
          Keep chatting
        </Button>
        {/* Board review & Start → PENDING, assigned to PO + HoM for review */}
        <HelpTip label="Routes this task through the Product Owner and Head of Marketing for review before any work starts.">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onStart("board")}
            disabled={isLaunching}
          >
            {isLaunching ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Users className="mr-1.5 h-3.5 w-3.5" />
            )}
            Board review &amp; Start
          </Button>
        </HelpTip>
        {/* Approve & Start → PENDING, straight to Main PM (skip the board) */}
        <HelpTip label="Skips board review — dispatches this task immediately.">
          <Button
            size="sm"
            onClick={() => onStart("main_pm")}
            disabled={isLaunching}
          >
            {isLaunching ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Rocket className="mr-1.5 h-3.5 w-3.5" />
            )}
            Approve &amp; Start
          </Button>
        </HelpTip>
      </CardFooter>
    </Card>
  );
}
