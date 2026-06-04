"use client";

import { CheckCircle2, ArrowLeft, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { TaskCreate } from "@/types";

const PRIORITY_LABELS: Record<number, string> = {
  0: "P0 — Urgent",
  1: "P1 — High",
  2: "P2 — Medium",
  3: "P3 — Low",
};

const PRIORITY_COLORS: Record<number, string> = {
  0: "destructive",
  1: "warning",
  2: "secondary",
  3: "outline",
};

interface LaunchSummaryProps {
  draft: TaskCreate;
  onBack: () => void;
  onConfirm: () => void;
  isLaunching?: boolean;
}

export function LaunchSummary({
  draft,
  onBack,
  onConfirm,
  isLaunching = false,
}: LaunchSummaryProps) {
  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="h-5 w-5 text-green-500" />
        <h3 className="font-semibold">Ready to launch</h3>
      </div>

      {/* Summary card */}
      <div className="rounded-lg border bg-card p-4 space-y-4 text-sm">
        {/* Title */}
        <div>
          <div className="text-xs text-muted-foreground mb-1">Title</div>
          <div className="font-medium">{draft.title}</div>
        </div>

        {/* Meta row */}
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary" className="capitalize">
            {draft.team}
          </Badge>
          <Badge
            variant={
              (PRIORITY_COLORS[draft.priority ?? 2] as
                | "secondary"
                | "outline"
                | "destructive") ?? "secondary"
            }
          >
            {PRIORITY_LABELS[draft.priority ?? 2]}
          </Badge>
          {draft.task_type && (
            <Badge variant="outline" className="capitalize">
              {draft.task_type}
            </Badge>
          )}
          {draft.estimated_complexity && (
            <Badge variant="outline" className="capitalize">
              {draft.estimated_complexity}
            </Badge>
          )}
        </div>

        {/* Description */}
        <div>
          <div className="text-xs text-muted-foreground mb-1">Description</div>
          <div className="text-sm text-foreground whitespace-pre-wrap line-clamp-4">
            {draft.description}
          </div>
        </div>

        {/* Acceptance Criteria */}
        {draft.acceptance_criteria.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-2">
              Acceptance Criteria ({draft.acceptance_criteria.length})
            </div>
            <ul className="space-y-1">
              {draft.acceptance_criteria.map((c, i) => (
                <li key={i} className={cn("flex items-start gap-2")}>
                  <CheckCircle2 className="h-3.5 w-3.5 mt-0.5 shrink-0 text-green-500" />
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-3 mt-auto">
        <Button
          variant="outline"
          onClick={onBack}
          disabled={isLaunching}
          className="flex-1"
        >
          <ArrowLeft className="h-4 w-4 mr-1" />
          Back to editing
        </Button>
        <Button
          onClick={onConfirm}
          disabled={isLaunching}
          className="flex-1"
        >
          <Rocket className="h-4 w-4 mr-1" />
          {isLaunching ? "Launching…" : "Launch"}
        </Button>
      </div>
    </div>
  );
}
