"use client";

import { useQuery } from "@tanstack/react-query";
import { coronerApi } from "@/lib/api";
import type { Postmortem } from "@/lib/api/coroner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { HelpTip } from "@/components/ui/help-tip";
import { Stethoscope } from "lucide-react";

const INCIDENT_KIND_LABELS: Record<string, string> = {
  bounced: "bounced 3+ times",
  cancelled: "cancelled after work started",
  budget: "budget-blocked",
};

function PostmortemRow({ postmortem }: { postmortem: Postmortem }) {
  return (
    <div className="rounded-lg border p-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {postmortem.incident_title ?? "Untitled incident"}
        </span>
        {postmortem.incident_kind && (
          <HelpTip label="What triggered this autopsy">
            <Badge variant="outline">
              {INCIDENT_KIND_LABELS[postmortem.incident_kind] ??
                postmortem.incident_kind}
            </Badge>
          </HelpTip>
        )}
        {postmortem.failed_stage && (
          <HelpTip label="The lifecycle stage where it actually broke down">
            <Badge variant="secondary">{postmortem.failed_stage}</Badge>
          </HelpTip>
        )}
        {postmortem.process_change_kind && (
          <HelpTip label="The kind of process change proposed">
            <Badge>{postmortem.process_change_kind}</Badge>
          </HelpTip>
        )}
      </div>
      {postmortem.incident_summary && (
        <p className="text-sm text-muted-foreground">
          {postmortem.incident_summary}
        </p>
      )}
      {postmortem.root_cause && (
        <p className="text-sm">
          <span className="font-semibold">Root cause: </span>
          {postmortem.root_cause}
        </p>
      )}
      {postmortem.process_change_description && (
        <p className="text-sm">
          <span className="font-semibold">Process change: </span>
          {postmortem.process_change_description}
        </p>
      )}
      {postmortem.completed_at && (
        <p className="text-xs text-muted-foreground">
          {new Date(postmortem.completed_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}

// Read-only Postmortems list — a Coroner postmortem completes atomically at
// propose_postmortem time (no per-item approve/reject like Pest Control/
// Roadmap), so unlike those review queues this never hides on empty; it's a
// retrospective report the CEO checks, not an urgent action queue.
export function CoronerPostmortemsCard({ className }: { className?: string }) {
  const {
    data: postmortems,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["coroner", "postmortems"],
    queryFn: () => coronerApi.listPostmortems(),
    refetchInterval: 30000,
  });

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Stethoscope className="h-5 w-5" />
          Postmortems
        </CardTitle>
        <CardDescription>
          The Auditor&apos;s Coroner autopsies — bounced, cancelled, or
          budget-blocked incidents, each with a root cause and one proposed
          process change.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : isError ? (
          <OfflineState
            title="Failed to load Postmortems"
            description="Could not reach the orchestrator API. Check the backend is running."
            onRetry={() => void refetch()}
          />
        ) : !postmortems || postmortems.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No postmortems yet — Coroner autopsies open only when a task
            bounces 3+ times, is cancelled after work started, or is
            budget-blocked.
          </p>
        ) : (
          <div className="space-y-3">
            {postmortems.map((pm) => (
              <PostmortemRow key={pm.task_id} postmortem={pm} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
