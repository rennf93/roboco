"use client";

import { MessageCircle, ClipboardCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { DraftProposal } from "@/lib/api/prompter";

interface DraftProposalCardProps {
  draft: DraftProposal;
  onKeepChatting: () => void;
  onOpenReview: () => void;
}

const PRIORITY_LABELS: Record<number, string> = {
  0: "Low",
  1: "Medium",
  2: "High",
  3: "Urgent",
};

export function DraftProposalCard({
  draft,
  onKeepChatting,
  onOpenReview,
}: DraftProposalCardProps) {
  const priorityLabel = PRIORITY_LABELS[draft.priority ?? 2] ?? "High";

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm font-semibold leading-tight">
            {draft.title}
          </CardTitle>
          <div className="flex flex-wrap gap-1 shrink-0">
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
          </div>
        </div>
      </CardHeader>

      <CardContent className="pb-3 space-y-3">
        {/* Description excerpt */}
        {draft.description && (
          <p className="text-sm text-muted-foreground line-clamp-3">
            {draft.description}
          </p>
        )}

        {/* Acceptance criteria */}
        {draft.acceptance_criteria.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">
              Acceptance criteria ({draft.acceptance_criteria.length})
            </p>
            <ul className="space-y-1">
              {draft.acceptance_criteria.slice(0, 4).map((criterion, i) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  <span className="mt-0.5 h-3 w-3 shrink-0 rounded-full border border-primary/50 flex items-center justify-center">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary/50" />
                  </span>
                  <span className="text-foreground line-clamp-2">{criterion}</span>
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

      <CardFooter className="gap-2 pt-0">
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          onClick={onKeepChatting}
        >
          <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
          Keep Chatting
        </Button>
        <Button
          size="sm"
          className="flex-1"
          onClick={onOpenReview}
        >
          <ClipboardCheck className="mr-1.5 h-3.5 w-3.5" />
          Review &amp; Confirm
        </Button>
      </CardFooter>
    </Card>
  );
}
